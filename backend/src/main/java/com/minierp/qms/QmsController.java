package com.minierp.qms;

import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@Tag(name = "qms", description = "质量：8D、SPC、量具校准、OQC 抽样方案")
@RestController
@RequestMapping("/api/qms")
@RequiredArgsConstructor
public class QmsController {
    private final QmsService qms;
    private final JdbcTemplate jdbc;

    public record StepReq(int step, String content) {
    }

    public record CalReq(String result, String deviation) {
    }

    @GetMapping("/8d/{id}")
    public Map<String, Object> eightD(@PathVariable long id) {
        return jdbc.queryForMap("SELECT * FROM v_8d WHERE id = ?", id);
    }

    @PostMapping("/8d/{id}/step")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY')")
    @Audited("qms.update8d")
    public void step(@PathVariable long id, @RequestBody StepReq r) {
        qms.update8dStep(id, r.step(), r.content(), LocalDate.now());
    }

    @Operation(summary = "SPC 均值-极差图与 Cpk：characteristic = back_emf / no_load_current / encoder_offset / insulation_mohm / vibration")
    @GetMapping("/spc")
    public Map<String, Object> spc(@RequestParam String product, @RequestParam String characteristic,
                                   @RequestParam LocalDate from, @RequestParam LocalDate to, @RequestParam(defaultValue = "5") int n) {
        return qms.spc(product, characteristic, from, to, n);
    }

    @GetMapping("/spc/cpk-trend")
    public List<Map<String, Object>> cpkTrend(@RequestParam String product, @RequestParam String characteristic,
                                              @RequestParam LocalDate from, @RequestParam LocalDate to) {
        return qms.cpkTrend(product, characteristic, from, to);
    }

    @GetMapping("/spc/products")
    public List<Map<String, Object>> spcProducts() {
        return jdbc.queryForList("""
                SELECT p.code, p.name, s.characteristic, s.name AS char_name, s.unit, s.nominal, s.lsl, s.usl
                FROM mes_test_spec s JOIN md_product p ON p.id = s.product_id ORDER BY p.code, s.id""");
    }

    @PostMapping("/gauges/{id}/calibrate")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY')")
    @Audited("qms.calibrate")
    public void calibrate(@PathVariable long id, @RequestBody CalReq r, Authentication auth) {
        String name = jdbc.queryForObject("SELECT display_name FROM sys_user WHERE username = ?", String.class, auth.getName());
        qms.calibrate(id, LocalDate.now(), r.result(), r.deviation(), name);
    }

    @Operation(summary = "GB/T 2828.1 抽样方案查询（一般检验水平 II，正常一次抽样）")
    @GetMapping("/aql-plan")
    public AqlSampling.Plan plan(@RequestParam int lotSize, @RequestParam double aql) {
        return AqlSampling.plan(lotSize, aql);
    }
}
