package com.minierp.workflow;

import com.minierp.common.BizException;
import com.minierp.common.PageResult;
import com.minierp.finance.Expense;
import com.minierp.finance.FinanceService;
import com.minierp.hr.LeaveService;
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
import java.util.*;

/** OA：审批中心（发起 / 待办 / 已办 / 我发起的）、流程定义、公告 */
@Tag(name = "oa", description = "OA 协同办公与审批流")
@RestController
@RequestMapping("/api/oa")
@RequiredArgsConstructor
public class OaController {
    private final WorkflowService workflow;
    private final OrgService orgs;
    private final LeaveService leaves;
    private final FinanceService finance;
    private final JdbcTemplate jdbc;

    public record SubmitReq(String defCode, String title, Map<String, Object> form) {
    }

    public record ActReq(String comment, Long toEmpId) {
    }

    public record DefinitionReq(String code, String name, String definition, String formSchema) {
    }

    public record NoticeReq(String title, String content, String category, Long deptId, Boolean pinned) {
    }

    @GetMapping("/definitions")
    public List<Map<String, Object>> definitions() {
        return workflow.definitions();
    }

    @Operation(summary = "保存流程定义新版本（运行中的实例按原版本执行）")
    @PostMapping("/definitions")
    @PreAuthorize("hasAnyRole('ADMIN','HR')")
    @Audited("oa.saveDefinition")
    public Map<String, Object> saveDefinition(@RequestBody DefinitionReq r) {
        return Map.of("version", workflow.saveDefinition(r.code(), r.name(), r.definition(), r.formSchema()));
    }

    @Operation(summary = "预览审批路线")
    @PostMapping("/preview")
    public List<WorkflowRouter.RouteNode> preview(@RequestBody SubmitReq r, Authentication auth) {
        return workflow.preview(r.defCode(), me(auth), vars(r));
    }

    @Operation(summary = "发起审批：请假、离职、报销走对应业务；出差、用印、通用审批直接发起流程")
    @PostMapping("/submit")
    @Audited("oa.submit")
    public Map<String, Object> submit(@RequestBody SubmitReq r, Authentication auth) {
        long emp = me(auth);
        Map<String, Object> f = r.form() == null ? Map.of() : r.form();
        LocalDateTime now = LocalDateTime.now();
        long id = switch (r.defCode()) {
            case "LEAVE" -> leaves.applyLeave(emp, str(f, "leaveType"), date(f, "startDate"), date(f, "endDate"), str(f, "reason"), now);
            case "RESIGN" -> leaves.applyResign(emp, date(f, "lastDay"), str(f, "reason"), now);
            case "EXPENSE" -> {
                Expense e = new Expense();
                e.setExpenseDate(LocalDate.now());
                e.setDept(jdbc.queryForObject("SELECT d.name FROM hr_employee x JOIN org_dept d ON d.id = x.dept_id WHERE x.id = ?", String.class, emp));
                e.setCategory(str(f, "category"));
                e.setAmount(new BigDecimal(String.valueOf(f.get("amount"))));
                e.setEmployee(jdbc.queryForObject("SELECT name FROM hr_employee WHERE id = ?", String.class, emp));
                e.setDescription(str(f, "description"));
                yield finance.submit(e, emp, now).getWfInstanceId();
            }
            case "PAYROLL" -> throw new BizException("工资发放审批请在工资表中提交");
            default -> {
                Map<String, Object> v = vars(r);
                yield workflow.start(new WorkflowService.StartReq(r.defCode(), r.title() == null || r.title().isBlank() ? defaultTitle(r, emp) : r.title(),
                        f, null, null, (BigDecimal) v.get("amount"), (BigDecimal) v.get("days")), emp, now);
            }
        };
        return workflow.detail(id);
    }

    @GetMapping("/instances/{id}")
    public Map<String, Object> detail(@PathVariable long id) {
        return workflow.detail(id);
    }

    @PostMapping("/tasks/{id}/approve")
    @Audited("oa.approve")
    public void approve(@PathVariable long id, @RequestBody(required = false) ActReq r, Authentication auth) {
        workflow.approve(id, me(auth), r == null ? null : r.comment(), LocalDateTime.now());
    }

    @PostMapping("/tasks/{id}/reject")
    @Audited("oa.reject")
    public void reject(@PathVariable long id, @RequestBody ActReq r, Authentication auth) {
        workflow.reject(id, me(auth), r.comment(), LocalDateTime.now());
    }

    @PostMapping("/tasks/{id}/transfer")
    @Audited("oa.transfer")
    public void transfer(@PathVariable long id, @RequestBody ActReq r, Authentication auth) {
        workflow.transfer(id, me(auth), r.toEmpId(), r.comment(), LocalDateTime.now());
    }

    @PostMapping("/tasks/{id}/add-sign")
    @Audited("oa.addSign")
    public void addSign(@PathVariable long id, @RequestBody ActReq r, Authentication auth) {
        workflow.addSign(id, me(auth), r.toEmpId(), r.comment(), LocalDateTime.now());
    }

