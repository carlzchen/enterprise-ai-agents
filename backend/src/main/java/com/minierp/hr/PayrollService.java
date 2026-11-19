package com.minierp.hr;

import com.minierp.common.BizException;
import com.minierp.common.NotFoundException;
import com.minierp.common.WorkCalendar;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.workflow.WorkflowFinishedEvent;
import com.minierp.workflow.WorkflowService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 月度工资：核算（考勤 + 计件 + 个税累计预扣）→ 工资发放审批（OA）→ 审批通过计提入账 → 发放。
 * 计件：班组当月良品数 × 报工日有效的产线计件单价 = 班组计件总额，按组员出勤工时（含加班）分摊。
 * 计提：按部门成本类型借记 制造费用 / 销售费用 / 管理费用 / 研发费用（应发 + 单位社保公积金），贷记应付职工薪酬。
 * 发放：借应付职工薪酬，贷银行存款（实发 + 社保公积金个人与单位部分）与应交个税；随后缴纳个税。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class PayrollService {
    private final JdbcTemplate jdbc;
    private final WorkflowService workflow;
    private final GlService gl;

    @Transactional
    public long calculate(YearMonth ym, LocalDate calcDate) {
        String period = ym.toString();
        List<Map<String, Object>> existing = jdbc.queryForList("SELECT id, status FROM hr_payroll WHERE period = ?", period);
        if (!existing.isEmpty()) {
            if (!"DRAFT".equals(existing.get(0).get("status"))) {
                throw new BizException(period + " 工资已提交审批或已入账，不能重算");
            }
            jdbc.update("DELETE FROM hr_payslip WHERE payroll_id = ?", existing.get(0).get("id"));
            jdbc.update("DELETE FROM hr_payroll WHERE id = ?", existing.get(0).get("id"));
        }
        LocalDate from = ym.atDay(1), to = ym.atEndOfMonth();
        int monthWorkdays = WorkCalendar.workdays(ym);

        // 1. 计件：班组 × 报工日单价
        Map<String, BigDecimal> teamPiece = new HashMap<>();
        jdbc.query("""
                SELECT SUBSTRING_INDEX(r.operator, '-班组', 1) AS line, SUBSTRING_INDEX(r.operator, '-班组', -1) AS team,
                       SUM(r.good_qty * (SELECT pr.rate FROM hr_piece_rate pr WHERE pr.line_code = SUBSTRING_INDEX(r.operator, '-班组', 1)
                                          AND pr.effective_from <= r.report_date ORDER BY pr.effective_from DESC LIMIT 1)) AS amount
                FROM mes_wo_report r WHERE r.report_date BETWEEN ? AND ? AND r.operator LIKE '%-班组%' GROUP BY line, team""", rs -> {
            if (rs.getBigDecimal(3) != null) {
                teamPiece.put(rs.getString(1) + "-" + rs.getString(2), rs.getBigDecimal(3));
            }
        }, Date.valueOf(from), Date.valueOf(to));

        // 2. 考勤汇总
        Map<Long, Map<String, Object>> att = new HashMap<>();
        jdbc.queryForList("""
                SELECT employee_id, SUM(status IN ('NORMAL','LATE')) AS attend, SUM(status = 'LEAVE' AND leave_type IN ('ANNUAL','SICK')) AS paid_leave,
                       SUM(status = 'LEAVE' AND leave_type = 'PERSONAL') + SUM(status = 'ABSENT') AS unpaid, SUM(status = 'LEAVE') AS leave_days,
                       SUM(status = 'ABSENT') AS absent, SUM(work_hours) + SUM(overtime_hours) AS hours, SUM(overtime_hours) AS ot
                FROM hr_attendance WHERE work_date BETWEEN ? AND ? GROUP BY employee_id""", Date.valueOf(from), Date.valueOf(to))
                .forEach(r -> att.put(((Number) r.get("employee_id")).longValue(), r));

        List<Map<String, Object>> emps = jdbc.queryForList("""
                SELECT e.*, d.cost_type FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id
                WHERE e.hire_date <= ? AND (e.leave_date IS NULL OR e.leave_date >= ?) ORDER BY e.id""", Date.valueOf(to), Date.valueOf(from));

        // 3. 班组内按工时分摊计件
        Map<String, Map<Long, BigDecimal>> teamHours = new HashMap<>();
        for (Map<String, Object> e : emps) {
            if ("PIECE".equals(e.get("wage_type")) && e.get("line_code") != null) {
                long id = ((Number) e.get("id")).longValue();
                BigDecimal h = att.containsKey(id) ? num(att.get(id).get("hours")) : BigDecimal.ZERO;
                teamHours.computeIfAbsent(e.get("line_code") + "-" + e.get("team"), k -> new HashMap<>()).put(id, h);
            }
        }
        Map<Long, BigDecimal> piece = new HashMap<>();
        teamHours.forEach((team, hours) -> piece.putAll(PieceWageAllocator.allocate(teamPiece.getOrDefault(team, BigDecimal.ZERO), hours)));

        // 4. 本年累计（个税累计预扣）
        Map<Long, BigDecimal[]> ytd = new HashMap<>();
        if (ym.getMonthValue() > 1) {
            jdbc.query("""
                    SELECT s.employee_id, s.ytd_taxable, s.ytd_tax FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id
                    WHERE p.period = (SELECT MAX(p2.period) FROM hr_payroll p2 JOIN hr_payslip s2 ON s2.payroll_id = p2.id
                                      WHERE s2.employee_id = s.employee_id AND p2.period >= ? AND p2.period < ?)""", rs -> {
                ytd.put(rs.getLong(1), new BigDecimal[]{rs.getBigDecimal(2), rs.getBigDecimal(3)});
            }, ym.getYear() + "-01", period);
        }

        jdbc.update("""
                INSERT INTO hr_payroll (period, status, headcount, gross_total, net_total, tax_total, social_employee, social_employer, employer_cost, calc_date)
                VALUES (?, 'DRAFT', 0, 0, 0, 0, 0, 0, 0, ?)""", period, Date.valueOf(calcDate));
        long payrollId = jdbc.queryForObject("SELECT id FROM hr_payroll WHERE period = ?", Long.class, period);
        List<Object[]> rows = new ArrayList<>();
        for (Map<String, Object> e : emps) {
            long id = ((Number) e.get("id")).longValue();
            LocalDate hire = ((java.sql.Date) e.get("hire_date")).toLocalDate();
            LocalDate leave = e.get("leave_date") == null ? null : ((java.sql.Date) e.get("leave_date")).toLocalDate();
            LocalDate s = hire.isAfter(from) ? hire : from;
            LocalDate t = leave != null && leave.isBefore(to) ? leave : to;
            int scheduled = WorkCalendar.workdays(s, t);
            Map<String, Object> a = att.getOrDefault(id, Map.of());
            BigDecimal[] prev = ytd.getOrDefault(id, new BigDecimal[]{BigDecimal.ZERO, BigDecimal.ZERO});
            PayrollCalculator.Result r = PayrollCalculator.compute(new PayrollCalculator.Input((String) e.get("wage_type"),
                    num(e.get("base_salary")), num(e.get("social_base")), monthWorkdays, scheduled, num(a.get("attend")), num(a.get("paid_leave")),
                    num(a.get("unpaid")), num(a.get("ot")), piece.getOrDefault(id, BigDecimal.ZERO), prev[0], prev[1]));
            rows.add(new Object[]{payrollId, id, e.get("dept_id"), e.get("cost_type"), e.get("wage_type"), scheduled, num(a.get("attend")),
                    num(a.get("leave_days")), num(a.get("absent")), num(a.get("ot")), r.basePay(), BigDecimal.ZERO, r.piecePay(), r.topup(),
                    r.overtimePay(), r.deduction(), r.gross(), r.socialEmployee(), r.tax(), r.net(), r.socialEmployer(), r.ytdTaxable(), r.ytdTax()});
        }
        jdbc.batchUpdate("""
                INSERT INTO hr_payslip (payroll_id, employee_id, dept_id, cost_type, wage_type, scheduled_days, attend_days, leave_days, absent_days,
                  overtime_hours, base_pay, piece_qty, piece_pay, guarantee_topup, overtime_pay, deduction, gross_pay, social_employee, income_tax,
                  net_pay, social_employer, ytd_taxable, ytd_tax) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows);
        jdbc.update("""
                UPDATE hr_payroll p JOIN (SELECT payroll_id, COUNT(*) n, SUM(gross_pay) g, SUM(net_pay) net, SUM(income_tax) tax,
                  SUM(social_employee) se, SUM(social_employer) sr FROM hr_payslip WHERE payroll_id = ? GROUP BY payroll_id) x ON x.payroll_id = p.id
                SET p.headcount = x.n, p.gross_total = x.g, p.net_total = x.net, p.tax_total = x.tax, p.social_employee = x.se,
                    p.social_employer = x.sr, p.employer_cost = x.g + x.sr WHERE p.id = ?""", payrollId, payrollId);
        return payrollId;
    }

    /** 提交工资发放审批（人力负责人 → 财务负责人 → 总经理） */
    @Transactional
    public long submit(long payrollId, long applicantEmpId, LocalDateTime now) {
        Map<String, Object> p = payroll(payrollId);
        if (!"DRAFT".equals(p.get("status"))) {
            throw new BizException("只有草稿状态的工资表可以提交");
        }
        long wf = workflow.start(new WorkflowService.StartReq("PAYROLL", p.get("period") + " 工资发放（" + p.get("headcount") + " 人，应发 "
                + ((BigDecimal) p.get("gross_total")).toPlainString() + " 元）", Map.of("period", p.get("period")), "PAYROLL", payrollId,
                (BigDecimal) p.get("employer_cost"), null), applicantEmpId, now);
        jdbc.update("UPDATE hr_payroll SET status = 'PENDING', wf_instance_id = ? WHERE id = ? AND status = 'DRAFT'", wf, payrollId);
        return wf;
    }

    @EventListener
    public void onFinished(WorkflowFinishedEvent ev) {
        if (!"PAYROLL".equals(ev.defCode()) || ev.bizId() == null) {
            return;
        }
        if (!"APPROVED".equals(ev.status())) {
            jdbc.update("UPDATE hr_payroll SET status = 'DRAFT', wf_instance_id = NULL WHERE id = ?", ev.bizId());
            return;
        }
        accrue(ev.bizId(), ev.finishedAt().toLocalDate());
    }

    /** 计提：借各成本费用科目，贷应付职工薪酬（记账日取审批日与期末较早者，保证计入工资所属月份） */
    private void accrue(long payrollId, LocalDate approvedOn) {
        Map<String, Object> p = payroll(payrollId);
        YearMonth ym = YearMonth.parse((String) p.get("period"));
        LocalDate postDate = approvedOn.isAfter(ym.atEndOfMonth()) ? ym.atEndOfMonth() : approvedOn;
        List<Line> lines = new ArrayList<>();
        BigDecimal total = BigDecimal.ZERO;
        for (Map<String, Object> r : jdbc.queryForList("""
                SELECT cost_type, SUM(gross_pay + social_employer) AS cost FROM hr_payslip WHERE payroll_id = ? GROUP BY cost_type ORDER BY cost_type""", payrollId)) {
            BigDecimal cost = (BigDecimal) r.get("cost");
            lines.add(Line.dr(GlService.laborAccount((String) r.get("cost_type")), cost, "计提 " + ym + " 工资及单位社保公积金"));
            total = total.add(cost);
        }
        lines.add(Line.cr(GlService.PAYROLL_PAYABLE, total, "应付职工薪酬"));
        gl.post(postDate, "PAYROLL", "PR-" + ym, ym + " 工资计提", lines, null);
        jdbc.update("UPDATE hr_payroll SET status = 'APPROVED' WHERE id = ?", payrollId);
        log.info("{} 工资已计提入账：{} 元", ym, total);
    }

    /** 发放：实发工资与社保公积金从银行付出，个税代扣后缴纳 */
    @Transactional
    public void pay(long payrollId, LocalDate date) {
        Map<String, Object> p = payroll(payrollId);
        if (!"APPROVED".equals(p.get("status"))) {
            throw new BizException("工资表未审批入账，不能发放");
        }
        BigDecimal employerCost = (BigDecimal) p.get("employer_cost");
        BigDecimal tax = (BigDecimal) p.get("tax_total");
        BigDecimal social = ((BigDecimal) p.get("social_employee")).add((BigDecimal) p.get("social_employer"));
        BigDecimal bank = employerCost.subtract(tax);
        String no = "PR-" + p.get("period");
        gl.post(date, "PAYROLL", no, p.get("period") + " 工资发放及社保公积金缴纳", List.of(
                Line.dr(GlService.PAYROLL_PAYABLE, employerCost, "发放工资"),
                Line.cr(GlService.BANK, bank.subtract(social), "实发工资"),
                Line.cr(GlService.BANK, social, "缴纳社保公积金（个人 + 单位）"),
                Line.cr(GlService.IIT_PAYABLE, tax, "代扣个人所得税")), null);
        gl.post(date, "PAYROLL", no, p.get("period") + " 缴纳代扣个税", List.of(
                Line.dr(GlService.IIT_PAYABLE, tax, "缴纳个税"), Line.cr(GlService.BANK, tax, "银行付款")), null);
        jdbc.update("UPDATE hr_payroll SET status = 'PAID', paid_date = ? WHERE id = ?", Date.valueOf(date), payrollId);
    }

    public Map<String, Object> payroll(long id) {
        return jdbc.queryForList("SELECT * FROM v_payroll WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("工资表", id));
    }

    private static BigDecimal num(Object v) {
        return v == null ? BigDecimal.ZERO : new BigDecimal(v.toString());
    }
}
