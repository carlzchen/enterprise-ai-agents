package com.minierp.ehs;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.workflow.WorkflowFinishedEvent;
import com.minierp.workflow.WorkflowService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.*;

/**
 * 安全环保：
 * 1. 隐患排查治理：
 *    - 来源有周检、员工随手拍、事故整改；
 *    - 一般隐患 7 天、重大隐患 30 天内整改，安环部复查后关闭。
 * 2. 事故 / 未遂事件（海因里希金字塔）：可记录事故和损工事故必须完成根因分析与整改；
 *    安全绩效按考勤工时计算 LTIFR、TRIR、严重率。
 * 3. 危险作业许可：
 *    - 走 OA 流程 EHS_PERMIT：部门负责人 → 安环部 → 受限空间或一级动火由总经理审批；
 *    - 作业结束后关闭作业票。
 * 4. 危险废物台账：产生、转移（联单），按先进先出监控贮存期限（不超过一年）。
 */
@Service
@RequiredArgsConstructor
public class EhsService {
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final WorkflowService workflow;

    public record HazardReq(String source, long deptId, String location, String category, String description, String level, LocalDate foundDate,
                            Long foundBy, long ownerEmpId) {
    }

    public record IncidentReq(LocalDateTime occurredAt, long deptId, String location, String type, Long employeeId, String description, String injury, int lostDays) {
    }

    public record PermitReq(String type, String level, long deptId, String location, String workDesc, Long guardianEmpId,
                            LocalDateTime plannedStart, LocalDateTime plannedEnd) {
    }

    // ================= 隐患 =================

    /** {部门 id, 部门负责人员工 id} */
    public long[] deptAndHead(String deptCode) {
        Map<String, Object> d = jdbc.queryForList("""
                SELECT id, COALESCE(manager_emp_id, (SELECT manager_emp_id FROM org_dept WHERE code = 'GM')) AS head FROM org_dept WHERE code = ?""", deptCode)
                .stream().findFirst().orElseThrow(() -> new NotFoundException("部门", deptCode));
        return new long[]{((Number) d.get("id")).longValue(), ((Number) d.get("head")).longValue()};
    }

    @Transactional
    public long reportHazard(HazardReq r) {
        String level = "MAJOR".equals(r.level()) ? "MAJOR" : "GENERAL";
        String no = docNo.next("HZ", r.foundDate());
        jdbc.update("""
                INSERT INTO ehs_hazard (hazard_no, source, dept_id, location, category, description, level, found_date, found_by, owner_emp_id, deadline, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,'OPEN')""", no, r.source(), r.deptId(), r.location(), r.category(), r.description(), level,
                Date.valueOf(r.foundDate()), r.foundBy(), r.ownerEmpId(), Date.valueOf(EhsRules.hazardDeadline(level, r.foundDate())));
        return jdbc.queryForObject("SELECT id FROM ehs_hazard WHERE hazard_no = ?", Long.class, no);
    }

    @Transactional
    public void rectify(long id, String action, LocalDate date) {
        if (jdbc.update("UPDATE ehs_hazard SET status = 'RECTIFIED', rectified_date = ?, action = ? WHERE id = ? AND status = 'OPEN'", Date.valueOf(date), action, id) == 0) {
            throw new BizException("只有待整改的隐患可以提交整改");
        }
    }

    @Transactional
    public void verify(long id, boolean pass, LocalDate date) {
        int n = pass ? jdbc.update("UPDATE ehs_hazard SET status = 'CLOSED', verified_date = ? WHERE id = ? AND status = 'RECTIFIED'", Date.valueOf(date), id)
                : jdbc.update("UPDATE ehs_hazard SET status = 'OPEN', rectified_date = NULL, action = CONCAT(COALESCE(action, ''), '（复查未通过）') WHERE id = ? AND status = 'RECTIFIED'", id);
        if (n == 0) {
            throw new BizException("只有已整改的隐患可以复查");
        }
    }

    // ================= 事故 =================

