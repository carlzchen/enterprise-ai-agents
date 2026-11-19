package com.minierp.aps;

import com.minierp.common.DocNoService;
import com.minierp.masterdata.BomExploder;
import com.minierp.masterdata.BomService;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 排产（多级 BOM）：未完工工单按物料低阶码分层，从最底层组件开始逐层
 *   物料齐套检查（现存量 → 替代料 → 在途采购 → 下层组件工单的计划完工）→ 有限产能排程；
 * 下层组件工单排出的完工日作为上层工单的"在途供应"，因此上层工单不会早于其组件完工开工。
 * 成品交期取"该产品未发货订单的最早要求交期"，没有订单需求则取工单计划完工日；组件工单交期 = 上级工单交期 - 2 天。
 */
@Service
@RequiredArgsConstructor
public class ApsService {
    /** 缺料且无在途时，假定补采购需要的天数；缺半成品时按组件生产周期 */
    private static final int SHORTAGE_LEAD_DAYS = 15, SEMI_LEAD_DAYS = 3;

    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final BomService bomService;

    private record Wo(long id, String itemType, long itemId, String line, BigDecimal qty, LocalDate due, Long parentId) {
        String key() {
            return "PRODUCT".equals(itemType) ? BomExploder.product(itemId) : BomExploder.material(itemId);
        }
    }

