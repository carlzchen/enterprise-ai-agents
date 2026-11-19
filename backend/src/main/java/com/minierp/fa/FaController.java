package com.minierp.fa;

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

@Tag(name = "fa", description = "固定资产：资本性支出审批、折旧、处置")
@RestController
@RequestMapping("/api/fa")
@RequiredArgsConstructor
public class FaController {
    private final FaService fa;
    private final OrgService orgs;

    public record CapexReq(String name, String categoryCode, String deptCode, LocalDate inServiceDate, BigDecimal cost, String reason) {
    }

    public record DisposeReq(LocalDate date, BigDecimal proceeds, BigDecimal clearingCost, String reason) {
    }

    @Operation(summary = "提交资本性支出审批（通过后自动入账建卡）")
    @PostMapping("/capex")
    @Audited("fa.submitCapex")
    public Map<String, Object> capex(@RequestBody CapexReq r, Authentication a) {
        return Map.of("wfInstanceId", fa.submitCapex(new FaService.AssetReq(r.name(), r.categoryCode(), r.deptCode(), null,
                r.inServiceDate() == null ? LocalDate.now() : r.inServiceDate(), r.cost()), orgs.employeeOfUser(a.getName()), r.reason(), LocalDateTime.now()));
    }

    @Operation(summary = "计提某期间折旧（重复执行跳过已计提资产）")
    @PostMapping("/depreciation/{period}")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("fa.depreciate")
    public Map<String, Object> depreciate(@PathVariable String period) {
        YearMonth ym = YearMonth.parse(period);
        return fa.depreciate(ym, ym.atEndOfMonth());
    }

    @PostMapping("/assets/{id}/dispose")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("fa.dispose")
    public Map<String, Object> dispose(@PathVariable long id, @RequestBody DisposeReq r) {
        BigDecimal gain = fa.dispose(id, r.date() == null ? LocalDate.now() : r.date(), r.proceeds(), r.clearingCost(), r.reason());
        return Map.of("gain", gain);
    }

    @GetMapping("/assets/{id}")
    public Map<String, Object> detail(@PathVariable long id) {
        return fa.detail(id);
    }

    @GetMapping("/summary")
    public Map<String, Object> summary() {
        return fa.summary();
    }
}
