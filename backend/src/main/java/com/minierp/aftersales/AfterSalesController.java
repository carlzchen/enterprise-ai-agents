package com.minierp.aftersales;

import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.Map;

@Tag(name = "aftersales", description = "售后服务：保修查询、服务工单、RMA、质量保证金")
@RestController
@RequestMapping("/api/aftersales")
@RequiredArgsConstructor
@PreAuthorize("hasAnyRole('ADMIN','SALES','QUALITY','MANAGEMENT','FINANCE')")
public class AfterSalesController {
    private final AfterSalesService svc;

    public record ResolveReq(String resolution, String note, BigDecimal fee) {
    }

    public record InspectReq(String disposition, String finding) {
    }

    @Operation(summary = "按序列号查询保修状态、生产与发货信息、历史工单")
    @GetMapping("/warranty/{sn}")
    public Map<String, Object> warranty(@PathVariable String sn) {
        return svc.warranty(sn, LocalDate.now());
    }

    @Operation(summary = "新建服务工单（有序列号时自动带出客户、产品、保修状态）")
    @PostMapping("/tickets")
    @Audited("aftersales.createTicket")
    public Map<String, Object> create(@RequestBody AfterSalesService.TicketReq r) {
        return Map.of("id", svc.createTicket(r, LocalDateTime.now()));
    }

    @PostMapping("/tickets/{id}/assign")
    @Audited("aftersales.assign")
    public void assign(@PathVariable long id, @RequestParam long engineerEmpId) {
        svc.assign(id, engineerEmpId, LocalDateTime.now());
    }

    @Operation(summary = "处理完成：REMOTE / ONSITE / NO_FAULT 直接解决；RMA 开具退货授权")
    @PostMapping("/tickets/{id}/resolve")
    @Audited("aftersales.resolve")
    public Map<String, Object> resolve(@PathVariable long id, @RequestBody ResolveReq r) {
        Long rma = svc.resolve(id, r.resolution(), r.note(), r.fee(), LocalDateTime.now());
        return rma == null ? Map.of() : Map.of("rmaId", rma);
    }

    @PostMapping("/tickets/{id}/close")
    @Audited("aftersales.close")
    public void close(@PathVariable long id, @RequestParam(required = false) Integer satisfaction) {
        svc.close(id, satisfaction, LocalDateTime.now());
    }

    @PostMapping("/rma/{id}/receive")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY','WAREHOUSE')")
    @Audited("aftersales.rmaReceive")
    public void receive(@PathVariable long id) {
        svc.receiveRma(id, LocalDate.now());
    }

    @Operation(summary = "RMA 检验处置：REPLACE 换新 / REPAIR 维修 / NO_FAULT 无故障退回")
    @PostMapping("/rma/{id}/inspect")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY')")
    @Audited("aftersales.rmaInspect")
    public Map<String, Object> inspect(@PathVariable long id, @RequestBody InspectReq r) {
        return svc.inspectRma(id, r.disposition(), r.finding(), LocalDate.now());
    }

    @Operation(summary = "月末计提质量保证金")
    @PostMapping("/provision/{period}")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("aftersales.accrue")
    public Map<String, Object> accrue(@PathVariable String period) {
        return Map.of("amount", svc.accrue(YearMonth.parse(period), LocalDate.now()));
    }

    @Operation(summary = "售后看板：SLA 达成、一次修复率、满意度、RMA、质保金、伺服按生产月的现场故障率")
    @GetMapping("/dashboard")
    public Map<String, Object> dashboard(@RequestParam LocalDate from, @RequestParam LocalDate to) {
        return svc.dashboard(from, to);
    }
}