    @Transactional
    public long reportIncident(IncidentReq r) {
        if (!List.of("NEAR_MISS", "FIRST_AID", "RECORDABLE", "LOST_TIME").contains(r.type())) {
            throw new BizException("事件类型无效");
        }
        String no = docNo.next("AC", r.occurredAt().toLocalDate());
        jdbc.update("""
                INSERT INTO ehs_incident (incident_no, occurred_at, dept_id, location, type, employee_id, description, injury, lost_days, status)
                VALUES (?,?,?,?,?,?,?,?,?,'OPEN')""", no, Timestamp.valueOf(r.occurredAt()), r.deptId(), r.location(), r.type(), r.employeeId(),
                r.description(), r.injury(), "LOST_TIME".equals(r.type()) ? r.lostDays() : 0);
        return jdbc.queryForObject("SELECT id FROM ehs_incident WHERE incident_no = ?", Long.class, no);
    }

    @Transactional
    public void closeIncident(long id, String rootCause, String action, LocalDate date) {
        Map<String, Object> i = jdbc.queryForList("SELECT type, status FROM ehs_incident WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("事故", id));
        if (!"OPEN".equals(i.get("status"))) {
            throw new BizException("事故已关闭");
        }
        if (List.of("RECORDABLE", "LOST_TIME").contains(i.get("type")) && (rootCause == null || rootCause.isBlank() || action == null || action.isBlank())) {
            throw new BizException("可记录 / 损工事故必须填写根因和整改措施");
        }
        jdbc.update("UPDATE ehs_incident SET status = 'CLOSED', root_cause = ?, corrective_action = ?, closed_date = ? WHERE id = ?", rootCause, action, Date.valueOf(date), id);
    }

    // ================= 作业票 =================

    @Transactional
    public long applyPermit(PermitReq r, long applicantEmpId, LocalDateTime now) {
        if (!List.of("HOT_WORK", "HEIGHT", "CONFINED", "ELECTRICAL").contains(r.type())) {
            throw new BizException("作业类型无效");
        }
        if (!r.plannedEnd().isAfter(r.plannedStart())) {
            throw new BizException("作业结束时间必须晚于开始时间");
        }
        String level = "1".equals(r.level()) ? "1" : "2";
        String no = docNo.next("PT", now.toLocalDate());
        jdbc.update("""
                INSERT INTO ehs_permit (permit_no, type, level, dept_id, location, work_desc, applicant_emp_id, guardian_emp_id, planned_start, planned_end, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,'APPROVING')""", no, r.type(), level, r.deptId(), r.location(), r.workDesc(), applicantEmpId, r.guardianEmpId(),
                Timestamp.valueOf(r.plannedStart()), Timestamp.valueOf(r.plannedEnd()));
        long id = jdbc.queryForObject("SELECT id FROM ehs_permit WHERE permit_no = ?", Long.class, no);
        long wf = workflow.start(new WorkflowService.StartReq("EHS_PERMIT", "作业票：" + label(r.type()) + " " + r.location() + "（" + r.workDesc() + "）",
                Map.of("permitType", r.type(), "level", level, "permitNo", no, "location", r.location()), "EHS_PERMIT", id, null, null), applicantEmpId, now);
        jdbc.update("UPDATE ehs_permit SET wf_instance_id = ? WHERE id = ?", wf, id);
        return id;
    }

    @EventListener
    public void onFinished(WorkflowFinishedEvent ev) {
        if (!"EHS_PERMIT".equals(ev.defCode()) || ev.bizId() == null) {
            return;
        }
        jdbc.update("UPDATE ehs_permit SET status = ? WHERE id = ?", "APPROVED".equals(ev.status()) ? "APPROVED" : "REJECTED", ev.bizId());
    }

    @Transactional
    public void closePermit(long id, LocalDateTime at) {
        if (jdbc.update("UPDATE ehs_permit SET status = 'CLOSED', closed_at = ? WHERE id = ? AND status = 'APPROVED'", Timestamp.valueOf(at), id) == 0) {
            throw new BizException("只有已批准的作业票可以关闭");
        }
    }

    private static String label(String type) {
        return switch (type) {
            case "HOT_WORK" -> "动火作业";
            case "HEIGHT" -> "高处作业";
            case "CONFINED" -> "受限空间作业";
            default -> "临时用电";
        };
    }

    // ================= 危废 =================

    @Transactional
    public void generateWaste(String code, String name, BigDecimal qtyTon, Long deptId, LocalDate date) {
        jdbc.update("INSERT INTO ehs_waste (waste_code, waste_name, move_type, move_date, qty_ton, dept_id) VALUES (?,?,'GENERATE',?,?,?)",
                code, name, Date.valueOf(date), qtyTon, deptId);
    }

    /** 转移处置：整类清库，生成联单号，返回转移量 */
    @Transactional
    public BigDecimal transferWaste(String code, String receiver, BigDecimal pricePerTon, LocalDate date) {
        EhsRules.WasteStock s = wasteStock(code, date);
        if (s.qty().signum() <= 0) {
            return BigDecimal.ZERO;
        }
        String name = jdbc.queryForObject("SELECT waste_name FROM ehs_waste WHERE waste_code = ? LIMIT 1", String.class, code);
        jdbc.update("""
                INSERT INTO ehs_waste (waste_code, waste_name, move_type, move_date, qty_ton, manifest_no, receiver, cost) VALUES (?,?,'TRANSFER',?,?,?,?,?)""",
                code, name, Date.valueOf(date), s.qty().negate(), docNo.next("HW", date), receiver, s.qty().multiply(pricePerTon).setScale(2, java.math.RoundingMode.HALF_UP));
        return s.qty();
    }

    public EhsRules.WasteStock wasteStock(String code, LocalDate asOf) {
        List<EhsRules.WasteMove> moves = jdbc.query("SELECT move_date, qty_ton FROM ehs_waste WHERE waste_code = ? AND move_date <= ? ORDER BY move_date, id",
                (rs, i) -> new EhsRules.WasteMove(rs.getDate(1).toLocalDate(), rs.getBigDecimal(2)), code, Date.valueOf(asOf));
        return EhsRules.wasteStock(moves, asOf);
    }

    public List<Map<String, Object>> wasteSummary(LocalDate asOf) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> w : jdbc.queryForList("SELECT waste_code, MAX(waste_name) AS waste_name FROM ehs_waste GROUP BY waste_code ORDER BY waste_code")) {
            EhsRules.WasteStock s = wasteStock((String) w.get("waste_code"), asOf);
            Map<String, Object> m = new LinkedHashMap<>(w);
            m.put("stockTon", s.qty());
            m.put("oldest", s.oldest());
            m.put("days", s.days());
            m.put("status", s.status());
            m.put("lastTransfer", jdbc.queryForObject("SELECT MAX(move_date) FROM ehs_waste WHERE waste_code = ? AND move_type = 'TRANSFER' AND move_date <= ?",
                    LocalDate.class, w.get("waste_code"), Date.valueOf(asOf)));
            out.add(m);
        }
        return out;
    }

    // ================= 看板 =================

    public EhsRules.SafetyKpi safety(LocalDate from, LocalDate to) {
        Double hours = jdbc.queryForObject("SELECT COALESCE(SUM(work_hours + overtime_hours), 0) FROM hr_attendance WHERE work_date BETWEEN ? AND ?",
                Double.class, Date.valueOf(from), Date.valueOf(to));
        Map<String, Object> c = jdbc.queryForMap("""
                SELECT COALESCE(SUM(type = 'LOST_TIME'), 0) AS lti, COALESCE(SUM(type = 'RECORDABLE'), 0) AS rec, COALESCE(SUM(lost_days), 0) AS days
                FROM ehs_incident WHERE occurred_at >= ? AND occurred_at < ?""", Date.valueOf(from), Date.valueOf(to.plusDays(1)));
        return EhsRules.safety(hours == null ? 0 : hours, ((Number) c.get("lti")).intValue(), ((Number) c.get("rec")).intValue(), ((Number) c.get("days")).intValue());
    }

    public Map<String, Object> dashboard(LocalDate from, LocalDate to) {
        Date f = Date.valueOf(from), t = Date.valueOf(to.plusDays(1));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("safety", safety(from, to));
        LocalDate lastLti = jdbc.queryForObject("SELECT DATE(MAX(occurred_at)) FROM ehs_incident WHERE type = 'LOST_TIME' AND occurred_at < ?", LocalDate.class, t);
        out.put("lastLostTime", lastLti);
        out.put("daysWithoutLti", lastLti == null ? null : ChronoUnit.DAYS.between(lastLti, to));
        out.put("pyramid", jdbc.queryForList("""
                SELECT type, COUNT(*) AS n, SUM(lost_days) AS lost_days FROM ehs_incident WHERE occurred_at >= ? AND occurred_at < ? GROUP BY type""", f, t));
        out.put("hazards", jdbc.queryForMap("""
                SELECT COUNT(*) AS found, SUM(level = 'MAJOR') AS major, SUM(status = 'CLOSED') AS closed, SUM(status <> 'CLOSED') AS open_now,
                       SUM(status = 'OPEN' AND deadline < ?) AS overdue_open,
                       ROUND(SUM(rectified_date IS NOT NULL AND rectified_date <= deadline) / NULLIF(SUM(rectified_date IS NOT NULL), 0), 4) AS on_time_rate,
                       ROUND(AVG(DATEDIFF(rectified_date, found_date)), 1) AS avg_days
                FROM ehs_hazard WHERE found_date >= ? AND found_date < ?""", Date.valueOf(to), f, t));
        out.put("hazardsByCategory", jdbc.queryForList("""
                SELECT category, COUNT(*) AS n, SUM(level = 'MAJOR') AS major FROM ehs_hazard WHERE found_date >= ? AND found_date < ? GROUP BY category ORDER BY n DESC""", f, t));
        out.put("hazardsByDept", jdbc.queryForList("""
                SELECT d.name AS dept, COUNT(*) AS n, SUM(h.rectified_date > h.deadline OR (h.rectified_date IS NULL AND h.deadline < ?)) AS overdue
                FROM ehs_hazard h JOIN org_dept d ON d.id = h.dept_id WHERE h.found_date >= ? AND h.found_date < ? GROUP BY d.name ORDER BY n DESC""", Date.valueOf(to), f, t));
        out.put("monthly", jdbc.queryForList("""
                SELECT m.period, m.hazards, m.major, COALESCE(i.near_miss, 0) AS near_miss, COALESCE(i.injuries, 0) AS injuries, COALESCE(p.permits, 0) AS permits FROM (
                  SELECT DATE_FORMAT(found_date, '%Y-%m') AS period, COUNT(*) AS hazards, SUM(level = 'MAJOR') AS major FROM ehs_hazard
                  WHERE found_date >= ? AND found_date < ? GROUP BY period) m
                LEFT JOIN (SELECT DATE_FORMAT(occurred_at, '%Y-%m') AS period, SUM(type = 'NEAR_MISS') AS near_miss, SUM(type <> 'NEAR_MISS') AS injuries
                           FROM ehs_incident GROUP BY period) i ON i.period = m.period
                LEFT JOIN (SELECT DATE_FORMAT(planned_start, '%Y-%m') AS period, COUNT(*) AS permits FROM ehs_permit GROUP BY period) p ON p.period = m.period
                ORDER BY m.period""", f, t));
        out.put("permits", jdbc.queryForList("""
                SELECT type, COUNT(*) AS n, SUM(status = 'REJECTED') AS rejected, SUM(status = 'CLOSED') AS closed FROM ehs_permit
                WHERE planned_start >= ? AND planned_start < ? GROUP BY type""", f, t));
        out.put("seriousIncidents", jdbc.queryForList("""
                SELECT incident_no, occurred_date, dept_name, type, description, injury, lost_days, root_cause, corrective_action, status FROM v_ehs_incident
                WHERE type IN ('RECORDABLE','LOST_TIME') AND occurred_at >= ? AND occurred_at < ? ORDER BY occurred_at DESC""", f, t));
        out.put("waste", wasteSummary(to));
        return out;
    }
}
