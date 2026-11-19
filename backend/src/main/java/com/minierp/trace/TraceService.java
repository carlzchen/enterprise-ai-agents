package com.minierp.trace;

import com.minierp.common.NotFoundException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.*;

/**
 * 质量追溯
 *  反向：发货单 / 客诉 → 成品批次(工单) → [半成品批次(组件工单) → …] → 原材料批次 → 供应商 & IQC 结果（定位问题来源）
 *  正向：原材料批次 → 用到它的组件工单 → 上级成品工单(成品批次) → 发出的发货单 → 客户（生成召回 / 排查清单）
 *  批次调拨到前置仓后批次号不变，发货单无论从哪个仓发出都能追到。
 */
@Service
@RequiredArgsConstructor
public class TraceService {
    private final JdbcTemplate jdbc;

    public Map<String, Object> backwardFromDelivery(String deliveryNo) {
        return backwardFromDelivery(deliveryNo, null);
    }

    /** @param productCode 只追溯发货单中的某个产品（客诉追溯时使用）；null 表示全部产品 */
    public Map<String, Object> backwardFromDelivery(String deliveryNo, String productCode) {
        List<Map<String, Object>> head = jdbc.queryForList("SELECT * FROM v_delivery WHERE delivery_no = ?", deliveryNo);
        if (head.isEmpty()) {
            throw new NotFoundException("发货单", deliveryNo);
        }
        List<Map<String, Object>> productLots = jdbc.queryForList("""
                SELECT l.lot_no, l.item_code, l.item_name, SUM(t.qty) AS qty, l.received_date,
                       w.line_code, w.actual_start, w.actual_end,
                       ROUND(w.good_qty / NULLIF(w.good_qty + w.scrap_qty, 0), 4) AS yield_rate
                FROM inv_lot_txn t JOIN v_lot l ON l.id = t.lot_id LEFT JOIN mes_work_order w ON w.wo_no = l.lot_no
                WHERE t.ref_no = ? AND l.item_type = 'PRODUCT' AND (? IS NULL OR l.item_code = ?)
                GROUP BY l.id, l.lot_no, l.item_code, l.item_name, l.received_date, w.line_code, w.actual_start, w.actual_end, w.good_qty, w.scrap_qty""",
                deliveryNo, productCode, productCode);
        for (Map<String, Object> pl : productLots) {
            pl.put("materials", materialsOfWorkOrder((String) pl.get("lot_no")));
        }
        Map<String, Object> r = new LinkedHashMap<>(head.get(0));
        r.put("productLots", productLots);
        return r;
    }

    public Map<String, Object> backwardFromComplaint(long complaintId) {
        List<Map<String, Object>> c = jdbc.queryForList("""
                SELECT c.*, d.delivery_no FROM v_complaint c LEFT JOIN sal_delivery d ON d.id = c.delivery_id WHERE c.id = ?""", complaintId);
        if (c.isEmpty()) {
            throw new NotFoundException("客诉", complaintId);
        }
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("complaint", c.get(0));
        if (c.get(0).get("delivery_no") != null) {
            r.put("trace", backwardFromDelivery((String) c.get(0).get("delivery_no"), (String) c.get(0).get("product_code")));
        }
        return r;
    }

