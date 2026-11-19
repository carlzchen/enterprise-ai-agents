package com.minierp.masterdata;

import com.minierp.common.BizException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.*;

/** 多级 BOM（成品 BOM md_bom + 半成品 BOM md_material_bom）与替代料 */
@Service
@RequiredArgsConstructor
public class BomService {
    private final JdbcTemplate jdbc;
    private final MasterDataService md;

    public record Component(long materialId, BigDecimal qtyPer, boolean make) {
    }

    public record Substitute(long substituteId, BigDecimal ratio, int priority) {
    }

    /** 某个成品或半成品的直接下层组件 */
    public List<Component> componentsOf(String itemType, long itemId) {
        String sql = "PRODUCT".equals(itemType)
                ? "SELECT b.material_id, b.qty_per, m.supply_type FROM md_bom b JOIN md_material m ON m.id = b.material_id WHERE b.product_id = ? ORDER BY b.id"
                : "SELECT b.child_material_id, b.qty_per, m.supply_type FROM md_material_bom b JOIN md_material m ON m.id = b.child_material_id WHERE b.parent_material_id = ? ORDER BY b.id";
        return jdbc.query(sql, (rs, i) -> new Component(rs.getLong(1), rs.getBigDecimal(2), "MAKE".equals(rs.getString(3))), itemId);
    }

    /** 全部 BOM 结构：节点键 P:id / M:id → (子物料 → 用量) */
    public Map<String, Map<Long, BigDecimal>> structure() {
        Map<String, Map<Long, BigDecimal>> s = new HashMap<>();
        jdbc.query("SELECT product_id, material_id, qty_per FROM md_bom", rs -> {
            s.computeIfAbsent(BomExploder.product(rs.getLong(1)), k -> new LinkedHashMap<>()).put(rs.getLong(2), rs.getBigDecimal(3));
        });
        jdbc.query("SELECT parent_material_id, child_material_id, qty_per FROM md_material_bom", rs -> {
            s.computeIfAbsent(BomExploder.material(rs.getLong(1)), k -> new LinkedHashMap<>()).put(rs.getLong(2), rs.getBigDecimal(3));
        });
        return s;
    }

    /** 生效的替代料，按优先级排序 */
    public List<Substitute> substitutesOf(long materialId, LocalDate date) {
        return jdbc.query("""
                SELECT substitute_id, ratio, priority FROM md_material_substitute
                WHERE material_id = ? AND (valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR valid_to >= ?)
                ORDER BY priority, id""", (rs, i) -> new Substitute(rs.getLong(1), rs.getBigDecimal(2), rs.getInt(3)), materialId, date, date);
    }

    public Map<Long, List<Substitute>> allSubstitutes(LocalDate date) {
        Map<Long, List<Substitute>> m = new HashMap<>();
        jdbc.query("""
                SELECT material_id, substitute_id, ratio, priority FROM md_material_substitute
                WHERE (valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR valid_to >= ?) ORDER BY priority, id""", rs -> {
            m.computeIfAbsent(rs.getLong(1), k -> new ArrayList<>()).add(new Substitute(rs.getLong(2), rs.getBigDecimal(3), rs.getInt(4)));
        }, date, date);
        return m;
    }

    /** 多级 BOM 展开树（前端树形表格），含每层物料参考成本与现存量 */
    public Map<String, Object> tree(long productId, BigDecimal qty) {
        Product p = md.product(productId);
        Map<String, Map<Long, BigDecimal>> s = structure();
        List<BomExploder.Node> nodes;
        try {
            nodes = BomExploder.explode(BomExploder.product(productId), qty, s);
        } catch (IllegalArgumentException e) {
            throw new BizException(e.getMessage());
        }
        Map<Long, Map<String, Object>> info = new HashMap<>();
        jdbc.query("""
                SELECT m.id, m.code, m.name, m.uom, m.category, m.supply_type, m.line_code,
                       (SELECT COALESCE(SUM(st.qty), 0) FROM inv_stock st WHERE st.item_type = 'MATERIAL' AND st.item_id = m.id) AS stock,
                       (SELECT COUNT(*) FROM md_material_substitute x WHERE x.material_id = m.id) AS subs
                FROM md_material m""", rs -> {
            Map<String, Object> r = new HashMap<>();
            r.put("code", rs.getString(2));
            r.put("name", rs.getString(3));
            r.put("uom", rs.getString(4));
            r.put("category", rs.getString(5));
            r.put("supplyType", rs.getString(6));
            r.put("lineCode", rs.getString(7));
            r.put("stock", rs.getBigDecimal(8));
            r.put("substitutes", rs.getInt(9));
            info.put(rs.getLong(1), r);
        });
        LocalDate today = LocalDate.now();
        // explode 是深度优先顺序：用"各层最近一个节点的子列表"栈挂接父子关系
        List<Map<String, Object>> roots = new ArrayList<>();
        Map<Integer, List<Map<String, Object>>> childrenAt = new HashMap<>();
        childrenAt.put(0, roots);
        int seq = 0;
        for (BomExploder.Node n : nodes) {
            long mid = Long.parseLong(n.key().substring(2));
            Map<String, Object> row = new LinkedHashMap<>(info.get(mid));
            row.put("key", n.key() + "#" + (seq++));
            row.put("level", n.level());
            row.put("qtyPer", n.qtyPer());
            row.put("totalQty", n.totalQty().setScale(4, java.math.RoundingMode.HALF_UP));
            BigDecimal price = md.materialPrice(mid, today);
            row.put("unitCost", price);
            row.put("extCost", n.leaf() ? price.multiply(n.totalQty()).setScale(2, java.math.RoundingMode.HALF_UP) : null);
            List<Map<String, Object>> kids = new ArrayList<>();
            if (!n.leaf()) {
                row.put("children", kids);
            }
            childrenAt.get(n.level() - 1).add(row);
            childrenAt.put(n.level(), kids);
        }
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("productCode", p.getCode());
        r.put("productName", p.getName());
        r.put("qty", qty);
        r.put("levels", nodes.stream().mapToInt(BomExploder.Node::level).max().orElse(0));
        r.put("materialCost", nodes.stream().filter(BomExploder.Node::leaf)
                .map(n -> md.materialPrice(Long.parseLong(n.key().substring(2)), today).multiply(n.totalQty()))
                .reduce(BigDecimal.ZERO, BigDecimal::add).setScale(2, java.math.RoundingMode.HALF_UP));
        r.put("tree", roots);
        return r;
    }

    // ---------- 维护 ----------

    @Transactional
    public void saveSubstitute(long materialId, long substituteId, BigDecimal ratio, int priority, LocalDate validFrom, LocalDate validTo, String reason) {
        if (materialId == substituteId) {
            throw new BizException("替代料不能是主料本身");
        }
        if (ratio == null || ratio.signum() <= 0) {
            throw new BizException("替代比例必须大于 0");
        }
        md.material(materialId);
        md.material(substituteId);
        jdbc.update("""
                INSERT INTO md_material_substitute (material_id, substitute_id, ratio, priority, valid_from, valid_to, reason) VALUES (?,?,?,?,?,?,?)
                ON DUPLICATE KEY UPDATE ratio = VALUES(ratio), priority = VALUES(priority), valid_from = VALUES(valid_from),
                  valid_to = VALUES(valid_to), reason = VALUES(reason)""", materialId, substituteId, ratio, priority, validFrom, validTo, reason);
    }

    @Transactional
    public void deleteSubstitute(long id) {
        jdbc.update("DELETE FROM md_material_substitute WHERE id = ?", id);
    }
}
