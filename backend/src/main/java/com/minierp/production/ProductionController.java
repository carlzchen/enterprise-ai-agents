package com.minierp.production;

import com.minierp.system.Audited;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;

@RestController
@RequestMapping("/api/production")
@RequiredArgsConstructor
public class ProductionController {
    private final ProductionService production;

    public record CreateReq(@NotNull Long productId, @NotNull BigDecimal qty,
                            @NotNull LocalDate planStart, @NotNull LocalDate planEnd) {
    }

    public record ReportReq(LocalDate date, BigDecimal goodQty, BigDecimal scrapQty, String defectCode, String operator) {
    }

    @PostMapping("/work-orders")
    @PreAuthorize("hasAnyRole('ADMIN','PRODUCTION')")
    @Audited("production.create")
    public WorkOrder create(@RequestBody @Valid CreateReq req) {
        return production.create(req.productId(), req.qty(), req.planStart(), req.planEnd());
    }

    @PostMapping("/work-orders/{id}/release")
    @PreAuthorize("hasAnyRole('ADMIN','PRODUCTION')")
    @Audited("production.release")
    public WorkOrder release(@PathVariable Long id) {
        return production.release(id);
    }

    @PostMapping("/work-orders/{id}/report")
    @PreAuthorize("hasAnyRole('ADMIN','PRODUCTION')")
    @Audited("production.report")
    public WoReport report(@PathVariable Long id, @RequestBody ReportReq req) {
        return production.report(id, req.date() == null ? LocalDate.now() : req.date(),
                req.goodQty(), req.scrapQty(), req.defectCode(), req.operator());
    }

    @PostMapping("/work-orders/{id}/close")
    @PreAuthorize("hasAnyRole('ADMIN','PRODUCTION')")
    @Audited("production.close")
    public WorkOrder close(@PathVariable Long id) {
        return production.close(id, LocalDate.now());
    }
}
