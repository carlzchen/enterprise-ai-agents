package com.minierp.itsm;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.common.SlaClock;
import com.minierp.workflow.WorkflowFinishedEvent;
import com.minierp.workflow.WorkflowService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Date;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

/**
 * IT 服务台（ITIL 简化版）：
 * 1. 故障与服务请求：
 *    - 影响范围 × 紧急程度确定优先级；
 *    - P1 按 7×24 计时，其余按工作时间计时，据此判断是否违反 SLA。
 * 2. 变更：
 *    - 走 OA 流程 IT_CHANGE（信息部负责人 → 高风险变更需总经理审批）；
 *    - 审批通过后实施、关闭，可以关联引发变更的故障。
 * 3. 入离职联动：
 *    - 入职：开通账号，从库存领用笔记本，分配全员许可和部门许可（席位不足时记在工单里，等待采购）；
 *    - 离职：回收电脑（满 4 年报废），释放许可。
 * 4. CMDB：配置项与员工、部门、固定资产卡片关联。
 */
@Service
@RequiredArgsConstructor
public class ItsmService {
    private static final Set<String> TYPES = Set.of("INCIDENT", "REQUEST");
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final WorkflowService workflow;

    public record TicketReq(String type, String category, String title, String description, Long requesterEmpId, Long ciId, String impact, String urgency) {
    }

    public record ChangeReq(String title, String description, String risk, LocalDateTime plannedStart, Long relatedId, Long ciId) {
    }

    @Transactional
    public long createTicket(TicketReq r, LocalDateTime now) {
        if (!TYPES.contains(r.type())) {
            throw new BizException("工单类型为 INCIDENT / REQUEST，变更请走变更申请");
        }
        String priority = PriorityMatrix.priority(r.impact(), r.urgency());
        PriorityMatrix.Sla sla = PriorityMatrix.sla(priority);
        String no = docNo.next("IT", now.toLocalDate());
        jdbc.update("""
                INSERT INTO it_ticket (ticket_no, type, category, title, description, requester_emp_id, ci_id, impact, urgency, priority, status,
                  created_at, response_due, resolve_due) VALUES (?,?,?,?,?,?,?,?,?,?,'NEW',?,?,?)""",
                no, r.type(), r.category(), r.title(), r.description(), r.requesterEmpId(), r.ciId(), r.impact(), r.urgency(), priority, Timestamp.valueOf(now),
                Timestamp.valueOf(SlaClock.due(now, sla.responseHours(), sla.businessHours())), Timestamp.valueOf(SlaClock.due(now, sla.resolveHours(), sla.businessHours())));
        return jdbc.queryForObject("SELECT id FROM it_ticket WHERE ticket_no = ?", Long.class, no);
    }

    @Transactional
    public void assign(long id, long assigneeEmpId, LocalDateTime at) {
        if (jdbc.update("UPDATE it_ticket SET assignee_emp_id = ?, status = 'IN_PROGRESS', responded_at = COALESCE(responded_at, ?) WHERE id = ? AND status = 'NEW'",
                assigneeEmpId, Timestamp.valueOf(at), id) == 0) {
            throw new BizException("只有新建工单可以受理");
        }
    }

    @Transactional
    public void resolve(long id, String resolution, String rootCause, LocalDateTime at) {
        if (jdbc.update("UPDATE it_ticket SET status = 'RESOLVED', resolution = ?, root_cause = ?, resolved_at = ? WHERE id = ? AND status = 'IN_PROGRESS'",
                resolution, rootCause, Timestamp.valueOf(at), id) == 0) {
            throw new BizException("只有处理中的工单可以解决");
        }
        jdbc.update("UPDATE it_ticket SET sla_breached = (responded_at > response_due OR resolved_at > resolve_due) WHERE id = ?", id);
    }

    @Transactional
    public void close(long id, Integer satisfaction, LocalDateTime at) {
        if (jdbc.update("UPDATE it_ticket SET status = 'CLOSED', closed_at = ?, satisfaction = ? WHERE id = ? AND status = 'RESOLVED'",
                Timestamp.valueOf(at), satisfaction, id) == 0) {
            throw new BizException("只有已解决的工单可以关闭");
        }
    }

    // ================= 变更 =================

