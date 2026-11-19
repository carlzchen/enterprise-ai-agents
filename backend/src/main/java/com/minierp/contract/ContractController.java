package com.minierp.contract;

import com.minierp.org.OrgService;
import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Tag(name = "contract", description = "合同：起草、审批、履约、到期提醒")
@RestController
@RequestMapping("/api/contracts")
@RequiredArgsConstructor
public class ContractController {
    private final ContractService contracts;
    private final OrgService orgs;

    public record TextReq(String reason) {
    }

    @PostMapping
    @Audited("contract.draft")
    public Map<String, Object> draft(@RequestBody ContractService.ContractReq r, Authentication a) {
        return contracts.detail(contracts.draft(r, orgs.employeeOfUser(a.getName()), LocalDate.now()));
    }

    @PostMapping("/{id}/submit")
    @Audited("contract.submit")
    public Map<String, Object> submit(@PathVariable long id) {
        contracts.submit(id, LocalDateTime.now());
        return contracts.detail(id);
    }

    @PostMapping("/{id}/terminate")
    @Audited("contract.terminate")
    public void terminate(@PathVariable long id, @RequestBody TextReq r) {
        contracts.terminate(id, r.reason());
    }

    @GetMapping("/{id}")
    public Map<String, Object> detail(@PathVariable long id) {
        return contracts.detail(id);
    }

    @GetMapping("/expiring")
    public List<Map<String, Object>> expiring(@RequestParam(defaultValue = "60") int days, @RequestParam(required = false) LocalDate date) {
        return contracts.expiring(date == null ? LocalDate.now() : date, days);
    }
}
