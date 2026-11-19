package com.minierp.crm;

import com.minierp.system.Audited;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/crm")
@RequiredArgsConstructor
public class CrmController {
    private final CrmService crm;

    public record OppReq(Long customerId, String title, String series, BigDecimal amount, LocalDate expectedClose) {
    }

    public record AdvanceReq(String target, String lostReason) {
    }

    public record ActivityReq(Long opportunityId, Long customerId, String type, String subject) {
    }

    public record ContactReq(Long customerId, String name, String title, String phone, String email, boolean primary) {
    }

    @PostMapping("/opportunities")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("crm.createOpportunity")
    public Map<String, Object> create(@RequestBody OppReq r, Authentication auth) {
        return Map.of("id", crm.create(r.customerId(), r.title(), r.series(), r.amount(),
                r.expectedClose() == null ? LocalDate.now().plusMonths(3) : r.expectedClose(), auth.getName(), LocalDate.now()));
    }

    @PostMapping("/opportunities/{id}/advance")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("crm.advance")
    public void advance(@PathVariable Long id, @RequestBody AdvanceReq r) {
        crm.advance(id, r.target(), r.lostReason(), LocalDate.now());
    }

    @PostMapping("/activities")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("crm.activity")
    public void activity(@RequestBody ActivityReq r, Authentication auth) {
        crm.activity(r.opportunityId(), r.customerId(), r.type(), r.subject(), auth.getName(), LocalDate.now());
    }

    @PostMapping("/contacts")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("crm.contact")
    public void contact(@RequestBody ContactReq r) {
        crm.contact(r.customerId(), r.name(), r.title(), r.phone(), r.email(), r.primary());
    }

    @GetMapping("/funnel")
    public List<Map<String, Object>> funnel() {
        return crm.funnel();
    }
}