    @Transactional
    public long submitChange(ChangeReq r, long requesterEmpId, LocalDateTime now) {
        if (!List.of("LOW", "MEDIUM", "HIGH").contains(r.risk())) {
            throw new BizException("变更风险为 LOW / MEDIUM / HIGH");
        }
        String no = docNo.next("IT", now.toLocalDate());
        String impact = "HIGH".equals(r.risk()) ? "HIGH" : "MEDIUM";
        jdbc.update("""
                INSERT INTO it_ticket (ticket_no, type, category, title, description, requester_emp_id, ci_id, impact, urgency, priority, status,
                  created_at, response_due, resolve_due, related_id, risk, planned_start) VALUES (?,'CHANGE','变更',?,?,?,?,?,'LOW',?,'APPROVING',?,?,?,?,?,?)""",
                no, r.title(), r.description(), requesterEmpId, r.ciId(), impact, PriorityMatrix.priority(impact, "LOW"), Timestamp.valueOf(now),
                Timestamp.valueOf(now), Timestamp.valueOf(r.plannedStart() == null ? now.plusDays(7) : r.plannedStart()), r.relatedId(), r.risk(),
                r.plannedStart() == null ? null : Timestamp.valueOf(r.plannedStart()));
        long id = jdbc.queryForObject("SELECT id FROM it_ticket WHERE ticket_no = ?", Long.class, no);
        long wf = workflow.start(new WorkflowService.StartReq("IT_CHANGE", "IT 变更：" + r.title(), Map.of("risk", r.risk(), "ticketNo", no,
                "plannedStart", r.plannedStart() == null ? "" : r.plannedStart().toString()), "IT_CHANGE", id, null, null), requesterEmpId, now);
        jdbc.update("UPDATE it_ticket SET wf_instance_id = ? WHERE id = ?", wf, id);
        return id;
    }

    @EventListener
    public void onFinished(WorkflowFinishedEvent ev) {
        if (!"IT_CHANGE".equals(ev.defCode()) || ev.bizId() == null) {
            return;
        }
        jdbc.update("UPDATE it_ticket SET status = ?, responded_at = ? WHERE id = ?", "APPROVED".equals(ev.status()) ? "APPROVED" : "REJECTED",
                Timestamp.valueOf(ev.finishedAt()), ev.bizId());
    }

    /** 变更实施完成：关闭变更单，关联的故障一并关闭 */
    @Transactional
    public void implementChange(long id, String result, LocalDateTime at) {
        if (jdbc.update("UPDATE it_ticket SET status = 'CLOSED', resolution = ?, resolved_at = ?, closed_at = ? WHERE id = ? AND type = 'CHANGE' AND status = 'APPROVED'",
                result, Timestamp.valueOf(at), Timestamp.valueOf(at), id) == 0) {
            throw new BizException("只有已批准的变更可以实施");
        }
        jdbc.update("""
                UPDATE it_ticket x JOIN it_ticket c ON c.related_id = x.id SET x.status = 'CLOSED', x.closed_at = ?
                WHERE c.id = ? AND x.status = 'RESOLVED'""", Timestamp.valueOf(at), id);
    }

    // ================= 入离职 =================

    /** 入职：开账号、领电脑、分配许可，返回服务请求工单 id（由 IT 工程师跟进处理） */
    @Transactional
    public long onboard(long empId, LocalDateTime at) {
        Map<String, Object> e = employee(empId);
        String deptCode = (String) e.get("dept_code");
        List<String> notes = new ArrayList<>();
        Long laptop = jdbc.queryForList("SELECT id FROM it_ci WHERE type = 'LAPTOP' AND status = 'STOCK' ORDER BY purchase_date DESC, id LIMIT 1", Long.class)
                .stream().findFirst().orElse(null);
        if (laptop != null) {
            jdbc.update("UPDATE it_ci SET status = 'IN_USE', owner_emp_id = ?, dept_id = ?, location = ? WHERE id = ?", empId, e.get("dept_id"), e.get("dept_name"), laptop);
            notes.add("领用笔记本 " + jdbc.queryForObject("SELECT ci_no FROM it_ci WHERE id = ?", String.class, laptop));
        } else {
            notes.add("笔记本库存不足，待采购");
        }
        for (Map<String, Object> l : jdbc.queryForList("SELECT id, name FROM v_it_license WHERE scope = 'ALL' OR scope = ?", deptCode)) {
            long lid = ((Number) l.get("id")).longValue();
            if (assignLicense(lid, empId, at.toLocalDate())) {
                notes.add("分配 " + l.get("name"));
            } else {
                notes.add(l.get("name") + " 席位不足");
            }
        }
        long id = createTicket(new TicketReq("REQUEST", "入离职", "新员工入职：" + e.get("name") + "（" + e.get("dept_name") + "）", String.join("；", notes),
                empId, laptop, "LOW", "MEDIUM"), at);
        return id;
    }

