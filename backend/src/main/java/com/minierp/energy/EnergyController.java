package com.minierp.energy;

import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.EnumMap;
import java.util.Map;

@Tag(name = "energy", description = "能源管理：分时电表读数、月度电费、单位产品电耗、碳排放")
@RestController
@RequestMapping("/api/energy")
@RequiredArgsConstructor
@PreAuthorize("hasAnyRole('ADMIN','PRODUCTION','MANAGEMENT','FINANCE')")
public class EnergyController {
    private final EnergyService energy;

    public record ReadingReq(String meterCode, LocalDate date, double sharp, double peak, double flat, double valley) {
    }

    @Operation(summary = "录入 / 采集电表日读数（分时段 kWh）")
    @PostMapping("/readings")
    @Audited("energy.reading")
    public void reading(@RequestBody ReadingReq r) {
        Map<TouTariff.Band, Double> kwh = new EnumMap<>(TouTariff.Band.class);
        kwh.put(TouTariff.Band.SHARP, r.sharp());
        kwh.put(TouTariff.Band.PEAK, r.peak());
        kwh.put(TouTariff.Band.FLAT, r.flat());
        kwh.put(TouTariff.Band.VALLEY, r.valley());
        energy.record(r.meterCode(), r.date(), kwh);
    }

    @Operation(summary = "生成月度电费账单（分时电费 + 基本电费 + 水费）")
    @PostMapping("/bills/{period}")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("energy.bill")
    public Map<String, Object> bill(@PathVariable String period, @RequestParam BigDecimal waterM3) {
        return energy.bill(YearMonth.parse(period), waterM3);
    }

    @Operation(summary = "能源看板：月度账单趋势、分时结构、各电表、单位产品电耗、空压站空载基线")
    @GetMapping("/dashboard")
    public Map<String, Object> dashboard(@RequestParam String period) {
        return energy.dashboard(YearMonth.parse(period));
    }
}