    @PostMapping("/instances/{id}/withdraw")
    @Audited("oa.withdraw")
    public void withdraw(@PathVariable long id, Authentication auth) {
        workflow.withdraw(id, me(auth), LocalDateTime.now());
    }

    @Operation(summary = "我的审批：todo 待办 / done 已办 / started 我发起的")
    @GetMapping("/my/{box}")
    public PageResult<Map<String, Object>> my(@PathVariable String box, @RequestParam(defaultValue = "1") int page,
                                              @RequestParam(defaultValue = "20") int size, Authentication auth) {
        long emp = me(auth);
        size = Math.min(Math.max(size, 1), 100);
        String where = switch (box) {
            case "todo" -> "FROM v_wf_task WHERE assignee_emp_id = ? AND status = 'PENDING'";
            case "done" -> "FROM v_wf_task WHERE assignee_emp_id = ? AND status IN ('APPROVED','REJECTED','TRANSFERRED')";
            case "started" -> "FROM v_wf_instance WHERE applicant_emp_id = ?";
            default -> throw new BizException("未知分类：" + box);
        };
        String order = "started".equals(box) ? " ORDER BY started_at DESC, id DESC" : " ORDER BY created_at DESC, id DESC";
        Long total = jdbc.queryForObject("SELECT COUNT(*) " + where, Long.class, emp);
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT * " + where + order + " LIMIT ? OFFSET ?", emp, size, (page - 1) * size);
        return new PageResult<>(rows, total == null ? 0 : total, page, size);
    }

    @GetMapping("/notices")
    public List<Map<String, Object>> notices(@RequestParam(defaultValue = "30") int limit) {
        return jdbc.queryForList("SELECT n.*, d.name AS dept_name FROM oa_notice n LEFT JOIN org_dept d ON d.id = n.dept_id "
                + "ORDER BY n.pinned DESC, n.publish_date DESC, n.id DESC LIMIT ?", Math.min(limit, 200));
    }

    @PostMapping("/notices")
    @PreAuthorize("hasAnyRole('ADMIN','HR','MANAGEMENT')")
    @Audited("oa.publishNotice")
    public void publish(@RequestBody NoticeReq r, Authentication auth) {
        if (r.title() == null || r.title().isBlank() || r.content() == null || r.content().isBlank()) {
            throw new BizException("标题和内容必填");
        }
        String publisher = jdbc.queryForObject("SELECT display_name FROM sys_user WHERE username = ?", String.class, auth.getName());
        jdbc.update("INSERT INTO oa_notice (title, content, category, dept_id, publisher, publish_date, pinned) VALUES (?,?,?,?,?,?,?)",
                r.title(), r.content(), r.category() == null ? "通知" : r.category(), r.deptId(), publisher, Date.valueOf(LocalDate.now()),
                Boolean.TRUE.equals(r.pinned()));
    }

    @Operation(summary = "选人（转交、加签用）")
    @GetMapping("/people")
    public List<Map<String, Object>> people(@RequestParam String kw) {
        return jdbc.queryForList("SELECT id, emp_no, name, dept_name, position_name FROM v_employee WHERE status <> 'LEFT' AND (name LIKE ? OR emp_no LIKE ?) LIMIT 20",
                "%" + kw + "%", "%" + kw + "%");
    }

    private long me(Authentication auth) {
        return orgs.employeeOfUser(auth.getName());
    }

    private String defaultTitle(SubmitReq r, long emp) {
        String name = jdbc.queryForObject("SELECT name FROM hr_employee WHERE id = ?", String.class, emp);
        String def = jdbc.queryForObject("SELECT name FROM wf_definition WHERE code = ? ORDER BY version DESC LIMIT 1", String.class, r.defCode());
        Object subject = r.form() == null ? null : Optional.ofNullable(r.form().get("subject")).orElse(r.form().get("destination"));
        return name + " 的" + def + (subject == null ? "" : "：" + subject);
    }

    /** 条件变量：金额取 amount / budget，天数取 days 或 startDate~endDate 的工作日数 */
    private static Map<String, Object> vars(SubmitReq r) {
        Map<String, Object> v = new HashMap<>(r.form() == null ? Map.of() : r.form());
        Object amount = v.getOrDefault("amount", v.get("budget"));
        if (amount != null && !String.valueOf(amount).isBlank()) {
            v.put("amount", new BigDecimal(String.valueOf(amount)));
        }
        if (!v.containsKey("days") && v.get("startDate") != null && v.get("endDate") != null) {
            v.put("days", BigDecimal.valueOf(com.minierp.common.WorkCalendar.workdays(LocalDate.parse(String.valueOf(v.get("startDate"))),
                    LocalDate.parse(String.valueOf(v.get("endDate"))))));
        }
        return v;
    }

    private static String str(Map<String, Object> f, String k) {
        Object v = f.get(k);
        return v == null ? null : String.valueOf(v);
    }

    private static LocalDate date(Map<String, Object> f, String k) {
        Object v = f.get(k);
        if (v == null || String.valueOf(v).isBlank()) {
            throw new BizException("缺少日期：" + k);
        }
        return LocalDate.parse(String.valueOf(v).substring(0, 10));
    }
}
