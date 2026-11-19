package com.minierp.itsm;

import com.minierp.org.OrgService;
import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Tag(name = "itsm", description = "IT 服务台：故障、服务请求、变更、CMDB、软件许可")
@RestController
@RequestMapping("/api/itsm")
@RequiredArgsConstructor
@PreAuthorize("isAuthenticated()")
public class ItsmController {
    private static final String IT = "hasAnyRole('ADMIN')";
    private final ItsmService itsm;
    private final OrgService orgs;

    public record NewTicket(String type, String category, String title, String description, Long ciId, String impact, String urgency) {
    }

    public record ResolveReq(String resolution, String rootCause) {
    }

    @Operation(summary = "提交 IT 工单（任何员工）：优先级由影响范围 × 紧急程度决定")
    @PostMapping("/tickets")
    @Audited("itsm.create")
    public Map<String, Object> create(@RequestBody NewTicket r, Authentication auth) {
        return Map.of("id", itsm.createTicket(new ItsmService.TicketReq(r.type(), r.category(), r.title(), r.description(), me(auth),
                r.ciId(), r.impact(), r.urgency()), LocalDateTime.now()));
    }

    @PostMapping("/tickets/{id}/assign")
    @PreAuthorize(IT)
    @Audited("itsm.assign")
    public void assign(@PathVariable long id, Authentication auth) {
        itsm.assign(id, me(auth), LocalDateTime.now());
    }

    @PostMapping("/tickets/{id}/resolve")
    @PreAuthorize(IT)
    @Audited("itsm.resolve")
    public void resolve(@PathVariable long id, @RequestBody ResolveReq r) {
        itsm.resolve(id, r.resolution(), r.rootCause(), LocalDateTime.now());
    }

    @PostMapping("/tickets/{id}/close")
    @Audited("itsm.close")
    public void close(@PathVariable long id, @RequestParam(required = false) Integer satisfaction) {
        itsm.close(id, satisfaction, LocalDateTime.now());
    }

    @Operation(summary = "提交变更申请（OA 流程 IT_CHANGE，高风险需总经理审批）")
    @PostMapping("/changes")
    @PreAuthorize(IT)
    @Audited("itsm.change")
    public Map<String, Object> change(@RequestBody ItsmService.ChangeReq r, Authentication auth) {
        return Map.of("id", itsm.submitChange(r, me(auth), LocalDateTime.now()));
    }

    @PostMapping("/changes/{id}/implement")
    @PreAuthorize(IT)
    @Audited("itsm.implement")
    public void implement(@PathVariable long id, @RequestParam String result) {
        itsm.implementChange(id, result, LocalDateTime.now());
    }

    @Operation(summary = "软件许可合规：席位、已用、等待分配")
    @GetMapping("/licenses")
    public List<Map<String, Object>> licenses() {
        return itsm.licenseCompliance();
    }

    @PostMapping("/licenses/{id}/seats")
    @PreAuthorize(IT)
    @Audited("itsm.addSeats")
    public Map<String, Object> addSeats(@PathVariable long id, @RequestParam int seats) {
        return Map.of("assigned", itsm.addSeats(id, seats, LocalDate.now()));
    }

    @Operation(summary = "IT 服务看板：SLA、MTTR、分类、重大故障与关联变更、CMDB、许可")
    @GetMapping("/dashboard")
    public Map<String, Object> dashboard(@RequestParam LocalDate from, @RequestParam LocalDate to) {
        return itsm.dashboard(from, to);
    }

    private long me(Authentication auth) {
        Long emp = orgs.employeeOfUser(auth.getName());
        if (emp == null) {
            throw new com.minierp.common.BizException("当前账号未关联员工档案");
        }
        return emp;
    }
}
