package com.minierp.inventory;

import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 批次：原材料批次号 = 到货单号，成品批次号 = 工单号。出库按先进先出消耗并记录消耗单据，形成追溯链：
 * 发货单 → 成品批次(工单) → 工单消耗的原材料批次 → 到货单 / 供应商 / IQC 结果
 */
@Service
@RequiredArgsConstructor
public class LotService {
    private final JdbcTemplate jdbc;

    @Transactional(propagation = Propagation.MANDATORY)
    public void receive(long warehouseId, String itemType, long itemId, BigDecimal qty, LocalDate date, String refType, String refNo) {
        if ("TRANSFER".equals(refType)) {
            transferIn(warehouseId, itemType, itemId, qty, date, refNo);
            return;
        }
        String sourceType = "GOODS_RECEIPT".equals(refType) ? "GOODS_RECEIPT" : "WORK_ORDER".equals(refType) ? "WORK_ORDER" : "ADJUST";
        Long supplierId = "GOODS_RECEIPT".equals(sourceType)
                ? jdbc.queryForList("SELECT supplier_id FROM pur_receipt WHERE gr_no = ?", Long.class, refNo).stream().findFirst().orElse(null)
                : null;
        String lotNo = "ADJUST".equals(sourceType) ? "ADJ-" + date.toString().replace("-", "") : refNo;
        upsert(warehouseId, itemType, itemId, lotNo, sourceType, refNo, supplierId, date, qty);
    }

    /** 调拨入库：按调出时消耗的批次原样在调入仓重建（批次号、来源、供应商、原入库日期不变），追溯链跨仓不断 */
    private void transferIn(long warehouseId, String itemType, long itemId, BigDecimal qty, LocalDate date, String transferNo) {
        List<java.util.Map<String, Object>> src = jdbc.queryForList("""
                SELECT l.lot_no, l.source_type, l.source_no, l.supplier_id, l.received_date, SUM(t.qty) AS qty
                FROM inv_lot_txn t JOIN inv_lot l ON l.id = t.lot_id
                WHERE t.ref_no = ? AND t.txn_type = 'TRANSFER_OUT' AND l.item_type = ? AND l.item_id = ?
                GROUP BY l.lot_no, l.source_type, l.source_no, l.supplier_id, l.received_date ORDER BY l.received_date""",
                transferNo, itemType, itemId);
        BigDecimal left = qty;
        for (var r : src) {
            BigDecimal q = ((BigDecimal) r.get("qty")).min(left);
            if (q.signum() <= 0) {
                break;
            }
            upsert(warehouseId, itemType, itemId, (String) r.get("lot_no"), (String) r.get("source_type"), (String) r.get("source_no"),
                    r.get("supplier_id") == null ? null : ((Number) r.get("supplier_id")).longValue(),
                    ((java.sql.Date) r.get("received_date")).toLocalDate(), q);
            left = left.subtract(q);
        }
        if (left.signum() > 0) {
            upsert(warehouseId, itemType, itemId, transferNo, "ADJUST", transferNo, null, date, left);
        }
    }

    private void upsert(long warehouseId, String itemType, long itemId, String lotNo, String sourceType, String sourceNo, Long supplierId,
                        LocalDate date, BigDecimal qty) {
        // 同一单据同一物料多次入库（如工单分多次报工）累加到同一批次
        int updated = jdbc.update("UPDATE inv_lot SET qty_in = qty_in + ?, qty_remaining = qty_remaining + ? "
                + "WHERE warehouse_id = ? AND item_type = ? AND item_id = ? AND lot_no = ?", qty, qty, warehouseId, itemType, itemId, lotNo);
        if (updated == 0) {
            jdbc.update("INSERT INTO inv_lot (warehouse_id, item_type, item_id, lot_no, source_type, source_no, supplier_id, received_date, qty_in, qty_remaining) "
                    + "VALUES (?,?,?,?,?,?,?,?,?,?)", warehouseId, itemType, itemId, lotNo, sourceType, sourceNo, supplierId, date, qty, qty);
        }
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void consume(long warehouseId, String itemType, long itemId, BigDecimal qty, LocalDate date, String txnType, String refNo) {
        List<LotAllocator.Lot> lots = jdbc.query("""
                SELECT id, qty_remaining FROM inv_lot WHERE warehouse_id = ? AND item_type = ? AND item_id = ? AND qty_remaining > 0
                ORDER BY received_date, id FOR UPDATE""", (rs, i) -> new LotAllocator.Lot(rs.getLong(1), rs.getBigDecimal(2)), warehouseId, itemType, itemId);
        for (LotAllocator.Take t : LotAllocator.fifo(lots, qty)) {
            if (t.lotId() < 0) {
                continue; // 无批次余量（不应发生：批次与现存量同步维护）
            }
            jdbc.update("UPDATE inv_lot SET qty_remaining = qty_remaining - ? WHERE id = ?", t.qty(), t.lotId());
            jdbc.update("INSERT INTO inv_lot_txn (lot_id, txn_type, qty, ref_no, txn_date) VALUES (?,?,?,?,?)",
                    t.lotId(), txnType, t.qty(), refNo, date);
        }
    }
}