    public Map<String, Object> forwardFromLot(String lotNo) {
        List<Map<String, Object>> lots = jdbc.queryForList("SELECT * FROM v_lot WHERE lot_no = ? AND item_type = 'MATERIAL'", lotNo);
        if (lots.isEmpty()) {
            throw new NotFoundException("原材料批次", lotNo);
        }
        List<Long> lotIds = lots.stream().map(l -> ((Number) l.get("id")).longValue()).toList();
        String in = String.join(",", Collections.nCopies(lotIds.size(), "?"));
        List<Map<String, Object>> workOrders = new ArrayList<>(jdbc.queryForList("""
                SELECT t.ref_no AS wo_no, SUM(t.qty) AS material_qty, w.item_type, w.product_code, w.product_name, w.good_qty, w.line_code, 1 AS hop
                FROM inv_lot_txn t JOIN v_work_order w ON w.wo_no = t.ref_no
                WHERE t.lot_id IN (%s) AND t.txn_type = 'WO_ISSUE' GROUP BY t.ref_no, w.item_type, w.product_code, w.product_name, w.good_qty, w.line_code
                ORDER BY t.ref_no""".formatted(in), lotIds.toArray()));
        // 组件工单的产出批次被哪些上级工单领用，逐层向上直到成品工单
        Set<String> seen = new HashSet<>();
        List<String> frontier = workOrders.stream().filter(w -> "MATERIAL".equals(w.get("item_type"))).map(w -> (String) w.get("wo_no")).toList();
        for (int hop = 2; !frontier.isEmpty() && hop <= 6; hop++) {
            seen.addAll(frontier);
            List<Map<String, Object>> up = jdbc.queryForList("""
                    SELECT t.ref_no AS wo_no, SUM(t.qty) AS material_qty, w.item_type, w.product_code, w.product_name, w.good_qty, w.line_code, %d AS hop
                    FROM inv_lot_txn t JOIN inv_lot l ON l.id = t.lot_id JOIN v_work_order w ON w.wo_no = t.ref_no
                    WHERE l.item_type = 'MATERIAL' AND l.lot_no IN (%s) AND t.txn_type = 'WO_ISSUE'
                    GROUP BY t.ref_no, w.item_type, w.product_code, w.product_name, w.good_qty, w.line_code ORDER BY t.ref_no"""
                    .formatted(hop, String.join(",", Collections.nCopies(frontier.size(), "?"))), frontier.toArray());
            workOrders.addAll(up);
            frontier = up.stream().filter(w -> "MATERIAL".equals(w.get("item_type"))).map(w -> (String) w.get("wo_no"))
                    .filter(n -> !seen.contains(n)).toList();
        }
        List<Object> productWos = workOrders.stream().filter(w -> "PRODUCT".equals(w.get("item_type"))).map(w -> w.get("wo_no")).distinct().toList();
        List<Map<String, Object>> shipments = productWos.isEmpty() ? List.of() : jdbc.queryForList("""
                SELECT d.delivery_no, d.delivery_date, d.customer_name, fl.lot_no AS product_lot, fl.item_code AS product_code, SUM(t.qty) AS qty,
                       (SELECT COUNT(*) FROM qms_complaint c WHERE c.delivery_id = d.id) AS complaints
                FROM inv_lot_txn t JOIN v_lot fl ON fl.id = t.lot_id AND fl.item_type = 'PRODUCT'
                JOIN v_delivery d ON d.delivery_no = t.ref_no
                WHERE fl.lot_no IN (%s) AND t.txn_type = 'SO_DELIVERY'
                GROUP BY d.delivery_no, d.delivery_date, d.customer_name, fl.lot_no, fl.item_code, d.id ORDER BY d.delivery_date"""
                .formatted(String.join(",", Collections.nCopies(productWos.size(), "?"))), productWos.toArray());
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("lots", lots);
        r.put("workOrders", workOrders);
        r.put("shipments", shipments);
        r.put("customers", shipments.stream().map(s -> s.get("customer_name")).distinct().toList());
        return r;
    }

    /** 工单耗用的物料批次；半成品批次（批次号 = 组件工单号）继续展开下一层 */
    private List<Map<String, Object>> materialsOfWorkOrder(String woNo) {
        return materialsOfWorkOrder(woNo, 0);
    }

    private List<Map<String, Object>> materialsOfWorkOrder(String woNo, int depth) {
        List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT l.lot_no, l.item_code, l.item_name, l.source_type, l.supplier_name, l.received_date, l.iqc_result, SUM(t.qty) AS qty
                FROM inv_lot_txn t JOIN v_lot l ON l.id = t.lot_id
                WHERE t.ref_no = ? AND t.txn_type = 'WO_ISSUE' AND l.item_type = 'MATERIAL'
                GROUP BY l.lot_no, l.item_code, l.item_name, l.source_type, l.supplier_name, l.received_date, l.iqc_result ORDER BY l.item_code""", woNo);
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> r : rows) {
            Map<String, Object> m = new LinkedHashMap<>(r);
            if ("WORK_ORDER".equals(r.get("source_type")) && depth < 5) {
                m.put("materials", materialsOfWorkOrder((String) r.get("lot_no"), depth + 1));
            }
            out.add(m);
        }
        return out;
    }
}
