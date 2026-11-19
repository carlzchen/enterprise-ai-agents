package com.minierp.masterdata;

import com.minierp.system.Audited;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/md")
@RequiredArgsConstructor
public class MasterDataController {
    private static final String EDITORS = "hasAnyRole('ADMIN','SALES','PURCHASE','PRODUCTION')";
    private final MasterDataService md;
    private final BomService bomService;

    public record SubstituteReq(@jakarta.validation.constraints.NotNull Long materialId, @jakarta.validation.constraints.NotNull Long substituteId,
                                java.math.BigDecimal ratio, Integer priority, java.time.LocalDate validFrom, java.time.LocalDate validTo, String reason) {
    }

    /** 多级 BOM 展开 */
    @GetMapping("/products/{id}/bom-tree")
    public Map<String, Object> bomTree(@PathVariable Long id, @RequestParam(defaultValue = "1") java.math.BigDecimal qty) {
        return bomService.tree(id, qty);
    }

    @PostMapping("/substitutes")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER','PURCHASE','PRODUCTION')")
    @Audited("md.saveSubstitute")
    public void saveSubstitute(@RequestBody @jakarta.validation.Valid SubstituteReq r) {
        bomService.saveSubstitute(r.materialId(), r.substituteId(), r.ratio() == null ? java.math.BigDecimal.ONE : r.ratio(),
                r.priority() == null ? 1 : r.priority(), r.validFrom(), r.validTo(), r.reason());
    }

    @DeleteMapping("/substitutes/{id}")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER')")
    @Audited("md.deleteSubstitute")
    public void deleteSubstitute(@PathVariable Long id) {
        bomService.deleteSubstitute(id);
    }

    @GetMapping("/options")
    public Map<String, List<Map<String, Object>>> options() {
        return md.options();
    }

    @PostMapping("/products")
    @PreAuthorize(EDITORS)
    @Audited("md.saveProduct")
    public Product saveProduct(@RequestBody Product p) {
        return md.saveProduct(p);
    }

    @PostMapping("/materials")
    @PreAuthorize(EDITORS)
    @Audited("md.saveMaterial")
    public Material saveMaterial(@RequestBody Material m) {
        return md.saveMaterial(m);
    }

    @PostMapping("/customers")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("md.saveCustomer")
    public Customer saveCustomer(@RequestBody Customer c) {
        return md.saveCustomer(c);
    }

    @PostMapping("/suppliers")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("md.saveSupplier")
    public Supplier saveSupplier(@RequestBody Supplier s) {
        return md.saveSupplier(s);
    }

    // BOM 只读：修改必须走 PLM 工程变更（ECN），见 PlmController
    @GetMapping("/products/{id}/bom")
    public List<BomItem> bom(@PathVariable Long id) {
        return md.bomOf(id);
    }

}
