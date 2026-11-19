package com.minierp.ehs;

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

@Tag(name = "ehs", description = "安全环保：隐患排查、事故事件、危险作业许可、危废台账")
@RestController
@RequestMapping("/api/ehs")
@RequiredArgsConstructor
@PreAuthorize("isAuthenticated()")
public class EhsController {
    private static final String EHS = "hasAnyRole('ADMIN','PRODUCTION','MANAGEMENT')";
    private final EhsService ehs;
    private final OrgService orgs;

    /** 随手拍：只需选部门，整改责任人默认为部门负责人 */
    public record HazardReq(String source, String deptCode, String location, String category, String description, String level) {
    }

    public record CloseReq(String rootCause, String action) {
    }

    @Operation(summary = "上报隐患（随手拍 / 检查），按等级自动确定整改期限")
    @PostMapping("/hazards")
    @Audited("ehs.hazard")
    public Map<String, Object> hazard(@RequestBody HazardReq r, Authentication auth) {
        long[] dept = ehs.deptAndHead(r.deptCode());
        return Map.of("id", ehs.reportHazard(new EhsService.HazardReq(r.source() == null ? "REPORT" : r.source(), dept[0], r.location(), r.category(),
                r.description(), r.level(), LocalDate.now(), me(auth), dept[1])));
    }

    @PostMapping("/hazards/{id}/rectify")
    @Audited("ehs.rectify")
    public void rectify(@PathVariable long id, @RequestParam String action) {
        ehs.rectify(id, action, LocalDate.now());
    }

    @PostMapping("/hazards/{id}/verify")
    @PreAuthorize(EHS)
    @Audited("ehs.verify")
    public void verify(@PathVariable long id, @RequestParam(defaultValue = "true") boolean pass) {
        ehs.verify(id, pass, LocalDate.now());
    }

    @PostMapping("/incidents")
    @Audited("ehs.incident")
    public Map<String, Object> incident(@RequestBody EhsService.IncidentReq r) {
        return Map.of("id", ehs.reportIncident(r));
    }

    @PostMapping("/incidents/{id}/close")
    @PreAuthorize(EHS)
    @Audited("ehs.closeIncident")
    public void closeIncident(@PathVariable long id, @RequestBody CloseReq r) {
        ehs.closeIncident(id, r.rootCause(), r.action(), LocalDate.now());
    }

    @Operation(summary = "申请危险作业许可（OA 流程 EHS_PERMIT）")
    @PostMapping("/permits")
    @Audited("ehs.permit")
    public Map<String, Object> permit(@RequestBody EhsService.PermitReq r, Authentication auth) {
        return Map.of("id", ehs.applyPermit(r, me(auth), LocalDateTime.now()));
    }

    @PostMapping("/permits/{id}/close")
    @Audited("ehs.closePermit")
    public void closePermit(@PathVariable long id) {
        ehs.closePermit(id, LocalDateTime.now());
    }

    @Operation(summary = "危废贮存情况：在库量、最早一批贮存天数（>270 天预警，>365 天违规）")
    @GetMapping("/waste")
    public List<Map<String, Object>> waste(@RequestParam(required = false) LocalDate asOf) {
        return ehs.wasteSummary(asOf == null ? LocalDate.now() : asOf);
    }

    @Operation(summary = "安环看板：LTIFR / TRIR / 严重率、事故金字塔、隐患整改、作业票、危废")
    @GetMapping("/dashboard")
    public Map<String, Object> dashboard(@RequestParam LocalDate from, @RequestParam LocalDate to) {
        return ehs.dashboard(from, to);
    }

    private long me(Authentication auth) {
        Long emp = orgs.employeeOfUser(auth.getName());
        if (emp == null) {
            throw new com.minierp.common.BizException("当前账号未关联员工档案");
        }
        return emp;
    }
}
