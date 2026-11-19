package com.minierp.hr;

import com.minierp.org.OrgService;
import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

@Tag(name = "hr", description = "人力资源：考勤、计件、薪资、HR 看板")
@RestController
@RequestMapping("/api/hr")
@RequiredArgsConstructor
public class HrController {
    private static final String HR = "hasAnyRole('ADMIN','HR')";
    private final PayrollService payroll;
    private final OrgService orgs;
    private final JdbcTemplate jdbc;

    public record PeriodReq(String period) {
    }

    public record DateReq(LocalDate date) {
    }

    public record PieceRateReq(String lineCode, BigDecimal rate, LocalDate effectiveFrom, String remark) {
    }

    @Operation(summary = "核算月度工资（草稿可重算）")
    @PostMapping("/payrolls/calculate")
    @PreAuthorize(HR)
    @Audited("hr.calculatePayroll")
    public Map<String, Object> calculate(@RequestBody PeriodReq r) {
        return payroll.payroll(payroll.calculate(YearMonth.parse(r.period()), LocalDate.now()));
    }

    @PostMapping("/payrolls/{id}/submit")
    @PreAuthorize(HR)
    @Audited("hr.submitPayroll")
    public Map<String, Object> submit(@PathVariable long id, Authentication auth) {
        payroll.submit(id, orgs.employeeOfUser(auth.getName()), LocalDateTime.now());
        return payroll.payroll(id);
    }

    @PostMapping("/payrolls/{id}/pay")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("hr.payPayroll")
    public Map<String, Object> pay(@PathVariable long id, @RequestBody(required = false) DateReq r) {
        payroll.pay(id, r == null || r.date() == null ? LocalDate.now() : r.date());
        return payroll.payroll(id);
    }

    @PostMapping("/piece-rates")
    @PreAuthorize("hasAnyRole('ADMIN','HR','PRODUCTION')")
    @Audited("hr.savePieceRate")
    public void saveRate(@RequestBody PieceRateReq r) {
        if (r.rate() == null || r.rate().signum() <= 0 || r.lineCode() == null || r.effectiveFrom() == null) {
            throw new com.minierp.common.BizException("产线、单价、生效日期必填，单价大于 0");
        }
        jdbc.update("INSERT INTO hr_piece_rate (line_code, rate, effective_from, remark) VALUES (?,?,?,?) ON DUPLICATE KEY UPDATE rate = VALUES(rate), remark = VALUES(remark)",
                r.lineCode(), r.rate(), Date.valueOf(r.effectiveFrom()), r.remark());
    }

    @Operation(summary = "我的工资条（员工自助）")
    @GetMapping("/my/payslips")
    public List<Map<String, Object>> myPayslips(Authentication auth) {
        return jdbc.queryForList("SELECT * FROM v_payslip WHERE employee_id = ? ORDER BY period DESC LIMIT 24", orgs.employeeOfUser(auth.getName()));
    }

