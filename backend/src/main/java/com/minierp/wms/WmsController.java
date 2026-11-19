package com.minierp.wms;

import com.minierp.system.Audited;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/wms")
@RequiredArgsConstructor
public class WmsController {
    private final WmsService wms;

    public record MoveReq(String itemType, Long itemId, Long fromLocationId, Long toLocationId, BigDecimal qty) {
    }

    @PostMapping("/move")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE')")
    @Audited("wms.move")
    public void move(@RequestBody MoveReq r) {
        wms.move(r.itemType(), r.itemId(), r.fromLocationId(), r.toLocationId(), r.qty(), LocalDate.now());
    }

    @GetMapping("/reconcile")
    public List<Map<String, Object>> reconcile() {
        return wms.reconcile();
    }
}
