package com.minierp.eam;

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
import java.util.List;
import java.util.Map;

@Tag(name = "eam", description = "设备管理：报修、维修、保养、可靠性")
@RestController
@RequestMapping("/api/eam")
@RequiredArgsConstructor
public class EamController {
    private static final String MAINT = "hasAnyRole('ADMIN','PRODUCTION')";
    private final EamService eam;
    private final OrgService orgs;

    public record ReportReq(long equipmentId, String faultCode, String desc) {
    }

    public record CompleteReq(String cause, String action, List<EamService.SpareReq> spares, BigDecimal laborHours) {
    }

    public record IntervalReq(int intervalDays) {
    }

    @PostMapping("/breakdowns")
    @Audited("eam.reportBreakdown")
    public Map<String, Object> report(@RequestBody ReportReq r, Authentication auth) {
        return Map.of("id", eam.reportBreakdown(r.equipmentId(), r.faultCode(), r.desc(), auth.getName(), LocalDateTime.now()));
    }

    @PostMapping("/work-orders/{id}/start")
    @PreAuthorize(MAINT)
    @Audited("eam.start")
    public void start(@PathVariable long id, Authentication auth) {
        eam.start(id, orgs.employeeOfUser(auth.getName()), LocalDateTime.now());
    }

    @PostMapping("/work-orders/{id}/complete")
    @PreAuthorize(MAINT)
    @Audited("eam.complete")
    public void complete(@PathVariable long id, @RequestBody CompleteReq r) {
        eam.complete(id, r.cause(), r.action(), r.spares(), r.laborHours(), LocalDateTime.now());
    }

    @PostMapping("/pm-plans/{id}/do")
    @PreAuthorize(MAINT)
    @Audited("eam.doPm")
    public Map<String, Object> doPm(@PathVariable long id, Authentication auth) {
        return Map.of("id", eam.doPm(id, orgs.employeeOfUser(auth.getName()), LocalDateTime.now(), List.of()));
    }

    @PostMapping("/pm-plans/{id}/interval")
    @PreAuthorize(MAINT)
    @Audited("eam.changePmInterval")
    public void interval(@PathVariable long id, @RequestBody IntervalReq r) {
        eam.changePmInterval(id, r.intervalDays());
    }

    @Operation(summary = "设备可靠性：MTBF、MTTR、固有可用度、维修记录")
    @GetMapping("/equipment/{id}/reliability")
    public Map<String, Object> reliability(@PathVariable long id, @RequestParam(required = false) LocalDate from, @RequestParam(required = false) LocalDate to) {
        LocalDate t = to == null ? LocalDate.now() : to;
        return eam.reliability(id, from == null ? t.minusYears(1) : from, t);
    }

    @GetMapping("/dashboard")
    public Map<String, Object> dashboard(@RequestParam(required = false) LocalDate from, @RequestParam(required = false) LocalDate to) {
        LocalDate t = to == null ? LocalDate.now() : to;
        return eam.dashboard(from == null ? t.minusYears(1) : from, t);
    }
}
