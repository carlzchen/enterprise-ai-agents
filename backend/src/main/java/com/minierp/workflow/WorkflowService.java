package com.minierp.workflow;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.org.OrgService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Timestamp;
import java.time.LocalDateTime;
import java.util.*;

/**
 * 轻量审批流引擎。
 * 发起：按流程定义（版本化）+ 表单变量 + 组织关系解析出整条审批路线（WorkflowRouter），存入实例，逐节点生成待办。
 * 办理：同意 / 驳回 / 转交 / 加签；申请人可在无人同意前撤回。节点结论由 NodeDecision 判定（或签、会签、加签）。
 * 结束：发布 WorkflowFinishedEvent，业务模块在同一事务内完成后续动作。
 * 所有操作带业务时间参数，模拟器可以回放历史；接口调用传当前时间。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class WorkflowService {
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final OrgService orgs;
    private final ApplicationEventPublisher events;
    private final ObjectMapper json;

    public record StartReq(String defCode, String title, Map<String, Object> form, String bizType, Long bizId,
                           BigDecimal amount, BigDecimal days) {
    }

    // ---------- 定义 ----------

    public Map<String, Object> definition(String code) {
        List<Map<String, Object>> r = jdbc.queryForList(
                "SELECT * FROM wf_definition WHERE code = ? AND enabled = 1 ORDER BY version DESC LIMIT 1", code);
        if (r.isEmpty()) {
            throw new NotFoundException("流程定义", code);
        }
        return r.get(0);
    }

    public List<Map<String, Object>> definitions() {
        return jdbc.queryForList("""
                SELECT d.* FROM wf_definition d WHERE d.enabled = 1
                  AND d.version = (SELECT MAX(x.version) FROM wf_definition x WHERE x.code = d.code) ORDER BY d.id""");
    }

    /** 保存新版本（旧版本保留，运行中的实例继续按旧版本执行） */
    @Transactional
    public int saveDefinition(String code, String name, String definitionJson, String formSchema) {
        List<WorkflowRouter.NodeDef> nodes = parseNodes(definitionJson);
        if (nodes.isEmpty()) {
            throw new BizException("流程至少需要一个审批节点");
        }
        for (WorkflowRouter.NodeDef n : nodes) {
            try {
                ConditionEvaluator.validate(n.condition());
            } catch (IllegalArgumentException e) {
                throw new BizException("节点「" + n.name() + "」" + e.getMessage());
            }
            if (!Set.of("MANAGER", "DEPT_HEAD", "DEPT_OF", "ROLE", "USER").contains(n.type())) {
                throw new BizException("节点「" + n.name() + "」审批人类型无效：" + n.type());
            }
        }
        Integer v = jdbc.queryForObject("SELECT COALESCE(MAX(version), 0) FROM wf_definition WHERE code = ?", Integer.class, code);
        jdbc.update("INSERT INTO wf_definition (code, name, version, definition, form_schema, enabled) VALUES (?,?,?,?,?,1)",
                code, name, v + 1, definitionJson, formSchema);
        return v + 1;
    }

    // ---------- 发起 ----------

    /** 预览审批路线（发起前展示给申请人） */
    public List<WorkflowRouter.RouteNode> preview(String defCode, long applicantEmpId, Map<String, Object> vars) {
        return WorkflowRouter.route(parseNodes((String) definition(defCode).get("definition")), vars, applicantEmpId, orgs.resolver());
    }

    @Transactional
    public long start(StartReq req, long applicantEmpId, LocalDateTime now) {
        Map<String, Object> def = definition(req.defCode());
        Map<String, Object> vars = new HashMap<>(req.form() == null ? Map.of() : req.form());
        if (req.amount() != null) {
            vars.put("amount", req.amount());
        }
        if (req.days() != null) {
            vars.put("days", req.days());
        }
        List<WorkflowRouter.RouteNode> route = WorkflowRouter.route(parseNodes((String) def.get("definition")), vars, applicantEmpId, orgs.resolver());
        String no = docNo.next("WF", now.toLocalDate());
        jdbc.update("""
                INSERT INTO wf_instance (instance_no, def_code, def_version, title, biz_type, biz_id, form_data, amount, days, route,
                  current_node, applicant_emp_id, status, started_at) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,'RUNNING',?)""",
                no, req.defCode(), def.get("version"), req.title(), req.bizType(), req.bizId(), write(req.form()), req.amount(), req.days(),
                write(route), applicantEmpId, Timestamp.valueOf(now));
        long id = jdbc.queryForObject("SELECT id FROM wf_instance WHERE instance_no = ?", Long.class, no);
        activate(id, route, 0, applicantEmpId, null, now);
        return id;
    }

    /** 从 nodeIndex 开始找第一个有审批人的节点生成待办；没有则流程通过 */
    private void activate(long id, List<WorkflowRouter.RouteNode> route, int nodeIndex, long applicant, Long lastApprover, LocalDateTime now) {
        for (int i = nodeIndex; i < route.size(); i++) {
            WorkflowRouter.RouteNode n = route.get(i);
            if (n.autoPass()) {
                continue;
            }
            jdbc.update("UPDATE wf_instance SET current_node = ? WHERE id = ?", i, id);
            for (Long a : n.approvers()) {
                jdbc.update("INSERT INTO wf_task (instance_id, node_index, node_name, sign_mode, assignee_emp_id, status, created_at) VALUES (?,?,?,?,?,'PENDING',?)",
                        id, i, n.name(), n.mode(), a, Timestamp.valueOf(now));
            }
            return;
        }
        finish(id, "APPROVED", lastApprover, now);
    }

    // ---------- 办理 ----------

    @Transactional
    public void approve(long taskId, long actorEmpId, String comment, LocalDateTime now) {
        Map<String, Object> t = pendingTaskOf(taskId, actorEmpId);
        act(taskId, "APPROVED", comment, now);
        long id = ((Number) t.get("instance_id")).longValue();
        int node = ((Number) t.get("node_index")).intValue();
        NodeDecision.Outcome o = NodeDecision.evaluate((String) t.get("sign_mode"), nodeTasks(id, node));
        if (o == NodeDecision.Outcome.APPROVED) {
            jdbc.update("UPDATE wf_task SET status = 'CANCELLED', acted_at = ?, comment = '或签：他人已同意' WHERE instance_id = ? AND node_index = ? AND status = 'PENDING'",
                    Timestamp.valueOf(now), id, node);
            Map<String, Object> inst = instance(id);
            activate(id, route(inst), node + 1, ((Number) inst.get("applicant_emp_id")).longValue(), actorEmpId, now);
        }
    }

    @Transactional
    public void reject(long taskId, long actorEmpId, String comment, LocalDateTime now) {
        if (comment == null || comment.isBlank()) {
            throw new BizException("驳回必须填写理由");
        }
        Map<String, Object> t = pendingTaskOf(taskId, actorEmpId);
        act(taskId, "REJECTED", comment, now);
        long id = ((Number) t.get("instance_id")).longValue();
        jdbc.update("UPDATE wf_task SET status = 'CANCELLED', acted_at = ? WHERE instance_id = ? AND status = 'PENDING'", Timestamp.valueOf(now), id);
        finish(id, "REJECTED", actorEmpId, now);
    }

    /** 转交：本人任务标记为已转交，接收人获得同一节点的新待办 */
    @Transactional
    public void transfer(long taskId, long actorEmpId, long toEmpId, String comment, LocalDateTime now) {
        Map<String, Object> t = pendingTaskOf(taskId, actorEmpId);
        if (toEmpId == actorEmpId) {
            throw new BizException("不能转交给自己");
        }
        checkAssignable(t, toEmpId);
        act(taskId, "TRANSFERRED", comment, now);
        jdbc.update("""
                INSERT INTO wf_task (instance_id, node_index, node_name, sign_mode, assignee_emp_id, status, added_by_emp_id, created_at)
                VALUES (?,?,?,?,?,'PENDING',?,?)""", t.get("instance_id"), t.get("node_index"), t.get("node_name"), t.get("sign_mode"),
                toEmpId, t.get("added_by_emp_id"), Timestamp.valueOf(now));
    }

    /** 加签：邀请他人一起审批本节点，加签人必须同意，本人的待办保留 */
    @Transactional
    public void addSign(long taskId, long actorEmpId, long signerEmpId, String comment, LocalDateTime now) {
        Map<String, Object> t = pendingTaskOf(taskId, actorEmpId);
        checkAssignable(t, signerEmpId);
        jdbc.update("""
                INSERT INTO wf_task (instance_id, node_index, node_name, sign_mode, assignee_emp_id, status, comment, added_by_emp_id, created_at)
                VALUES (?,?,?,?,?,'PENDING',?,?,?)""", t.get("instance_id"), t.get("node_index"), t.get("node_name") + "（加签）",
                t.get("sign_mode"), signerEmpId, comment, actorEmpId, Timestamp.valueOf(now));
    }

    /** 撤回：仅申请人，且还没有任何人同意 */
    @Transactional
    public void withdraw(long instanceId, long actorEmpId, LocalDateTime now) {
        Map<String, Object> inst = instance(instanceId);
        if (((Number) inst.get("applicant_emp_id")).longValue() != actorEmpId) {
            throw new BizException("只有申请人可以撤回");
        }
        if (!"RUNNING".equals(inst.get("status"))) {
            throw new BizException("流程已结束，不能撤回");
        }
        Integer approved = jdbc.queryForObject("SELECT COUNT(*) FROM wf_task WHERE instance_id = ? AND status = 'APPROVED'", Integer.class, instanceId);
        if (approved != null && approved > 0) {
            throw new BizException("已有审批人同意，不能撤回");
        }
        jdbc.update("UPDATE wf_task SET status = 'CANCELLED', acted_at = ?, comment = '申请人撤回' WHERE instance_id = ? AND status = 'PENDING'",
                Timestamp.valueOf(now), instanceId);
        finish(instanceId, "WITHDRAWN", null, now);
    }

    public Map<String, Object> detail(long instanceId) {
        Map<String, Object> inst = new LinkedHashMap<>(jdbc.queryForList("SELECT * FROM v_wf_instance WHERE id = ?", instanceId).stream()
                .findFirst().orElseThrow(() -> new NotFoundException("流程实例", instanceId)));
        Map<String, Object> raw = instance(instanceId);
        inst.put("form", read((String) raw.get("form_data"), new TypeReference<Map<String, Object>>() {
        }));
        List<Map<String, Object>> route = new ArrayList<>();
        for (WorkflowRouter.RouteNode n : route(raw)) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("index", n.index());
            m.put("name", n.name());
            m.put("mode", n.mode());
            m.put("note", n.note());
            m.put("approvers", n.approvers().stream().map(this::empName).toList());
            route.add(m);
        }
        inst.put("route", route);
        inst.put("tasks", jdbc.queryForList("SELECT * FROM v_wf_task WHERE instance_id = ? ORDER BY node_index, id", instanceId));
        return inst;
    }

    // ---------- 内部 ----------

    private void finish(long id, String status, Long lastApprover, LocalDateTime now) {
        jdbc.update("UPDATE wf_instance SET status = ?, finished_at = ? WHERE id = ?", status, Timestamp.valueOf(now), id);
        Map<String, Object> inst = instance(id);
        log.info("流程 {} {} 结束：{}", inst.get("instance_no"), inst.get("def_code"), status);
        events.publishEvent(new WorkflowFinishedEvent(id, (String) inst.get("def_code"), (String) inst.get("biz_type"),
                inst.get("biz_id") == null ? null : ((Number) inst.get("biz_id")).longValue(), status,
                ((Number) inst.get("applicant_emp_id")).longValue(), lastApprover, now));
    }

    private Map<String, Object> pendingTaskOf(long taskId, long actorEmpId) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM wf_task WHERE id = ? FOR UPDATE", taskId);
        if (r.isEmpty()) {
            throw new NotFoundException("审批任务", taskId);
        }
        Map<String, Object> t = r.get(0);
        if (((Number) t.get("assignee_emp_id")).longValue() != actorEmpId) {
            throw new BizException("这不是你的待办");
        }
        if (!"PENDING".equals(t.get("status"))) {
            throw new BizException("该任务已处理");
        }
        return t;
    }

    private void checkAssignable(Map<String, Object> task, long empId) {
        Map<String, Object> inst = instance(((Number) task.get("instance_id")).longValue());
        if (((Number) inst.get("applicant_emp_id")).longValue() == empId) {
            throw new BizException("不能交给申请人本人审批");
        }
        Integer active = jdbc.queryForObject("SELECT COUNT(*) FROM hr_employee WHERE id = ? AND status <> 'LEFT'", Integer.class, empId);
        if (active == null || active == 0) {
            throw new BizException("目标人员不存在或已离职");
        }
        Integer dup = jdbc.queryForObject("SELECT COUNT(*) FROM wf_task WHERE instance_id = ? AND node_index = ? AND assignee_emp_id = ? AND status = 'PENDING'",
                Integer.class, task.get("instance_id"), task.get("node_index"), empId);
        if (dup != null && dup > 0) {
            throw new BizException("对方已在本节点待办中");
        }
    }

    private void act(long taskId, String status, String comment, LocalDateTime now) {
        jdbc.update("UPDATE wf_task SET status = ?, comment = ?, acted_at = ? WHERE id = ?", status, comment, Timestamp.valueOf(now), taskId);
    }

    private List<NodeDecision.TaskState> nodeTasks(long instanceId, int node) {
        return jdbc.query("SELECT status, added_by_emp_id FROM wf_task WHERE instance_id = ? AND node_index = ?",
                // 转交产生的任务沿用原任务的加签标记，所以 added_by 非空即为加签任务
                (rs, i) -> new NodeDecision.TaskState(rs.getString(1), rs.getObject(2) != null), instanceId, node);
    }

    private Map<String, Object> instance(long id) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM wf_instance WHERE id = ?", id);
        if (r.isEmpty()) {
            throw new NotFoundException("流程实例", id);
        }
        return r.get(0);
    }

    private List<WorkflowRouter.RouteNode> route(Map<String, Object> inst) {
        return read((String) inst.get("route"), new TypeReference<List<WorkflowRouter.RouteNode>>() {
        });
    }

    private String empName(long id) {
        return jdbc.queryForList("SELECT name FROM hr_employee WHERE id = ?", String.class, id).stream().findFirst().orElse("#" + id);
    }

    public List<WorkflowRouter.NodeDef> parseNodes(String definitionJson) {
        Map<String, List<WorkflowRouter.NodeDef>> m = read(definitionJson, new TypeReference<>() {
        });
        return m.getOrDefault("nodes", List.of());
    }

    private String write(Object o) {
        try {
            return o == null ? null : json.writeValueAsString(o);
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    private <T> T read(String s, TypeReference<T> type) {
        try {
            return s == null ? null : json.readValue(s, type);
        } catch (Exception e) {
            throw new BizException("流程数据格式错误：" + e.getMessage());
        }
    }
}
