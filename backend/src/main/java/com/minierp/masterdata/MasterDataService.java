package com.minierp.masterdata;

import com.minierp.common.BizException;
import com.minierp.common.NotFoundException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

@Service
@RequiredArgsConstructor
public class MasterDataService {
    private final ProductRepository products;
    private final MaterialRepository materials;
    private final CustomerRepository customers;
    private final SupplierRepository suppliers;
    private final BomItemRepository bom;
    private final MaterialPriceRepository prices;
    private final FxRateRepository fx;
    private final WarehouseRepository warehouses;
    private final JdbcTemplate jdbc;

    private final Map<String, Long> warehouseIds = new ConcurrentHashMap<>();

    // ---------- 查询辅助 ----------

    public Product product(Long id) {
        return products.findById(id).orElseThrow(() -> new NotFoundException("产品", id));
    }

    public Material material(Long id) {
        return materials.findById(id).orElseThrow(() -> new NotFoundException("物料", id));
    }

    public Customer customer(Long id) {
        return customers.findById(id).orElseThrow(() -> new NotFoundException("客户", id));
    }

    public Supplier supplier(Long id) {
        return suppliers.findById(id).orElseThrow(() -> new NotFoundException("供应商", id));
    }

    public List<BomItem> bomOf(Long productId) {
        return bom.findByProductId(productId);
    }

    /** 仓库类型 RAW / SEMI / FG 对应的仓库 id（前置仓 DC 有多个，按编码取） */
    public Long warehouseId(String type) {
        return warehouseIds.computeIfAbsent(type, t -> warehouses.findByType(t)
                .orElseThrow(() -> new NotFoundException("仓库", t)).getId());
    }

    /** 记账汇率：取业务日期所在月（或之前最近月）的汇率 */
    public BigDecimal fxRate(String currency, LocalDate date) {
        if ("CNY".equals(currency)) {
            return BigDecimal.ONE;
        }
        return fx.findTopByCurrencyAndRateMonthLessThanEqualOrderByRateMonthDesc(currency, date.withDayOfMonth(1))
                .map(FxRate::getRate)
                .orElseThrow(() -> new BizException("缺少汇率：" + currency + " " + date));
    }

    /** 物料参考采购价：取业务日期所在月（或之前最近月）；自制半成品按下层物料参考价 + 加工费逐层卷积 */
    public BigDecimal materialPrice(Long materialId, LocalDate date) {
        var price = prices.findTopByMaterialIdAndPriceMonthLessThanEqualOrderByPriceMonthDesc(materialId, date.withDayOfMonth(1))
                .map(MaterialPrice::getPrice);
        if (price.isPresent()) {
            return price.get();
        }
        Material m = material(materialId);
        if (!"MAKE".equals(m.getSupplyType())) {
            throw new BizException("缺少物料参考价：" + m.getCode());
        }
        BigDecimal cost = m.getConversionCost() == null ? BigDecimal.ZERO : m.getConversionCost();
        for (var c : jdbc.queryForList("SELECT child_material_id, qty_per FROM md_material_bom WHERE parent_material_id = ?", materialId)) {
            cost = cost.add(((BigDecimal) c.get("qty_per")).multiply(materialPrice(((Number) c.get("child_material_id")).longValue(), date)));
        }
        return cost.setScale(4, java.math.RoundingMode.HALF_UP);
    }

    /** 下拉选项 */
    public Map<String, List<Map<String, Object>>> options() {
        return Map.of(
                "customers", jdbc.queryForList("SELECT id, code, name, currency, is_domestic AS isDomestic FROM md_customer WHERE status='ACTIVE' ORDER BY code"),
                "suppliers", jdbc.queryForList("SELECT id, code, name, category FROM md_supplier WHERE status='ACTIVE' ORDER BY code"),
                "products", jdbc.queryForList("SELECT id, code, name, series, list_price AS listPrice FROM md_product WHERE status='ACTIVE' ORDER BY code"),
                "materials", jdbc.queryForList("SELECT m.id, m.code, m.name, m.uom, m.category, m.supply_type AS supplyType, m.latest_price AS latestPrice FROM v_material m ORDER BY m.code"),
                "warehouses", jdbc.queryForList("SELECT id, code, name, type, region FROM md_warehouse ORDER BY id"));
    }

    // ---------- 维护 ----------

    @Transactional
    public Product saveProduct(Product p) {
        if (p.getId() == null && products.existsByCode(p.getCode())) {
            throw new BizException("产品编码已存在：" + p.getCode());
        }
        return products.save(p);
    }

    @Transactional
    public Material saveMaterial(Material m) {
        if (m.getId() == null && materials.existsByCode(m.getCode())) {
            throw new BizException("物料编码已存在：" + m.getCode());
        }
        return materials.save(m);
    }

    @Transactional
    public Customer saveCustomer(Customer c) {
        if (c.getId() == null && customers.existsByCode(c.getCode())) {
            throw new BizException("客户编码已存在：" + c.getCode());
        }
        return customers.save(c);
    }

    @Transactional
    public Supplier saveSupplier(Supplier s) {
        if (s.getId() == null && suppliers.existsByCode(s.getCode())) {
            throw new BizException("供应商编码已存在：" + s.getCode());
        }
        return suppliers.save(s);
    }

    public record BomLine(Long materialId, BigDecimal qtyPer) {
    }

    @Transactional
    public void replaceBom(Long productId, List<BomLine> lines) {
        product(productId);
        bom.deleteByProductId(productId);
        bom.flush();
        for (BomLine l : lines) {
            if (l.qtyPer() == null || l.qtyPer().signum() <= 0) {
                throw new BizException("BOM 用量必须大于 0");
            }
            material(l.materialId());
            BomItem b = new BomItem();
            b.setProductId(productId);
            b.setMaterialId(l.materialId());
            b.setQtyPer(l.qtyPer());
            bom.save(b);
        }
    }
}
