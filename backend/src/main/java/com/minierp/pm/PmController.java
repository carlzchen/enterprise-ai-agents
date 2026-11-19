package com.minierp.pm;

import com.minierp.org.OrgService;
import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.Map;

@Tag(name = "pm", description = "研发项目管理：看板、迭代、问题、工时、度量、研发费用归集")
@RestController
@RequestMapping("/api/pm")
@RequiredArgsConstructor
public class PmController {
    private static final String DEV = "hasAnyRole('ADMIN','ENGINEER','QUALITY','PRODUCTION','MANAGEMENT')";
    private final PmService pm;
    private final OrgService orgs;

    public record ProjectReq(String key, String name, Long plmProjectId, String boardType, String series) {
    }

    public record SprintReq(long projectId, String name, String goal, LocalDate start, LocalDate end) {
    }

    public record TransitionReq(String to, String resolution) {
    }

    public record AssignReq(Long empId) {
    }

    public record SprintMoveReq(Long sprintId) {
    }

    public record TextReq(String body) {
    }

    public record WorklogReq(LocalDate date, BigDecimal hours, String comment) {
    }

    public record LinkReq(long toIssueId, String type) {
    }

    public record CloseReq(Long nextSprintId) {
    }

    private long me(Authentication a) {
        return orgs.employeeOfUser(a.getName());
    }

    @PostMapping("/projects")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER','MANAGEMENT')")
    @Audited("pm.createProject")
    public Map<String, Object> project(@RequestBody ProjectReq r, Authentication a) {
        return pm.board(pm.createProject(r.key(), r.name(), r.plmProjectId(), me(a), r.boardType() == null ? "SCRUM" : r.boardType(), r.series(), LocalDate.now()));
    }

    @Operation(summary = "项目看板：当前迭代 / 看板问题、待办池、史诗进度、迭代列表")
    @GetMapping("/projects/{id}/board")
    public Map<String, Object> board(@PathVariable long id) {
        return pm.board(id);
    }

    @Operation(summary = "项目度量：速率、周期时间分位数、累积流图、缺陷趋势")
    @GetMapping("/projects/{id}/metrics")
    public Map<String, Object> metrics(@PathVariable long id, @RequestParam(required = false) LocalDate from, @RequestParam(required = false) LocalDate to) {
        LocalDate t = to == null ? LocalDate.now() : to;
        return pm.metrics(id, from == null ? t.minusMonths(6) : from, t);
    }

    @PostMapping("/sprints")
    @PreAuthorize(DEV)
    @Audited("pm.createSprint")
    public Map<String, Object> sprint(@RequestBody SprintReq r) {
        return Map.of("id", pm.createSprint(r.projectId(), r.name(), r.goal(), r.start(), r.end()));
    }

    @PostMapping("/sprints/{id}/start")
    @PreAuthorize(DEV)
    @Audited("pm.startSprint")
    public void start(@PathVariable long id) {
        pm.startSprint(id);
    }

    @PostMapping("/sprints/{id}/close")
    @PreAuthorize(DEV)
    @Audited("pm.closeSprint")
    public Map<String, Object> close(@PathVariable long id, @RequestBody(required = false) CloseReq r) {
        return Map.of("carriedOver", pm.closeSprint(id, r == null ? null : r.nextSprintId(), LocalDateTime.now()));
    }

    @GetMapping("/sprints/{id}/burndown")
    public Map<String, Object> burndown(@PathVariable long id) {
        return pm.burndown(id);
    }

    @PostMapping("/issues")
    @PreAuthorize(DEV)
    @Audited("pm.createIssue")
    public Map<String, Object> create(@RequestBody PmService.IssueReq r, Authentication a) {
        return pm.detail(pm.createIssue(r, me(a), LocalDateTime.now()));
    }

    @GetMapping("/issues/{id}")
    public Map<String, Object> detail(@PathVariable long id) {
        return pm.detail(id);
    }

    @PostMapping("/issues/{id}/transition")
    @PreAuthorize(DEV)
    @Audited("pm.transition")
    public Map<String, Object> transition(@PathVariable long id, @RequestBody TransitionReq r, Authentication a) {
        pm.transition(id, r.to(), r.resolution(), me(a), LocalDateTime.now());
        return pm.detail(id);
    }

    @PostMapping("/issues/{id}/assign")
    @PreAuthorize(DEV)
    @Audited("pm.assign")
    public Map<String, Object> assign(@PathVariable long id, @RequestBody AssignReq r, Authentication a) {
        pm.assign(id, r.empId(), me(a), LocalDateTime.now());
        return pm.detail(id);
    }

    @PostMapping("/issues/{id}/sprint")
    @PreAuthorize(DEV)
    @Audited("pm.moveToSprint")
    public void moveToSprint(@PathVariable long id, @RequestBody SprintMoveReq r, Authentication a) {
        pm.moveToSprint(id, r.sprintId(), me(a), LocalDateTime.now());
    }

    @PostMapping("/issues/{id}/comments")
    @Audited("pm.comment")
    public Map<String, Object> comment(@PathVariable long id, @RequestBody TextReq r, Authentication a) {
        pm.comment(id, me(a), r.body(), LocalDateTime.now());
        return pm.detail(id);
    }

    @PostMapping("/issues/{id}/worklogs")
    @PreAuthorize(DEV)
    @Audited("pm.logWork")
    public Map<String, Object> worklog(@PathVariable long id, @RequestBody WorklogReq r, Authentication a) {
        pm.logWork(id, me(a), r.date() == null ? LocalDate.now() : r.date(), r.hours(), r.comment());
        return pm.detail(id);
    }

    @PostMapping("/issues/{id}/links")
    @PreAuthorize(DEV)
    @Audited("pm.link")
    public void link(@PathVariable long id, @RequestBody LinkReq r) {
        pm.link(id, r.toIssueId(), r.type());
    }

    @Operation(summary = "研发人工成本按项目归集（研发费用加计扣除的项目辅助账）")
    @GetMapping("/rnd-cost")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE','MANAGEMENT')")
    public Map<String, Object> rndCost(@RequestParam String from, @RequestParam String to) {
        return pm.rndCost(YearMonth.parse(from), YearMonth.parse(to));
    }
}
