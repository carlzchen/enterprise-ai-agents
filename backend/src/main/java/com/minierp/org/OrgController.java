package com.minierp.org;

import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@Tag(name = "org", description = "组织与统一身份")
@RestController
@RequestMapping("/api/org")
@RequiredArgsConstructor
public class OrgController {
    private static final String HR_ADMIN = "hasAnyRole('ADMIN','HR')";
    private final OrgService orgs;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    public record TransferReq(Long deptId, Long positionId, Long managerId, LocalDate date, String remark) {
    }

    public record SalaryReq(BigDecimal baseSalary, LocalDate date, String remark) {
    }

    public record AccountReq(String username, String role, String initialPassword) {
    }

    public record LeaveNowReq(LocalDate date, String reason) {
    }

    @Operation(summary = "部门树（含在职人数）")
    @GetMapping("/tree")
    public List<Map<String, Object>> tree() {
        return orgs.tree();
    }

    @GetMapping("/positions")
    public List<Map<String, Object>> positions() {
        return jdbc.queryForList("SELECT id, code, name, grade FROM org_position ORDER BY id");
    }

    @Operation(summary = "员工列表：按部门（含全部下级部门）、关键字、状态分页；薪资列按角色裁剪")
    @GetMapping("/employees")
    public com.minierp.common.PageResult<Map<String, Object>> employees(@RequestParam(required = false) Long deptId,
            @RequestParam(required = false) String kw, @RequestParam(required = false) String status,
            @RequestParam(defaultValue = "1") int page, @RequestParam(defaultValue = "20") int size,
            org.springframework.security.core.Authentication auth) {
        StringBuilder where = new StringBuilder(" WHERE 1=1");
        List<Object> args = new java.util.ArrayList<>();
        if (deptId != null) {
            java.util.Set<Long> ids = OrgChart.subtree(deptId, orgs.chart().depts().values());
            where.append(" AND dept_id IN (").append(String.join(",", java.util.Collections.nCopies(ids.size(), "?"))).append(")");
            args.addAll(ids);
        }
        if (kw != null && !kw.isBlank()) {
            where.append(" AND (name LIKE ? OR emp_no LIKE ?)");
            args.add("%" + kw.trim() + "%");
            args.add("%" + kw.trim() + "%");
        }
        if (status != null && !status.isBlank()) {
            where.append(" AND status = ?");
            args.add(status);
        }
        size = Math.min(Math.max(size, 1), 100);
        Long total = jdbc.queryForObject("SELECT COUNT(*) FROM v_employee" + where, Long.class, args.toArray());
        List<Object> pageArgs = new java.util.ArrayList<>(args);
        pageArgs.add(size);
        pageArgs.add((Math.max(page, 1) - 1) * size);
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT * FROM v_employee" + where + " ORDER BY emp_no LIMIT ? OFFSET ?", pageArgs.toArray());
        if (!com.minierp.query.ViewRegistry.canSeeSensitive(com.minierp.query.CurrentRole.of(auth), "employees")) {
            rows.forEach(r -> {
                r.remove("base_salary");
                r.remove("social_base");
            });
        }
        return new com.minierp.common.PageResult<>(rows, total == null ? 0 : total, page, size);
    }

    @Operation(summary = "员工详情（含汇报链、异动记录）")
    @GetMapping("/employees/{id}")
    public Map<String, Object> employee(@PathVariable long id, org.springframework.security.core.Authentication auth) {
        Map<String, Object> e = orgs.employee(id);
        if (!com.minierp.query.ViewRegistry.canSeeSensitive(com.minierp.query.CurrentRole.of(auth), "employees")) {
            e.remove("base_salary");
            e.remove("social_base");
            @SuppressWarnings("unchecked") List<Map<String, Object>> changes = (List<Map<String, Object>>) e.get("changes");
            changes.forEach(c -> {
                c.remove("from_salary");
                c.remove("to_salary");
            });
        }
        return e;
    }

    @PostMapping("/employees")
    @PreAuthorize(HR_ADMIN)
    @Audited("org.hire")
    public Map<String, Object> hire(@RequestBody OrgService.HireReq req) {
        return orgs.employee(orgs.hire(req));
    }

    @PostMapping("/employees/{id}/transfer")
    @PreAuthorize(HR_ADMIN)
    @Audited("org.transfer")
    public Map<String, Object> transfer(@PathVariable long id, @RequestBody TransferReq r) {
        orgs.transfer(id, r.deptId(), r.positionId(), r.managerId(), r.date() == null ? LocalDate.now() : r.date(), r.remark());
        return orgs.employee(id);
    }

    @PostMapping("/employees/{id}/salary")
    @PreAuthorize(HR_ADMIN)
    @Audited("org.adjustSalary")
    public Map<String, Object> salary(@PathVariable long id, @RequestBody SalaryReq r) {
        orgs.adjustSalary(id, r.baseSalary(), r.date() == null ? LocalDate.now() : r.date(), r.remark());
        return orgs.employee(id);
    }

    @Operation(summary = "直接离职（辞退 / 自离），当天生效并停用账号")
    @PostMapping("/employees/{id}/leave")
    @PreAuthorize(HR_ADMIN)
    @Audited("org.leaveNow")
    public Map<String, Object> leaveNow(@PathVariable long id, @RequestBody LeaveNowReq r) {
        orgs.leaveNow(id, r.date() == null ? LocalDate.now() : r.date(), r.reason());
        return orgs.employee(id);
    }

    @Operation(summary = "为员工开通系统账号（统一身份）")
    @PostMapping("/employees/{id}/account")
    @PreAuthorize(HR_ADMIN)
    @Audited("org.createAccount")
    public Map<String, Object> account(@PathVariable long id, @RequestBody AccountReq r) {
        orgs.createAccount(id, r.username(), r.role(), r.initialPassword());
        return orgs.employee(id);
    }
}
