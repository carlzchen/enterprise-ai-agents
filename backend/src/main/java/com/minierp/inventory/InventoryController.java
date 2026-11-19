package com.minierp.inventory;

import com.minierp.system.Audited;
import jakarta.validation.constraints.NotNull;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;

@RestController
@RequestMapping("/api/inventory")
@RequiredArgsConstructor
public class InventoryController {
    private final InventoryService inventory;

    public record AdjustRequest(@NotNull String itemType, @NotNull Long itemId, @NotNull BigDecimal delta,
                                BigDecimal unitCost, String remark) {
    }

    @PostMapping("/adjust")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE')")
    @Audited("inventory.adjust")
    public void adjust(@RequestBody @jakarta.validation.Valid AdjustRequest req) {
        inventory.adjust(req.itemType(), req.itemId(), req.delta(), req.unitCost(), LocalDate.now(), req.remark());
    }
}