    @Transactional
    public Map<String, Object> run(LocalDate planDate, String user) {
        Map<String, Integer> capacity = new HashMap<>();
        jdbc.query("SELECT line_code, daily_capacity * shifts FROM mes_line", rs -> {
            capacity.put(rs.getString(1), rs.getInt(2));
        });
        List<Wo> raw = jdbc.query("""
                SELECT w.id, w.item_type, COALESCE(w.product_id, w.material_id), w.line_code, (w.planned_qty - w.good_qty - w.scrap_qty) AS qty,
                       CASE WHEN w.item_type = 'PRODUCT' THEN
                         COALESCE((SELECT MIN(o.required_date) FROM sal_order o JOIN sal_order_line l ON l.order_id = o.id
                                   WHERE l.product_id = w.product_id AND o.status IN ('CONFIRMED','PARTIAL') AND l.qty > l.shipped_qty), w.plan_end)
                       ELSE w.plan_end END AS due, w.parent_wo_id
                FROM mes_work_order w WHERE w.status IN ('PLANNED','RELEASED','IN_PROGRESS') AND w.planned_qty > w.good_qty + w.scrap_qty""",
                (rs, i) -> new Wo(rs.getLong(1), rs.getString(2), rs.getLong(3), rs.getString(4), rs.getBigDecimal(5),
                        rs.getDate(6).toLocalDate(), (Long) rs.getObject(7)));
        Map<Long, Wo> byId = new HashMap<>();
        raw.forEach(w -> byId.put(w.id(), w));
        // 组件工单交期跟随上级工单
        List<Wo> wos = raw.stream().map(w -> w.parentId() != null && byId.containsKey(w.parentId())
                ? new Wo(w.id(), w.itemType(), w.itemId(), w.line(), w.qty(), min(w.due(), byId.get(w.parentId()).due().minusDays(2)), w.parentId())
                : w).toList();

        Map<String, Map<Long, BigDecimal>> structure = bomService.structure();
        Map<String, Integer> llc;
        try {
            llc = BomExploder.lowLevelCodes(structure);
        } catch (IllegalArgumentException e) {
            throw new com.minierp.common.BizException(e.getMessage());
        }
        Map<Long, BigDecimal> stock = new HashMap<>();
        jdbc.query("SELECT item_id, SUM(qty) FROM inv_stock WHERE item_type = 'MATERIAL' GROUP BY item_id", rs -> {
            stock.put(rs.getLong(1), rs.getBigDecimal(2));
        });
        List<KittingCalculator.Supply> supplies = jdbc.query("""
                SELECT l.material_id, o.expected_date, l.qty - l.received_qty FROM pur_order o JOIN pur_order_line l ON l.po_id = o.id
                WHERE o.status IN ('CONFIRMED','PARTIAL') AND l.qty > l.received_qty""",
                (rs, i) -> new KittingCalculator.Supply(rs.getLong(1), rs.getDate(2).toLocalDate(), rs.getBigDecimal(3)));
        Map<Long, List<KittingCalculator.Sub>> subs = new HashMap<>();
        bomService.allSubstitutes(planDate).forEach((mid, list) -> subs.put(mid,
                list.stream().map(x -> new KittingCalculator.Sub(x.substituteId(), x.ratio())).toList()));
        Map<Long, String> materialCodes = new HashMap<>();
        Set<Long> makeItems = new HashSet<>();
        jdbc.query("SELECT id, code, supply_type FROM md_material", rs -> {
            materialCodes.put(rs.getLong(1), rs.getString(2));
            if ("MAKE".equals(rs.getString(3))) {
                makeItems.add(rs.getLong(1));
            }
        });

        KittingCalculator.Pool pool = new KittingCalculator.Pool(stock, supplies, subs);
        Map<Long, KittingCalculator.Kit> kits = new HashMap<>();
        Map<Long, Integer> levelOf = new HashMap<>();
        List<ApsScheduler.Slot> slots = new ArrayList<>();
        Map<Integer, List<Wo>> byLevel = new TreeMap<>(Comparator.reverseOrder());
        for (Wo w : wos) {
            int level = llc.getOrDefault(w.key(), 0);
            levelOf.put(w.id(), level);
            byLevel.computeIfAbsent(level, k -> new ArrayList<>()).add(w);
        }
        for (var e : byLevel.entrySet()) {
            List<Wo> level = e.getValue();
            List<KittingCalculator.Demand> demands = level.stream().map(w -> new KittingCalculator.Demand(w.id(), w.due(),
                    structure.getOrDefault(w.key(), Map.of()).entrySet().stream()
                            .collect(Collectors.toMap(Map.Entry::getKey, x -> x.getValue().multiply(w.qty()))))).toList();
            Map<Long, KittingCalculator.Kit> levelKits = pool.check(demands, planDate, SHORTAGE_LEAD_DAYS);
            // 只缺半成品（没有采购件短缺）时，按组件生产周期而不是采购周期估计齐套日
            levelKits.replaceAll((id, k) -> "SHORTAGE".equals(k.status()) && makeItems.containsAll(k.shortMaterials())
                    ? new KittingCalculator.Kit(id, k.status(), planDate.plusDays(SEMI_LEAD_DAYS), k.shortMaterials(), k.substitutions()) : k);
            kits.putAll(levelKits);
            List<ApsScheduler.Order> orders = level.stream().map(w -> new ApsScheduler.Order(w.id(), w.line(), w.qty(), w.due(),
                    kits.get(w.id()).earliestStart())).toList();
            ApsScheduler.Result result = ApsScheduler.schedule(capacity, orders, planDate, Set.of());
            slots.addAll(result.slots());
            // 本层半成品工单的计划完工 → 上层的在途供应（完工次日可用）
            for (ApsScheduler.Slot s : result.slots()) {
                Wo w = byId.get(s.woId());
                if ("MATERIAL".equals(w.itemType())) {
                    pool.addSupply(new KittingCalculator.Supply(w.itemId(), s.end().plusDays(1), s.qty()));
                }
            }
        }

        long late = slots.stream().filter(s -> s.lateDays() > 0).count();
        String planNo = docNo.next("APS", planDate);
        jdbc.update("INSERT INTO aps_plan (plan_no, plan_date, created_by, order_count, late_count) VALUES (?,?,?,?,?)",
                planNo, Date.valueOf(planDate), user, wos.size(), late);
        Long planId = jdbc.queryForObject("SELECT id FROM aps_plan WHERE plan_no = ?", Long.class, planNo);
        jdbc.batchUpdate("""
                INSERT INTO aps_plan_item (plan_id, wo_id, line_code, seq, qty, start_date, end_date, due_date, late_days,
                  kit_status, earliest_start, shortage, bom_level, substitution) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                slots.stream().map(s -> {
                    KittingCalculator.Kit k = kits.get(s.woId());
                    String shortage = k.shortMaterials().isEmpty() ? null : k.shortMaterials().stream()
                            .map(m -> materialCodes.get(m) + (makeItems.contains(m) ? "（半成品，需下达组件工单）" : "（无在途）"))
                            .collect(Collectors.joining("、", "缺料：", ""));
                    String substitution = k.substitutions().isEmpty() ? null : k.substitutions().entrySet().stream()
                            .map(x -> materialCodes.get(x.getKey()) + " × " + x.getValue().stripTrailingZeros().toPlainString())
                            .collect(Collectors.joining("、", "替代料：", ""));
                    return new Object[]{planId, s.woId(), s.line(), s.seq(), s.qty(), Date.valueOf(s.start()), Date.valueOf(s.end()),
                            Date.valueOf(s.due()), s.lateDays(), k.status(), k.earliestStart() == null ? null : Date.valueOf(k.earliestStart()),
                            shortage, levelOf.get(s.woId()), substitution};
                }).toList());
        return latest();
    }

    private static LocalDate min(LocalDate a, LocalDate b) {
        return a.isBefore(b) ? a : b;
    }

    public Map<String, Object> latest() {
        List<Map<String, Object>> plans = jdbc.queryForList("SELECT * FROM aps_plan ORDER BY id DESC LIMIT 1");
        if (plans.isEmpty()) {
            return Map.of();
        }
        Map<String, Object> plan = new LinkedHashMap<>(plans.get(0));
        Object id = plan.get("id");
        plan.put("items", jdbc.queryForList("""
                SELECT i.*, w.wo_no, w.item_type, COALESCE(p.code, m.code) AS product_code, COALESCE(p.name, m.name) AS product_name,
                       pw.wo_no AS parent_wo_no
                FROM aps_plan_item i JOIN mes_work_order w ON w.id = i.wo_id
                LEFT JOIN md_product p ON p.id = w.product_id LEFT JOIN md_material m ON m.id = w.material_id
                LEFT JOIN mes_work_order pw ON pw.id = w.parent_wo_id
                WHERE i.plan_id = ? ORDER BY i.line_code, i.seq""", id));
        plan.put("lines", jdbc.queryForList("SELECT line_code, name, daily_capacity * shifts AS daily_capacity, shifts FROM mes_line ORDER BY line_code"));
        return plan;
    }
}