    /** 离职：回收电脑（满 48 个月报废）、释放许可 */
    @Transactional
    public long offboard(long empId, LocalDateTime at) {
        Map<String, Object> e = employee(empId);
        List<String> notes = new ArrayList<>();
        for (Map<String, Object> ci : jdbc.queryForList("SELECT id, ci_no, purchase_date FROM it_ci WHERE owner_emp_id = ? AND status = 'IN_USE' AND type = 'LAPTOP'", empId)) {
            LocalDate bought = ((Date) ci.get("purchase_date")).toLocalDate();
            boolean retire = bought.plusMonths(48).isBefore(at.toLocalDate());
            jdbc.update("UPDATE it_ci SET status = ?, owner_emp_id = NULL, location = '信息部库房' WHERE id = ?", retire ? "RETIRED" : "STOCK", ci.get("id"));
            notes.add((retire ? "回收并报废 " : "回收 ") + ci.get("ci_no"));
        }
        int n = jdbc.update("UPDATE it_license_assign SET revoked_date = ? WHERE emp_id = ? AND revoked_date IS NULL", Date.valueOf(at.toLocalDate()), empId);
        notes.add("释放许可 " + n + " 个；停用域账号");
        return createTicket(new TicketReq("REQUEST", "入离职", "员工离职：" + e.get("name") + "（" + e.get("dept_name") + "）", String.join("；", notes),
                empId, null, "LOW", "MEDIUM"), at);
    }

    @Transactional
    public boolean assignLicense(long licenseId, long empId, LocalDate date) {
        Integer free = jdbc.queryForObject("SELECT available FROM v_it_license WHERE id = ?", Integer.class, licenseId);
        if (free == null || free <= 0) {
            return false;
        }
        jdbc.update("INSERT INTO it_license_assign (license_id, emp_id, assigned_date) VALUES (?,?,?)", licenseId, empId, Date.valueOf(date));
        return true;
    }

    /** 增购席位（采购审批后由 IT 调整），并给等待中的员工补分配 */
    @Transactional
    public int addSeats(long licenseId, int seats, LocalDate date) {
        jdbc.update("UPDATE it_license SET seats = seats + ? WHERE id = ?", seats, licenseId);
        String scope = jdbc.queryForObject("SELECT scope FROM it_license WHERE id = ?", String.class, licenseId);
        int assigned = 0;
        for (Long emp : waiting(licenseId, scope)) {
            if (!assignLicense(licenseId, emp, date)) {
                break;
            }
            assigned++;
        }
        return assigned;
    }

