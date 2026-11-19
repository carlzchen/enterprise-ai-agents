package com.minierp.customs;

import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.YearMonth;
import java.util.Map;

@Tag(name = "customs", description = "关务与增值税：报关、免抵退税月结、缴税与退税到账")
@RestController
@RequestMapping("/api/customs")
@RequiredArgsConstructor
public class CustomsController {
    private final CustomsService customs;
    private final JdbcTemplate jdbc;

    public record DeclareReq(long deliveryId, String port) {
    }

    @PostMapping("/declarations")
    @PreAuthorize("hasAnyRole('ADMIN','SALES','WAREHOUSE')")
    @Audited("customs.declare")
    public Map<String, Object> declare(@RequestBody DeclareReq r) {
        long id = customs.declare(r.deliveryId(), LocalDate.now(), r.port() == null ? "深圳蛇口" : r.port());
        return jdbc.queryForMap("SELECT * FROM v_declaration WHERE id = ?", id);
    }

    @PostMapping("/declarations/{id}/release")
    @PreAuthorize("hasAnyRole('ADMIN','SALES','WAREHOUSE')")
    @Audited("customs.release")
    public void release(@PathVariable long id) {
        customs.release(id, LocalDate.now());
    }

    @PostMapping("/declarations/{id}/export")
    @PreAuthorize("hasAnyRole('ADMIN','SALES','WAREHOUSE')")
    @Audited("customs.export")
    public void export(@PathVariable long id) {
        customs.export(id, LocalDate.now());
    }

    @GetMapping("/declarations/{id}/lines")
    public java.util.List<Map<String, Object>> lines(@PathVariable long id) {
        return jdbc.queryForList("SELECT l.*, p.code AS product_code FROM cu_declaration_line l JOIN md_product p ON p.id = l.product_id WHERE l.decl_id = ?", id);
    }

    @Operation(summary = "增值税月结：生产企业免抵退计算、结转未交增值税、计提附加税")
    @PostMapping("/vat/{period}")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("customs.vatReturn")
    public Map<String, Object> vat(@PathVariable String period) {
        YearMonth ym = YearMonth.parse(period);
        return customs.vatReturn(ym, ym.atEndOfMonth());
    }

    @PostMapping("/vat/{period}/pay")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("customs.payVat")
    public Map<String, Object> pay(@PathVariable String period) {
        return Map.of("amount", customs.payVat(period, LocalDate.now()));
    }

    @PostMapping("/vat/{period}/refund")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("customs.receiveRefund")
    public void refund(@PathVariable String period) {
        customs.receiveRefund(period, LocalDate.now());
    }
}
