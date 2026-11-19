package com.minierp.wms;

import com.minierp.common.BizException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 库位管理：由 InventoryService 在出入库的同一事务内调用，保证 Σ库位库存 = inv_stock 现存量。
 * 库区：原材料按物料类别（R-COPPER…），成品按产品系列（F-SERVO…），每个库区首次使用时建 4 个库位。
 */
@Service
@RequiredArgsConstructor
public class WmsService {
    private static final int LOCATIONS_PER_ZONE = 4;
    private final JdbcTemplate jdbc;
    private final Map<String, String> zoneCache = new ConcurrentHashMap<>();
    private final Map<Long, String> prefixCache = new ConcurrentHashMap<>();

    @Transactional(propagation = Propagation.MANDATORY)
    public void putaway(long warehouseId, String itemType, long itemId, BigDecimal qty, LocalDate date, String refNo) {
        String zone = zoneOf(itemType, itemId);
        ensureZone(warehouseId, zone);
        List<BinAllocator.Bin> bins = jdbc.query("""
                SELECT l.id, COALESCE(SUM(CASE WHEN b.item_type = ? AND b.item_id = ? THEN b.qty END), 0) AS item_qty,
                       COALESCE(SUM(b.qty), 0) AS total_qty
                FROM wms_location l LEFT JOIN wms_bin_stock b ON b.location_id = l.id
                WHERE l.warehouse_id = ? AND l.zone = ? AND l.enabled = 1 GROUP BY l.id""",
                (rs, i) -> new BinAllocator.Bin(rs.getLong(1), rs.getBigDecimal(2), rs.getBigDecimal(3)),
                itemType, itemId, warehouseId, zone);
        long loc = BinAllocator.putaway(bins);
        jdbc.update("INSERT INTO wms_bin_stock (location_id, item_type, item_id, qty) VALUES (?,?,?,?) "
                + "ON DUPLICATE KEY UPDATE qty = qty + VALUES(qty)", loc, itemType, itemId, qty);
        task("PUTAWAY", itemType, itemId, null, loc, qty, date, refNo);
    }

    @Transactional(propagation = Propagation.MANDATORY)
    public void pick(long warehouseId, String itemType, long itemId, BigDecimal qty, LocalDate date, String refNo) {
        List<BinAllocator.Bin> bins = jdbc.query("""
                SELECT b.location_id, b.qty, b.qty FROM wms_bin_stock b JOIN wms_location l ON l.id = b.location_id
                WHERE l.warehouse_id = ? AND b.item_type = ? AND b.item_id = ? AND b.qty > 0 FOR UPDATE""",
                (rs, i) -> new BinAllocator.Bin(rs.getLong(1), rs.getBigDecimal(2), rs.getBigDecimal(3)),
                warehouseId, itemType, itemId);
        List<BinAllocator.Allocation> plan;
        try {
            plan = BinAllocator.pick(bins, qty);
        } catch (IllegalArgumentException e) {
            throw new BizException(e.getMessage());
        }
        for (BinAllocator.Allocation a : plan) {
            jdbc.update("UPDATE wms_bin_stock SET qty = qty - ? WHERE location_id = ? AND item_type = ? AND item_id = ?",
                    a.qty(), a.locationId(), itemType, itemId);
            task("PICK", itemType, itemId, a.locationId(), null, a.qty(), date, refNo);
        }
    }

    /** 移库 */
    @Transactional
    public void move(String itemType, long itemId, long fromLoc, long toLoc, BigDecimal qty, LocalDate date) {
        Integer sameWh = jdbc.queryForObject("SELECT COUNT(DISTINCT warehouse_id) FROM wms_location WHERE id IN (?, ?)",
                Integer.class, fromLoc, toLoc);
        if (sameWh == null || sameWh != 1) {
            throw new BizException("只能在同一仓库内移库");
        }
        int n = jdbc.update("UPDATE wms_bin_stock SET qty = qty - ? WHERE location_id = ? AND item_type = ? AND item_id = ? AND qty >= ?",
                qty, fromLoc, itemType, itemId, qty);
        if (n == 0) {
            throw new BizException("源库位库存不足");
        }
        jdbc.update("INSERT INTO wms_bin_stock (location_id, item_type, item_id, qty) VALUES (?,?,?,?) "
                + "ON DUPLICATE KEY UPDATE qty = qty + VALUES(qty)", toLoc, itemType, itemId, qty);
        task("MOVE", itemType, itemId, fromLoc, toLoc, qty, date, "MOVE");
    }

    /** 一致性校验：库位库存汇总与现存量不一致的物料 */
    public List<Map<String, Object>> reconcile() {
        return jdbc.queryForList("""
                SELECT s.warehouse_id, s.item_type, s.item_id, s.qty AS stock_qty, COALESCE(b.bin_qty, 0) AS bin_qty
                FROM inv_stock s LEFT JOIN (SELECT l.warehouse_id, b.item_type, b.item_id, SUM(b.qty) AS bin_qty FROM wms_bin_stock b
                  JOIN wms_location l ON l.id = b.location_id GROUP BY l.warehouse_id, b.item_type, b.item_id) b
                  ON b.warehouse_id = s.warehouse_id AND b.item_type = s.item_type AND b.item_id = s.item_id
                WHERE s.qty <> COALESCE(b.bin_qty, 0)""");
    }

    private void ensureZone(long warehouseId, String zone) {
        String prefix = prefixCache.computeIfAbsent(warehouseId,
                id -> jdbc.queryForObject("SELECT loc_prefix FROM md_warehouse WHERE id = ?", String.class, id));
        for (int i = 1; i <= LOCATIONS_PER_ZONE; i++) {
            jdbc.update("INSERT IGNORE INTO wms_location (warehouse_id, code, zone) VALUES (?,?,?)",
                    warehouseId, String.format("%s%s-%02d", prefix, zone, i), zone);
        }
    }

    private String zoneOf(String itemType, long itemId) {
        return zoneCache.computeIfAbsent(itemType + itemId, k -> "PRODUCT".equals(itemType)
                ? "F-" + jdbc.queryForObject("SELECT series FROM md_product WHERE id = ?", String.class, itemId)
                : "R-" + jdbc.queryForObject("SELECT category FROM md_material WHERE id = ?", String.class, itemId));
    }

    private void task(String type, String itemType, long itemId, Long from, Long to, BigDecimal qty, LocalDate date, String refNo) {
        jdbc.update("INSERT INTO wms_task (task_type, item_type, item_id, from_location_id, to_location_id, qty, ref_no, task_date) VALUES (?,?,?,?,?,?,?,?)",
                type, itemType, itemId, from, to, qty, refNo, date);
    }
}
