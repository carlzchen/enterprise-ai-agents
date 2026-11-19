package com.minierp.srm;

import com.minierp.purchase.PurchaseOrder;
import com.minierp.system.Audited;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/srm")
@RequiredArgsConstructor
public class SrmController {
    private final SrmService srm;

    public record RfqReq(Long materialId, BigDecimal qty, LocalDate needDate) {
    }

    public record QuoteReq(Long supplierId, BigDecimal unitPrice, Integer leadDays, String remark) {
    }

    public record AwardReq(Long supplierId, String reason) {
    }

    @PostMapping("/rfqs")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("srm.createRfq")
    public Map<String, Object> create(@RequestBody RfqReq r, Authentication auth) {
        return Map.of("id", srm.createRfq(r.materialId(), r.qty(), r.needDate(), LocalDate.now(), auth.getName()));
    }

    @PostMapping("/rfqs/{id}/quotes")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("srm.quote")
    public void quote(@PathVariable Long id, @RequestBody QuoteReq q) {
        srm.quote(id, q.supplierId(), q.unitPrice(), q.leadDays() == null ? 0 : q.leadDays(), LocalDate.now(), q.remark());
    }

    @GetMapping("/rfqs/{id}/compare")
    public List<Map<String, Object>> compare(@PathVariable Long id) {
        return srm.compare(id);
    }

    @PostMapping("/rfqs/{id}/award")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("srm.award")
    public PurchaseOrder award(@PathVariable Long id, @RequestBody AwardReq a, Authentication auth) {
        return srm.award(id, a.supplierId(), a.reason(), LocalDate.now(), auth.getName());
    }

    @PostMapping("/scores/compute")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("srm.computeScores")
    public Map<String, Object> compute(@RequestParam String month) {
        return Map.of("suppliers", srm.computeMonthly(YearMonth.parse(month)));
    }
}