    /** 许可合规：适用范围内在职人数 vs 席位 */
    public List<Map<String, Object>> licenseCompliance() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> l : jdbc.queryForList("SELECT * FROM v_it_license ORDER BY id")) {
            Map<String, Object> m = new LinkedHashMap<>(l);
            int waiting = waiting(((Number) l.get("id")).longValue(), (String) l.get("scope")).size();
            m.put("waiting", waiting);
            m.put("status", waiting > 0 ? "SHORTAGE" : ((Number) l.get("available")).intValue() > ((Number) l.get("seats")).intValue() * 0.3 ? "SURPLUS" : "OK");
            out.add(m);
        }
        return out;
    }

    /**
     * 等待分配的员工：全员许可 = 在职职员中没有席位的；部门许可（专业软件并非部门每人都用）= 入职开通时因席位不足未分配、目前仍在职且仍无席位的
     */
    private List<Long> waiting(long licenseId, String scope) {
        if ("ALL".equals(scope)) {
            return jdbc.queryForList("""
                    SELECT e.id FROM hr_employee e WHERE e.status <> 'LEFT' AND e.leave_date IS NULL AND e.wage_type = 'MONTHLY'
                      AND NOT EXISTS (SELECT 1 FROM it_license_assign a WHERE a.license_id = ? AND a.emp_id = e.id AND a.revoked_date IS NULL)
                    ORDER BY e.hire_date""", Long.class, licenseId);
        }
        String name = jdbc.queryForObject("SELECT name FROM it_license WHERE id = ?", String.class, licenseId);
        return jdbc.queryForList("""
                SELECT DISTINCT e.id FROM it_ticket t JOIN hr_employee e ON e.id = t.requester_emp_id
                WHERE t.category = '入离职' AND t.title LIKE '新员工入职%' AND t.description LIKE ? AND e.status <> 'LEFT' AND e.leave_date IS NULL
                  AND NOT EXISTS (SELECT 1 FROM it_license_assign a WHERE a.license_id = ? AND a.emp_id = e.id AND a.revoked_date IS NULL)
                ORDER BY e.id""", Long.class, "%" + name + " 席位不足%", licenseId);
    }

    // ================= 看板 =================

    public Map<String, Object> dashboard(LocalDate from, LocalDate to) {
        Date f = Date.valueOf(from), t = Date.valueOf(to.plusDays(1));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("summary", jdbc.queryForMap("""
                SELECT COUNT(*) AS tickets, SUM(type = 'INCIDENT') AS incidents, SUM(type = 'REQUEST') AS requests, SUM(type = 'CHANGE') AS changes,
                       SUM(priority = 'P1' AND type = 'INCIDENT') AS p1,
                       ROUND(SUM(type <> 'CHANGE' AND resolved_at IS NOT NULL AND sla_breached = 0) / NULLIF(SUM(type <> 'CHANGE' AND resolved_at IS NOT NULL), 0), 4) AS sla_rate,
                       ROUND(AVG(CASE WHEN type = 'INCIDENT' THEN TIMESTAMPDIFF(MINUTE, created_at, resolved_at) END) / 60, 1) AS mttr_h,
                       ROUND(AVG(satisfaction), 2) AS csat, SUM(status IN ('NEW','IN_PROGRESS')) AS backlog
                FROM it_ticket WHERE created_at >= ? AND created_at < ?""", f, t));
        out.put("byPriority", jdbc.queryForList("""
                SELECT priority, COUNT(*) AS n, SUM(sla_breached) AS breached, ROUND(AVG(TIMESTAMPDIFF(MINUTE, created_at, resolved_at)) / 60, 1) AS avg_resolve_h
                FROM it_ticket WHERE type <> 'CHANGE' AND created_at >= ? AND created_at < ? GROUP BY priority ORDER BY priority""", f, t));
        out.put("byCategory", jdbc.queryForList("""
                SELECT category, COUNT(*) AS n, SUM(type = 'INCIDENT') AS incidents FROM it_ticket
                WHERE type <> 'CHANGE' AND created_at >= ? AND created_at < ? GROUP BY category ORDER BY n DESC""", f, t));
        out.put("monthly", jdbc.queryForList("""
                SELECT DATE_FORMAT(created_at, '%Y-%m') AS period, SUM(type = 'INCIDENT') AS incidents, SUM(type = 'REQUEST') AS requests,
                       SUM(type = 'CHANGE') AS changes, SUM(sla_breached) AS breached FROM it_ticket WHERE created_at >= ? AND created_at < ?
                GROUP BY period ORDER BY period""", f, t));
        out.put("topCis", jdbc.queryForList("""
                SELECT ci.ci_no, ci.name, ci.type, COUNT(*) AS incidents FROM it_ticket t JOIN it_ci ci ON ci.id = t.ci_id
                WHERE t.type = 'INCIDENT' AND t.created_at >= ? AND t.created_at < ? GROUP BY ci.ci_no, ci.name, ci.type ORDER BY incidents DESC LIMIT 8""", f, t));
        out.put("majorIncidents", jdbc.queryForList("""
                SELECT i.ticket_no, i.title, i.created_at, i.resolved_at, i.root_cause, c.ticket_no AS change_no, c.title AS change_title, c.status AS change_status
                FROM it_ticket i LEFT JOIN it_ticket c ON c.related_id = i.id AND c.type = 'CHANGE'
                WHERE i.type = 'INCIDENT' AND i.priority = 'P1' AND i.created_at >= ? AND i.created_at < ? ORDER BY i.created_at""", f, t));
        out.put("cmdb", jdbc.queryForList("""
                SELECT type, SUM(status = 'IN_USE') AS in_use, SUM(status = 'STOCK') AS stock, SUM(status = 'RETIRED') AS retired,
                       SUM(status = 'IN_USE' AND purchase_date < DATE_SUB(?, INTERVAL 48 MONTH)) AS over_4y FROM it_ci GROUP BY type ORDER BY type""", Date.valueOf(to)));
        out.put("licenses", licenseCompliance());
        return out;
    }

    private Map<String, Object> employee(long empId) {
        return jdbc.queryForList("""
                SELECT e.id, e.name, e.dept_id, d.code AS dept_code, d.name AS dept_name FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id WHERE e.id = ?""", empId)
                .stream().findFirst().orElseThrow(() -> new NotFoundException("员工", empId));
    }
}
