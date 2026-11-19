package com.minierp.pm;

import com.minierp.common.BizException;
import com.minierp.common.NotFoundException;
import com.minierp.common.WorkCalendar;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 研发项目管理：项目 / 迭代 / 问题（史诗、需求、任务、缺陷）/ 评论 / 工时 / 关联。
 * 所有状态、经办人、迭代变化写入历史表，燃尽图、周期时间、累积流图都由历史计算；
 * 客诉进入分析自动生成对应产品系列平台项目的缺陷，客诉结案时缺陷关闭；
 * 研发人工成本按工时分摊到项目（研发费用加计扣除的项目辅助账）。
 */
@Service
@RequiredArgsConstructor
public class PmService {
    private final JdbcTemplate jdbc;

    public record IssueReq(long projectId, String type, String title, String description, String priority, Long assigneeEmpId,
                           Long epicId, Integer storyPoints, BigDecimal estimateHours, String labels, Long ecnId, Long complaintId, LocalDate dueDate) {
    }

    // ---------- 项目与迭代 ----------

    @Transactional
    public long createProject(String key, String name, Long plmProjectId, long leadEmpId, String boardType, String series, LocalDate date) {
        if (key == null || !key.matches("[A-Z]{2,6}")) {
            throw new BizException("项目编号前缀为 2~6 位大写字母");
        }
        if (!jdbc.queryForList("SELECT id FROM pm_project WHERE project_key = ?", Long.class, key).isEmpty()) {
            throw new BizException("项目前缀已存在：" + key);
        }
        jdbc.update("INSERT INTO pm_project (project_key, name, plm_project_id, lead_emp_id, board_type, series, created_date) VALUES (?,?,?,?,?,?,?)",
                key, name, plmProjectId, leadEmpId, boardType, series, Date.valueOf(date));
        return jdbc.queryForObject("SELECT id FROM pm_project WHERE project_key = ?", Long.class, key);
    }

    @Transactional
    public long createSprint(long projectId, String name, String goal, LocalDate start, LocalDate end) {
        if (!end.isAfter(start)) {
            throw new BizException("迭代结束日必须晚于开始日");
        }
        jdbc.update("INSERT INTO pm_sprint (project_id, name, goal, start_date, end_date, status) VALUES (?,?,?,?,?,'PLANNED')",
                projectId, name, goal, Date.valueOf(start), Date.valueOf(end));
        return jdbc.queryForObject("SELECT MAX(id) FROM pm_sprint WHERE project_id = ?", Long.class, projectId);
    }

    /** 启动迭代：同一项目只能有一个进行中的迭代，记录承诺点数 */
    @Transactional
    public void startSprint(long sprintId) {
        Map<String, Object> s = sprint(sprintId);
        if (!"PLANNED".equals(s.get("status"))) {
            throw new BizException("只有计划中的迭代可以启动");
        }
        Integer active = jdbc.queryForObject("SELECT COUNT(*) FROM pm_sprint WHERE project_id = ? AND status = 'ACTIVE'", Integer.class, s.get("project_id"));
        if (active != null && active > 0) {
            throw new BizException("项目已有进行中的迭代");
        }
        Integer points = jdbc.queryForObject("SELECT COALESCE(SUM(story_points), 0) FROM pm_issue WHERE sprint_id = ? AND status <> 'DONE'", Integer.class, sprintId);
        jdbc.update("UPDATE pm_sprint SET status = 'ACTIVE', committed_points = ? WHERE id = ?", points, sprintId);
    }

