package com.minierp.inventory;

import com.minierp.common.BizException;
import com.minierp.common.Money;
import com.minierp.masterdata.MasterDataService;
import com.minierp.wms.WmsService;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 库存记账：所有出入库都经过这里，保证 inv_stock 现存量与 inv_txn 流水一致。
 * 成本采用移动加权平均法。调用方必须处于事务中。
 */
@Service
@RequiredArgsConstructor
public class InventoryService {
    public static final String MATERIAL = "MATERIAL";
    public static final String PRODUCT = "PRODUCT";

    private final StockRepository stocks;
    private final InventoryTxnRepository txns;
    private final MasterDataService md;
    private final JdbcTemplate jdbc;
    private final WmsService wms;
    private final GlService gl;
    private final LotService lots;

    /** @param warehouseId 为 null 时使用物料的默认仓库（原材料仓 / 半成品仓 / 成品总仓） */
    public record Movement(String itemType, Long itemId, BigDecimal qty, LocalDate date,
                           String txnType, String refType, String refNo, Long warehouseId) {
        public Movement(String itemType, Long itemId, BigDecimal qty, LocalDate date, String txnType, String refType, String refNo) {
            this(itemType, itemId, qty, date, txnType, refType, refNo, null);
        }
    }

    private final Map<Long, Boolean> makeCache = new ConcurrentHashMap<>();

    /** 入库，按移动加权平均更新单位成本 */
    @Transactional(propagation = Propagation.MANDATORY)
    public void receive(Movement m, BigDecimal unitCost) {
        requirePositive(m.qty());
        long wh = warehouseOf(m);
        Stock s = lock(wh, m.itemType(), m.itemId());
        s.setAvgCost(CostMath.movingAverage(s.getQty(), s.getAvgCost(), m.qty(), unitCost));
        s.setQty(s.getQty().add(m.qty()));
        log(m, wh, m.qty(), unitCost);
        wms.putaway(wh, m.itemType(), m.itemId(), m.qty(), m.date(), m.refNo());
        lots.receive(wh, m.itemType(), m.itemId(), m.qty(), m.date(), m.refType(), m.refNo());
    }

    /** 出库，按当前平均成本计价，返回出库单位成本；库存不足直接拒绝 */
    @Transactional(propagation = Propagation.MANDATORY)
    public BigDecimal issue(Movement m) {
        requirePositive(m.qty());
        long wh = warehouseOf(m);
        Stock s = lock(wh, m.itemType(), m.itemId());
        if (s.getQty().compareTo(m.qty()) < 0) {
            throw new BizException(String.format("库存不足：%s#%d 仓库#%d 现存 %s，需要 %s",
                    m.itemType(), m.itemId(), wh, s.getQty().stripTrailingZeros().toPlainString(),
                    m.qty().stripTrailingZeros().toPlainString()));
        }
        s.setQty(s.getQty().subtract(m.qty()));
        log(m, wh, m.qty().negate(), s.getAvgCost());
        wms.pick(wh, m.itemType(), m.itemId(), m.qty(), m.date(), m.refNo());
        lots.consume(wh, m.itemType(), m.itemId(), m.qty(), m.date(), m.txnType(), m.refNo());
        return s.getAvgCost();
    }

    /** 盘点调整：正数盘盈（按当前成本或指定成本入账），负数盘亏 */
    @Transactional
    public void adjust(String itemType, Long itemId, BigDecimal delta, BigDecimal unitCost, LocalDate date, String refNo) {
        Movement m = new Movement(itemType, itemId, delta.abs(), date, "ADJUST", "ADJUST", refNo);
        String stockAccount = stockAccount(itemType, itemId);
        // 期初建账贷记实收资本，其余盘盈盘亏走待处理财产损溢
        String contra = "期初".equals(refNo) ? GlService.CAPITAL : GlService.PENDING_LOSS;
        if (delta.signum() > 0) {
            BigDecimal cost = unitCost != null ? unitCost : lock(defaultWarehouse(itemType, itemId), itemType, itemId).getAvgCost();
            receive(m, cost);
            BigDecimal value = delta.multiply(cost);
            gl.post(date, "ADJUST", refNo, "库存调增 " + itemType + "#" + itemId,
                    List.of(Line.dr(stockAccount, value, "存货增加"), Line.cr(contra, value, "期初 / 盘盈")), null);
        } else if (delta.signum() < 0) {
            BigDecimal value = delta.abs().multiply(issue(m));
            gl.post(date, "ADJUST", refNo, "库存调减 " + itemType + "#" + itemId,
                    List.of(Line.dr(GlService.PENDING_LOSS, value, "盘亏"), Line.cr(stockAccount, value, "存货减少")), null);
        }
    }