    @Operation(summary = "HR 看板：人数、入离职、流失率、出勤率、加班、人工成本、单位人工成本")
    @GetMapping("/dashboard")
    @PreAuthorize("hasAnyRole('ADMIN','HR','MANAGEMENT','FINANCE')")
    public Map<String, Object> dashboard(@RequestParam(required = false) String month) {
        // 默认取最近一个已核算工资的月份（人工成本、产线人均来自工资表）
        String latest = jdbc.queryForObject("SELECT MAX(period) FROM hr_payroll", String.class);
        YearMonth ym = month != null ? YearMonth.parse(month)
                : latest != null ? YearMonth.parse(latest) : YearMonth.from(jdbc.queryForObject("SELECT MAX(work_date) FROM hr_attendance", LocalDate.class));
        Date from = Date.valueOf(ym.atDay(1)), to = Date.valueOf(ym.atEndOfMonth());
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("month", ym.toString());
        r.put("headcount", jdbc.queryForObject("SELECT COUNT(*) FROM hr_employee WHERE hire_date <= ? AND (leave_date IS NULL OR leave_date > ?)", Long.class, to, to));
        r.put("hires", jdbc.queryForObject("SELECT COUNT(*) FROM hr_employee WHERE hire_date BETWEEN ? AND ?", Long.class, from, to));
        r.put("leaves", jdbc.queryForObject("SELECT COUNT(*) FROM hr_employee WHERE leave_date BETWEEN ? AND ? AND status = 'LEFT'", Long.class, from, to));
        r.put("attendanceRate", jdbc.queryForObject("SELECT ROUND(SUM(status IN ('NORMAL','LATE')) / NULLIF(COUNT(*), 0), 4) FROM hr_attendance WHERE work_date BETWEEN ? AND ?",
                BigDecimal.class, from, to));
        r.put("byDept", jdbc.queryForList("""
                SELECT COALESCE(p.name, d.name) AS dept, COUNT(*) AS headcount FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id
                LEFT JOIN org_dept p ON p.id = d.parent_id AND p.parent_id IS NOT NULL
                WHERE e.hire_date <= ? AND (e.leave_date IS NULL OR e.leave_date > ?) GROUP BY COALESCE(p.name, d.name) ORDER BY headcount DESC""", to, to));
        // 近 12 个月趋势：月末人数、入职、离职、月流失率（离职 / 月初人数）
        r.put("trend", jdbc.queryForList("""
                SELECT m.month,
                  (SELECT COUNT(*) FROM hr_employee e WHERE e.hire_date <= LAST_DAY(m.d) AND (e.leave_date IS NULL OR e.leave_date > LAST_DAY(m.d))) AS headcount,
                  (SELECT COUNT(*) FROM hr_employee e WHERE e.hire_date BETWEEN m.d AND LAST_DAY(m.d)) AS hires,
                  (SELECT COUNT(*) FROM hr_employee e WHERE e.status = 'LEFT' AND e.leave_date BETWEEN m.d AND LAST_DAY(m.d)) AS leaves,
                  (SELECT COUNT(*) FROM hr_employee e WHERE e.status = 'LEFT' AND e.leave_date BETWEEN m.d AND LAST_DAY(m.d) AND e.wage_type = 'PIECE') AS worker_leaves,
                  ROUND((SELECT COUNT(*) FROM hr_employee e WHERE e.status = 'LEFT' AND e.leave_date BETWEEN m.d AND LAST_DAY(m.d))
                    / NULLIF((SELECT COUNT(*) FROM hr_employee e WHERE e.hire_date < m.d AND (e.leave_date IS NULL OR e.leave_date >= m.d)), 0), 4) AS turnover
                FROM (SELECT DISTINCT DATE_FORMAT(work_date, '%Y-%m') AS month, DATE_FORMAT(work_date, '%Y-%m-01') AS d FROM hr_attendance
                      WHERE work_date > DATE_SUB(?, INTERVAL 12 MONTH) AND work_date <= ?) m ORDER BY m.month""", to, to));
        // 人工成本（来自工资表）：按成本类型、按月
        r.put("laborCost", jdbc.queryForList("""
                SELECT p.period, s.cost_type, ROUND(SUM(s.gross_pay + s.social_employer), 2) AS cost, ROUND(AVG(s.gross_pay), 2) AS avg_gross
                FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id WHERE p.period > ? GROUP BY p.period, s.cost_type ORDER BY p.period""",
                ym.minusMonths(12).toString()));
        // 各产线：人数、加班、计件、单位人工成本（该产线计件员工人工成本 / 当月良品数）
        r.put("lines", jdbc.queryForList("""
                SELECT e.line_code, COUNT(DISTINCT e.id) AS workers, ROUND(SUM(s.gross_pay + s.social_employer), 2) AS labor_cost,
                       ROUND(AVG(s.gross_pay), 2) AS avg_gross, ROUND(SUM(s.overtime_hours) / COUNT(DISTINCT e.id), 1) AS ot_per_worker,
                       (SELECT SUM(r.good_qty) FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id
                         WHERE w.line_code = e.line_code AND r.report_date BETWEEN ? AND ?) AS good_qty
                FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id JOIN hr_employee e ON e.id = s.employee_id
                WHERE p.period = ? AND e.line_code IS NOT NULL GROUP BY e.line_code ORDER BY e.line_code""", from, to, ym.toString()));
        return r;
    }
}