    /** 关闭迭代：统计完成点数，未完成问题移到下一个迭代（没有则回到待办池） */
    @Transactional
    public int closeSprint(long sprintId, Long nextSprintId, LocalDateTime at) {
        Map<String, Object> s = sprint(sprintId);
        if (!"ACTIVE".equals(s.get("status"))) {
            throw new BizException("只有进行中的迭代可以关闭");
        }
        Integer done = jdbc.queryForObject("SELECT COALESCE(SUM(story_points), 0) FROM pm_issue WHERE sprint_id = ? AND status = 'DONE'", Integer.class, sprintId);
        List<Long> open = jdbc.queryForList("SELECT id FROM pm_issue WHERE sprint_id = ? AND status <> 'DONE'", Long.class, sprintId);
        for (Long id : open) {
            history(id, "sprint", String.valueOf(sprintId), str(nextSprintId), at, null);
            jdbc.update("UPDATE pm_issue SET sprint_id = ? WHERE id = ?", nextSprintId, id);
        }
        jdbc.update("UPDATE pm_sprint SET status = 'CLOSED', completed_points = ? WHERE id = ?", done, sprintId);
        return open.size();
    }

    // ---------- 问题 ----------

    @Transactional
    public long createIssue(IssueReq r, long reporterEmpId, LocalDateTime at) {
        if (!Set.of("EPIC", "STORY", "TASK", "BUG").contains(r.type())) {
            throw new BizException("问题类型无效：" + r.type());
        }
        if (r.title() == null || r.title().isBlank()) {
            throw new BizException("标题必填");
        }
        Map<String, Object> p = jdbc.queryForList("SELECT * FROM pm_project WHERE id = ? FOR UPDATE", r.projectId()).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("研发项目", r.projectId()));
        int seq = ((Number) p.get("issue_seq")).intValue() + 1;
        jdbc.update("UPDATE pm_project SET issue_seq = ? WHERE id = ?", seq, r.projectId());
        String key = p.get("project_key") + "-" + seq;
        jdbc.update("""
                INSERT INTO pm_issue (issue_key, project_id, type, title, description, status, priority, assignee_emp_id, reporter_emp_id, epic_id,
                  story_points, estimate_hours, labels, ecn_id, complaint_id, due_date, created_at) VALUES (?,?,?,?,?,'TODO',?,?,?,?,?,?,?,?,?,?,?)""",
                key, r.projectId(), r.type(), r.title(), r.description(), r.priority() == null ? "MEDIUM" : r.priority(), r.assigneeEmpId(),
                reporterEmpId, r.epicId(), r.storyPoints(), r.estimateHours(), r.labels(), r.ecnId(), r.complaintId(),
                r.dueDate() == null ? null : Date.valueOf(r.dueDate()), Timestamp.valueOf(at));
        long id = jdbc.queryForObject("SELECT id FROM pm_issue WHERE issue_key = ?", Long.class, key);
        history(id, "status", null, "TODO", at, reporterEmpId);
        return id;
    }

    @Transactional
    public void transition(long issueId, String to, String resolution, long actorEmpId, LocalDateTime at) {
        Map<String, Object> i = issue(issueId);
        String from = (String) i.get("status");
        try {
            IssueWorkflow.validate((String) i.get("type"), from, to, resolution);
        } catch (IllegalArgumentException e) {
            throw new BizException(e.getMessage());
        }
        if ("DONE".equals(to) && "EPIC".equals(i.get("type"))) {
            Integer open = jdbc.queryForObject("SELECT COUNT(*) FROM pm_issue WHERE epic_id = ? AND status <> 'DONE'", Integer.class, issueId);
            if (open != null && open > 0) {
                throw new BizException("史诗下还有 " + open + " 个未完成的问题");
            }
        }
        jdbc.update("""
                UPDATE pm_issue SET status = ?, resolution = ?, started_at = CASE WHEN ? = 'IN_PROGRESS' AND started_at IS NULL THEN ? ELSE started_at END,
                  resolved_at = CASE WHEN ? = 'DONE' THEN ? ELSE NULL END WHERE id = ?""",
                to, "DONE".equals(to) ? resolution : null, to, Timestamp.valueOf(at), to, Timestamp.valueOf(at), issueId);
        history(issueId, "status", from, to, at, actorEmpId);
    }

    @Transactional
    public void assign(long issueId, Long empId, long actorEmpId, LocalDateTime at) {
        Map<String, Object> i = issue(issueId);
        jdbc.update("UPDATE pm_issue SET assignee_emp_id = ? WHERE id = ?", empId, issueId);
        history(issueId, "assignee", str(i.get("assignee_emp_id")), str(empId), at, actorEmpId);
    }

    /** 移入迭代（null 表示移回待办池）；迭代已启动时记为范围变更 */
    @Transactional
    public void moveToSprint(long issueId, Long sprintId, long actorEmpId, LocalDateTime at) {
        Map<String, Object> i = issue(issueId);
        if (sprintId != null) {
            Map<String, Object> s = sprint(sprintId);
            if (!s.get("project_id").equals(i.get("project_id"))) {
                throw new BizException("不能移入其他项目的迭代");
            }
            if ("CLOSED".equals(s.get("status"))) {
                throw new BizException("迭代已关闭");
            }
        }
        jdbc.update("UPDATE pm_issue SET sprint_id = ? WHERE id = ?", sprintId, issueId);
        history(issueId, "sprint", str(i.get("sprint_id")), str(sprintId), at, actorEmpId);
    }

    @Transactional
    public void comment(long issueId, long empId, String body, LocalDateTime at) {
        issue(issueId);
        if (body == null || body.isBlank()) {
            throw new BizException("评论内容不能为空");
        }
        jdbc.update("INSERT INTO pm_comment (issue_id, emp_id, body, created_at) VALUES (?,?,?,?)", issueId, empId, body, Timestamp.valueOf(at));
    }

    /** 登记工时：单人单日合计不超过 16 小时 */
    @Transactional
    public void logWork(long issueId, long empId, LocalDate date, BigDecimal hours, String comment) {
        issue(issueId);
        if (hours == null || hours.signum() <= 0) {
            throw new BizException("工时必须大于 0");
        }
        BigDecimal day = jdbc.queryForObject("SELECT COALESCE(SUM(hours), 0) FROM pm_worklog WHERE emp_id = ? AND work_date = ?", BigDecimal.class, empId, Date.valueOf(date));
        if (day.add(hours).compareTo(BigDecimal.valueOf(16)) > 0) {
            throw new BizException("当日工时合计超过 16 小时");
        }
        jdbc.update("INSERT INTO pm_worklog (issue_id, emp_id, work_date, hours, comment) VALUES (?,?,?,?,?)", issueId, empId, Date.valueOf(date), hours, comment);
    }

    @Transactional
    public void link(long fromId, long toId, String type) {
        if (fromId == toId || !Set.of("BLOCKS", "RELATES", "DUPLICATES").contains(type)) {
            throw new BizException("关联无效");
        }
        issue(fromId);
        issue(toId);
        jdbc.update("INSERT IGNORE INTO pm_issue_link (from_issue_id, to_issue_id, link_type) VALUES (?,?,?)", fromId, toId, type);
    }

    public Map<String, Object> detail(long issueId) {
        Map<String, Object> r = new LinkedHashMap<>(jdbc.queryForList("SELECT * FROM v_pm_issue WHERE id = ?", issueId).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("问题", issueId)));
        r.put("description", jdbc.queryForObject("SELECT description FROM pm_issue WHERE id = ?", String.class, issueId));
        r.put("comments", jdbc.queryForList("SELECT c.*, e.name AS emp_name FROM pm_comment c JOIN hr_employee e ON e.id = c.emp_id WHERE c.issue_id = ? ORDER BY c.id", issueId));
        r.put("worklogs", jdbc.queryForList("SELECT w.*, e.name AS emp_name FROM pm_worklog w JOIN hr_employee e ON e.id = w.emp_id WHERE w.issue_id = ? ORDER BY w.work_date DESC LIMIT 50", issueId));
        r.put("history", jdbc.queryForList("SELECT h.*, e.name AS emp_name FROM pm_issue_history h LEFT JOIN hr_employee e ON e.id = h.emp_id WHERE h.issue_id = ? ORDER BY h.id", issueId));
        r.put("links", jdbc.queryForList("""
                SELECT l.link_type, 'OUT' AS dir, i.issue_key, i.title, i.status FROM pm_issue_link l JOIN pm_issue i ON i.id = l.to_issue_id WHERE l.from_issue_id = ?
                UNION ALL SELECT l.link_type, 'IN', i.issue_key, i.title, i.status FROM pm_issue_link l JOIN pm_issue i ON i.id = l.from_issue_id WHERE l.to_issue_id = ?""",
                issueId, issueId));
        r.put("children", jdbc.queryForList("SELECT issue_key, title, type, status, story_points FROM pm_issue WHERE epic_id = ? ORDER BY id", issueId));
        return r;
    }

    /** 看板：进行中迭代（SCRUM）或全部未完成 + 近 14 天完成（KANBAN），按状态分列 */
    public Map<String, Object> board(long projectId) {
        Map<String, Object> p = jdbc.queryForList("SELECT * FROM v_pm_project WHERE id = ?", projectId).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("研发项目", projectId));
        Map<String, Object> r = new LinkedHashMap<>(p);
        Long sprintId = jdbc.queryForList("SELECT id FROM pm_sprint WHERE project_id = ? AND status = 'ACTIVE'", Long.class, projectId).stream().findFirst().orElse(null);
        List<Map<String, Object>> issues = "SCRUM".equals(p.get("board_type")) && sprintId != null
                ? jdbc.queryForList("SELECT * FROM v_pm_issue WHERE sprint_id = ? AND type <> 'EPIC' ORDER BY FIELD(priority,'HIGHEST','HIGH','MEDIUM','LOW'), id", sprintId)
                : jdbc.queryForList("""
                SELECT * FROM v_pm_issue WHERE project_id = ? AND type <> 'EPIC'
                  AND (status <> 'DONE' OR resolved_at >= (SELECT DATE_SUB(MAX(resolved_at), INTERVAL 14 DAY) FROM pm_issue WHERE project_id = ?))
                ORDER BY FIELD(priority,'HIGHEST','HIGH','MEDIUM','LOW'), id""", projectId, projectId);
        r.put("sprint", sprintId == null ? null : jdbc.queryForMap("SELECT * FROM v_pm_sprint WHERE id = ?", sprintId));
        r.put("issues", issues);
        r.put("backlog", jdbc.queryForList("SELECT * FROM v_pm_issue WHERE project_id = ? AND sprint_id IS NULL AND status <> 'DONE' AND type <> 'EPIC' ORDER BY id DESC LIMIT 100", projectId));
        r.put("epics", jdbc.queryForList("""
                SELECT e.id, e.issue_key, e.title, e.status, COUNT(c.id) AS total, SUM(c.status = 'DONE') AS done
                FROM pm_issue e LEFT JOIN pm_issue c ON c.epic_id = e.id WHERE e.project_id = ? AND e.type = 'EPIC' GROUP BY e.id ORDER BY e.id""", projectId));
        r.put("sprints", jdbc.queryForList("SELECT * FROM v_pm_sprint WHERE project_id = ? ORDER BY start_date DESC LIMIT 12", projectId));
        return r;
    }

    // ---------- 度量 ----------

    public Map<String, Object> burndown(long sprintId) {
        Map<String, Object> s = sprint(sprintId);
        LocalDate start = ((Date) s.get("start_date")).toLocalDate(), end = ((Date) s.get("end_date")).toLocalDate();
        List<LocalDate> days = new ArrayList<>();
        for (LocalDate d = start; !d.isAfter(end); d = d.plusDays(1)) {
            if (WorkCalendar.isWorkday(d)) {
                days.add(d);
            }
        }
        String name = (String) s.get("name");
        // 完成：迭代内问题转为 DONE 的日期；范围变更：迭代启动后移入本迭代的问题
        List<AgileMetrics.Completion> done = jdbc.query("""
                SELECT DATE(h.changed_at), COALESCE(i.story_points, 0) FROM pm_issue_history h JOIN pm_issue i ON i.id = h.issue_id
                WHERE i.sprint_id <=> ? AND h.field = 'status' AND h.to_value = 'DONE' AND DATE(h.changed_at) BETWEEN ? AND ?""",
                (rs, n) -> new AgileMetrics.Completion(rs.getDate(1).toLocalDate(), rs.getInt(2)), sprintId, Date.valueOf(start), Date.valueOf(end));
        List<AgileMetrics.Completion> added = jdbc.query("""
                SELECT DATE(h.changed_at), COALESCE(i.story_points, 0) FROM pm_issue_history h JOIN pm_issue i ON i.id = h.issue_id
                WHERE h.field = 'sprint' AND h.to_value = ? AND DATE(h.changed_at) > ? AND DATE(h.changed_at) <= ?""",
                (rs, n) -> new AgileMetrics.Completion(rs.getDate(1).toLocalDate(), rs.getInt(2)), String.valueOf(sprintId), Date.valueOf(start), Date.valueOf(end));
        Map<String, Object> r = new LinkedHashMap<>(s);
        r.put("points", AgileMetrics.burndown(days, ((Number) s.get("committed_points")).intValue(), done, added));
        r.put("name", name);
        return r;
    }

    public Map<String, Object> metrics(long projectId, LocalDate from, LocalDate to) {
        Map<String, Object> r = new LinkedHashMap<>();
        List<Map<String, Object>> sprints = jdbc.queryForList(
                "SELECT name, start_date, end_date, committed_points, completed_points FROM pm_sprint WHERE project_id = ? AND status = 'CLOSED' ORDER BY start_date", projectId);
        r.put("sprints", sprints);
        r.put("velocity", AgileMetrics.velocity(sprints.stream().map(s -> ((Number) s.get("completed_points")).intValue()).toList(), 5));
        List<Double> cycle = jdbc.queryForList("""
                SELECT TIMESTAMPDIFF(HOUR, started_at, resolved_at) / 24.0 FROM pm_issue WHERE project_id = ? AND type <> 'EPIC'
                  AND resolution = 'FIXED' AND started_at IS NOT NULL AND DATE(resolved_at) BETWEEN ? AND ?""", Double.class, projectId, Date.valueOf(from), Date.valueOf(to));
        r.put("cycleTime", AgileMetrics.percentiles(cycle));
        r.put("cycleTimeByType", jdbc.queryForList("""
                SELECT type, COUNT(*) AS n, ROUND(AVG(TIMESTAMPDIFF(HOUR, started_at, resolved_at)) / 24, 1) AS avg_days FROM pm_issue
                WHERE project_id = ? AND resolution = 'FIXED' AND started_at IS NOT NULL AND DATE(resolved_at) BETWEEN ? AND ? GROUP BY type""",
                projectId, Date.valueOf(from), Date.valueOf(to)));
        List<AgileMetrics.Transition> tr = jdbc.query("""
                SELECT h.issue_id, h.changed_at, h.to_value FROM pm_issue_history h JOIN pm_issue i ON i.id = h.issue_id
                WHERE i.project_id = ? AND i.type <> 'EPIC' AND h.field = 'status' AND h.changed_at < ?""",
                (rs, n) -> new AgileMetrics.Transition(rs.getLong(1), rs.getTimestamp(2).toLocalDateTime(), rs.getString(3)), projectId, Date.valueOf(to.plusDays(1)));
        Map<LocalDate, Map<String, Integer>> cfd = AgileMetrics.cumulativeFlow(tr, from, to);
        List<Map<String, Object>> flow = new ArrayList<>();
        cfd.forEach((d, c) -> {
            if (d.getDayOfWeek().getValue() == 5 || d.equals(to)) { // 按周取点
                Map<String, Object> m = new LinkedHashMap<>(c);
                m.put("date", d);
                flow.add(m);
            }
        });
        r.put("cumulativeFlow", flow);
        r.put("bugTrend", jdbc.queryForList("""
                SELECT DATE_FORMAT(created_at, '%Y-%m') AS month, COUNT(*) AS created, SUM(status = 'DONE') AS resolved
                FROM pm_issue WHERE project_id = ? AND type = 'BUG' AND DATE(created_at) BETWEEN ? AND ? GROUP BY month ORDER BY month""",
                projectId, Date.valueOf(from), Date.valueOf(to)));
        return r;
    }

    /**
     * 研发人工成本按项目归集：研发中心员工当月工资表人工成本（应发 + 单位社保公积金）× 项目工时 / max(出勤工时, 登记工时)。
     */
    public Map<String, Object> rndCost(YearMonth from, YearMonth to) {
        List<Map<String, Object>> months = new ArrayList<>();
        Map<String, BigDecimal> total = new TreeMap<>();
        Map<String, BigDecimal> hoursTotal = new TreeMap<>();
        for (YearMonth ym = from; !ym.isAfter(to); ym = ym.plusMonths(1)) {
            Date d1 = Date.valueOf(ym.atDay(1)), d2 = Date.valueOf(ym.atEndOfMonth());
            Map<Long, Map<String, BigDecimal>> projHours = new HashMap<>();
            jdbc.query("""
                    SELECT w.emp_id, p.project_key, SUM(w.hours) FROM pm_worklog w JOIN pm_issue i ON i.id = w.issue_id JOIN pm_project p ON p.id = i.project_id
                    WHERE w.work_date BETWEEN ? AND ? GROUP BY w.emp_id, p.project_key""", rs -> {
                projHours.computeIfAbsent(rs.getLong(1), k -> new HashMap<>()).put(rs.getString(2), rs.getBigDecimal(3));
            }, d1, d2);
            List<RndCostAllocator.EmpMonth> emps = jdbc.query("""
                    SELECT s.employee_id, s.gross_pay + s.social_employer,
                      (SELECT COALESCE(SUM(a.work_hours + a.overtime_hours), 0) FROM hr_attendance a WHERE a.employee_id = s.employee_id AND a.work_date BETWEEN ? AND ?)
                    FROM hr_payslip s JOIN hr_payroll pr ON pr.id = s.payroll_id WHERE pr.period = ? AND s.cost_type = 'RND'""",
                    (rs, n) -> new RndCostAllocator.EmpMonth(rs.getLong(1), rs.getBigDecimal(2), rs.getBigDecimal(3),
                            projHours.getOrDefault(rs.getLong(1), Map.of())), d1, d2, ym.toString());
            if (emps.isEmpty()) {
                continue;
            }
            Map<String, BigDecimal> alloc = RndCostAllocator.allocate(emps);
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("month", ym.toString());
            m.put("allocation", alloc);
            months.add(m);
            alloc.forEach((k, v) -> total.merge(k, v, BigDecimal::add));
            projHours.values().forEach(ph -> ph.forEach((k, v) -> hoursTotal.merge(k, v, BigDecimal::add)));
        }
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("months", months);
        r.put("total", total);
        r.put("hours", hoursTotal);
        r.put("projects", jdbc.queryForList("SELECT project_key, name, plm_project_no FROM v_pm_project ORDER BY id"));
        return r;
    }

    // ---------- 质量问题自动转研发缺陷 ----------

    /** 客诉进入分析：若产品系列有平台维护项目，生成缺陷（经办人 = 项目负责人），与客诉关联 */
    @Transactional
    public Long bugFromComplaint(long complaintId, LocalDateTime at) {
        Map<String, Object> c = jdbc.queryForMap("""
                SELECT c.id, c.complaint_no, c.defect_type, c.severity, c.qty_affected, p.code, p.series, cu.name AS customer
                FROM qms_complaint c JOIN md_product p ON p.id = c.product_id JOIN md_customer cu ON cu.id = c.customer_id WHERE c.id = ?""", complaintId);
        List<Map<String, Object>> proj = jdbc.queryForList("SELECT id, lead_emp_id FROM pm_project WHERE series = ? AND status = 'ACTIVE' LIMIT 1", c.get("series"));
        if (proj.isEmpty() || !jdbc.queryForList("SELECT id FROM pm_issue WHERE complaint_id = ?", Long.class, complaintId).isEmpty()) {
            return null;
        }
        long lead = ((Number) proj.get(0).get("lead_emp_id")).longValue();
        String priority = "HIGH".equals(c.get("severity")) ? "HIGHEST" : "MEDIUM".equals(c.get("severity")) ? "HIGH" : "MEDIUM";
        return createIssue(new IssueReq(((Number) proj.get(0).get("id")).longValue(), "BUG",
                "[客诉 " + c.get("complaint_no") + "] " + c.get("code") + " " + defectName((String) c.get("defect_type")),
                "客户：" + c.get("customer") + "\n涉及数量：" + ((Number) c.get("qty_affected")).intValue() + "\n由质量客诉自动转入，请研发分析设计 / 工艺根因。",
                priority, lead, null, "HIGHEST".equals(priority) ? 5 : 3, null, "客诉," + c.get("defect_type"), null, complaintId, at.toLocalDate().plusDays(14)),
                lead, at);
    }

    /** 客诉 8D 结案：关联缺陷如未完成则按已修复关闭（经评审） */
    @Transactional
    public void closeBugsOfComplaint(long complaintId, LocalDateTime at) {
        for (Map<String, Object> i : jdbc.queryForList("SELECT id, status, assignee_emp_id FROM pm_issue WHERE complaint_id = ? AND status <> 'DONE'", complaintId)) {
            long id = ((Number) i.get("id")).longValue();
            long who = i.get("assignee_emp_id") == null ? 0 : ((Number) i.get("assignee_emp_id")).longValue();
            if ("TODO".equals(i.get("status"))) {
                transition(id, "IN_PROGRESS", null, who, at.minusMinutes(2));
            }
            if (!"IN_REVIEW".equals(jdbc.queryForObject("SELECT status FROM pm_issue WHERE id = ?", String.class, id))) {
                transition(id, "IN_REVIEW", null, who, at.minusMinutes(1));
            }
            transition(id, "DONE", "FIXED", who, at);
            comment(id, who == 0 ? jdbc.queryForObject("SELECT reporter_emp_id FROM pm_issue WHERE id = ?", Long.class, id) : who, "客诉 8D 结案，缺陷关闭", at);
        }
    }

    // ---------- 内部 ----------

    private void history(long issueId, String field, String from, String to, LocalDateTime at, Long empId) {
        jdbc.update("INSERT INTO pm_issue_history (issue_id, field, from_value, to_value, changed_at, emp_id) VALUES (?,?,?,?,?,?)",
                issueId, field, from, to, Timestamp.valueOf(at), empId == null || empId == 0 ? null : empId);
    }

    private Map<String, Object> issue(long id) {
        return jdbc.queryForList("SELECT * FROM pm_issue WHERE id = ?", id).stream().findFirst().orElseThrow(() -> new NotFoundException("问题", id));
    }

    private Map<String, Object> sprint(long id) {
        return jdbc.queryForList("SELECT * FROM pm_sprint WHERE id = ?", id).stream().findFirst().orElseThrow(() -> new NotFoundException("迭代", id));
    }

    private static String str(Object o) {
        return o == null ? null : o.toString();
    }

    private static String defectName(String code) {
        return switch (code) {
            case "NOISE" -> "异响";
            case "ENCODER" -> "编码器故障";
            case "OVERHEAT" -> "过热";
            case "NO_START" -> "不启动";
            case "SPEED" -> "转速异常";
            default -> "外观不良";
        };
    }
}