    /**
     * 成本调整（采购价差计入仍在库的存货）：数量不变，调整库存金额并重算移动平均单价。
     * 调用方负责记总账。
     */
    @Transactional(propagation = Propagation.MANDATORY)
    public void adjustCost(long warehouseId, String itemType, long itemId, BigDecimal amount, LocalDate date, String refType, String refNo) {
        if (amount.signum() == 0) {
            return;
        }
        Stock s = lock(warehouseId, itemType, itemId);
        if (s.getQty().signum() > 0) {
            s.setAvgCost(s.getQty().multiply(s.getAvgCost()).add(amount).divide(s.getQty(), 4, java.math.RoundingMode.HALF_UP));
        }
        InventoryTxn t = new InventoryTxn();
        t.setTxnDate(date);
        t.setTxnType("COST_ADJ");
        t.setWarehouseId(warehouseId);
        t.setItemType(itemType);
        t.setItemId(itemId);
        t.setQty(BigDecimal.ZERO);
        t.setUnitCost(BigDecimal.ZERO);
        t.setAmount(Money.amt(amount));
        t.setRefType(refType);
        t.setRefNo(refNo);
        txns.save(t);
    }

    /** 非锁定读取默认仓库现存量（用于计划与展示） */
    public BigDecimal available(String itemType, Long itemId) {
        return availableIn(defaultWarehouse(itemType, itemId), itemType, itemId);
    }

    public BigDecimal availableIn(long warehouseId, String itemType, Long itemId) {
        return stocks.findByWarehouseIdAndItemTypeAndItemId(warehouseId, itemType, itemId)
                .map(Stock::getQty).orElse(BigDecimal.ZERO);
    }

    /** 存货科目：成品 1405，自制半成品 1411，外购原材料 1403 */
    public String stockAccount(String itemType, long itemId) {
        if (PRODUCT.equals(itemType)) {
            return GlService.FG;
        }
        return isMake(itemId) ? GlService.SEMI : GlService.RAW;
    }

    /** 默认仓库：成品 → 成品总仓，自制半成品 → 半成品仓，外购件 → 原材料仓 */
    public long defaultWarehouse(String itemType, long itemId) {
        if (PRODUCT.equals(itemType)) {
            return md.warehouseId("FG");
        }
        return md.warehouseId(isMake(itemId) ? "SEMI" : "RAW");
    }

    public boolean isMake(long materialId) {
        return makeCache.computeIfAbsent(materialId, id -> "MAKE".equals(md.material(id).getSupplyType()));
    }

    private long warehouseOf(Movement m) {
        return m.warehouseId() != null ? m.warehouseId() : defaultWarehouse(m.itemType(), m.itemId());
    }

    private Stock lock(long wh, String itemType, Long itemId) {
        jdbc.update("INSERT IGNORE INTO inv_stock (warehouse_id, item_type, item_id, qty, avg_cost) VALUES (?,?,?,0,0)",
                wh, itemType, itemId);
        return stocks.lockOne(wh, itemType, itemId).orElseThrow();
    }

    private void log(Movement m, Long wh, BigDecimal signedQty, BigDecimal unitCost) {
        InventoryTxn t = new InventoryTxn();
        t.setTxnDate(m.date());
        t.setTxnType(m.txnType());
        t.setWarehouseId(wh);
        t.setItemType(m.itemType());
        t.setItemId(m.itemId());
        t.setQty(signedQty);
        t.setUnitCost(unitCost);
        t.setAmount(Money.amt(signedQty.multiply(unitCost)));
        t.setRefType(m.refType());
        t.setRefNo(m.refNo());
        txns.save(t);
    }

    private static void requirePositive(BigDecimal qty) {
        if (qty == null || qty.signum() <= 0) {
            throw new BizException("数量必须大于 0");
        }
    }
}
