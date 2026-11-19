package com.minierp.transfer;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import com.minierp.common.NotFoundException;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.inventory.InventoryService;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.util.*;

/**
 * 仓库调拨：新建 → 发出（调出仓按移动平均出库，转入"调拨在途"）→ 到货（调入仓按原成本入库）。
 * 批次随货转移，在途期间库存既不在调出仓也不在调入仓，由 1402 调拨在途科目承接，保证总账与库存账一致。
 * 前置仓按订货点法生成补货建议（{@link ReplenishmentCalculator}）。
 */
@Service
@RequiredArgsConstructor
public class TransferService {
    /** 补货：可用量低于 7 天需求时补到 14 天，按 10 台取整 */
    private static final int REORDER_DAYS = 7, TARGET_DAYS = 14, DEMAND_WINDOW_DAYS = 28;
    private static final BigDecimal PACK = BigDecimal.TEN;

    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final InventoryService inventory;
    private final GlService gl;

    public record LineReq(@NotNull String itemType, @NotNull Long itemId, @NotNull @Positive BigDecimal qty) {
    }

    public record CreateReq(@NotNull Long fromWarehouseId, @NotNull Long toWarehouseId, LocalDate date, String remark,
                            @NotEmpty List<@Valid LineReq> lines) {
    }

    private record Line_(long id, String itemType, long itemId, BigDecimal qty, BigDecimal unitCost, BigDecimal amount) {
    }

    @Transactional
    public long create(CreateReq req, String user) {
        if (req.fromWarehouseId().equals(req.toWarehouseId())) {
            throw new BizException("调出仓和调入仓不能相同");
        }
        LocalDate date = req.date() == null ? LocalDate.now() : req.date();
        String no = docNo.next("TR", date);
        jdbc.update("INSERT INTO inv_transfer (transfer_no, from_warehouse_id, to_warehouse_id, status, create_date, created_by, remark) VALUES (?,?,?,?,?,?,?)",
                no, req.fromWarehouseId(), req.toWarehouseId(), "CREATED", Date.valueOf(date), user, req.remark());
        long id = jdbc.queryForObject("SELECT id FROM inv_transfer WHERE transfer_no = ?", Long.class, no);
        Map<String, BigDecimal> merged = new LinkedHashMap<>();
        for (LineReq l : req.lines()) {
            merged.merge(l.itemType() + ":" + l.itemId(), l.qty(), BigDecimal::add);
        }
        for (var e : merged.entrySet()) {
            String[] k = e.getKey().split(":");
            BigDecimal have = inventory.availableIn(req.fromWarehouseId(), k[0], Long.parseLong(k[1]));
            if (have.compareTo(e.getValue()) < 0) {
                throw new BizException("调出仓库存不足：" + k[0] + "#" + k[1] + " 现存 " + have.stripTrailingZeros().toPlainString());
            }
            jdbc.update("INSERT INTO inv_transfer_line (transfer_id, item_type, item_id, qty) VALUES (?,?,?,?)", id, k[0], Long.parseLong(k[1]), e.getValue());
        }
        return id;
    }

    /** 发出：调出仓出库，Dr 调拨在途 / Cr 存货 */
    @Transactional
    public void ship(long id, LocalDate date) {
        Map<String, Object> t = head(id);
        requireStatus(t, "CREATED");
        String no = (String) t.get("transfer_no");
        long from = ((Number) t.get("from_warehouse_id")).longValue();
        Map<String, BigDecimal> byAccount = new TreeMap<>();
        BigDecimal total = BigDecimal.ZERO;
        for (Line_ l : lines(id)) {
            BigDecimal cost = inventory.issue(new InventoryService.Movement(l.itemType(), l.itemId(), l.qty(), date,
                    "TRANSFER_OUT", "TRANSFER", no, from));
            BigDecimal amount = Money.amt(l.qty().multiply(cost));
            jdbc.update("UPDATE inv_transfer_line SET unit_cost = ?, amount = ? WHERE id = ?", cost, amount, l.id());
            byAccount.merge(inventory.stockAccount(l.itemType(), l.itemId()), amount, BigDecimal::add);
            total = total.add(amount);
        }
        int transit = jdbc.queryForObject("SELECT transit_days FROM md_warehouse WHERE id = ?", Integer.class, t.get("to_warehouse_id"));
        jdbc.update("UPDATE inv_transfer SET status = 'SHIPPED', ship_date = ?, expected_date = ? WHERE id = ?",
                Date.valueOf(date), Date.valueOf(date.plusDays(Math.max(transit, 1))), id);
        List<Line> v = new ArrayList<>();
        v.add(Line.dr(GlService.TRANSIT, total, "调拨在途 " + t.get("to_warehouse")));
        byAccount.forEach((acc, amt) -> v.add(Line.cr(acc, amt, "调出 " + t.get("from_warehouse"))));
        gl.post(date, "TRANSFER", no, "调拨发出 " + t.get("from_warehouse") + " → " + t.get("to_warehouse"), v, null);
    }

    /** 到货：调入仓按发出成本入库，Dr 存货 / Cr 调拨在途 */
    @Transactional
    public void receive(long id, LocalDate date) {
        Map<String, Object> t = head(id);
        requireStatus(t, "SHIPPED");
        String no = (String) t.get("transfer_no");
        long to = ((Number) t.get("to_warehouse_id")).longValue();
        Map<String, BigDecimal> byAccount = new TreeMap<>();
        BigDecimal total = BigDecimal.ZERO;
        for (Line_ l : lines(id)) {
            inventory.receive(new InventoryService.Movement(l.itemType(), l.itemId(), l.qty(), date, "TRANSFER_IN", "TRANSFER", no, to), l.unitCost());
            byAccount.merge(inventory.stockAccount(l.itemType(), l.itemId()), l.amount(), BigDecimal::add);
            total = total.add(l.amount());
        }
        jdbc.update("UPDATE inv_transfer SET status = 'RECEIVED', receive_date = ? WHERE id = ?", Date.valueOf(date), id);
        List<Line> v = new ArrayList<>();
        byAccount.forEach((acc, amt) -> v.add(Line.dr(acc, amt, "调入 " + t.get("to_warehouse"))));
        v.add(Line.cr(GlService.TRANSIT, total, "调拨在途转入库"));
        gl.post(date, "TRANSFER", no, "调拨到货 " + t.get("to_warehouse"), v, null);
    }

    @Transactional
    public void cancel(long id) {
        requireStatus(head(id), "CREATED");
        jdbc.update("UPDATE inv_transfer SET status = 'CANCELLED' WHERE id = ?", id);
    }

    public Map<String, Object> detail(long id) {
        Map<String, Object> r = new LinkedHashMap<>(head(id));
        r.put("lines", jdbc.queryForList("SELECT * FROM v_transfer_line WHERE transfer_id = ? ORDER BY id", id));
        return r;
    }

    /**
     * 前置仓补货建议：日均需求取近 28 天该仓服务区域客户的发货量；总仓可调量 = 总仓现存 - 总仓未发订单占用的一半（给本地客户留量）。
     */
    public List<Map<String, Object>> suggestions(LocalDate date) {
        long main = jdbc.queryForObject("SELECT id FROM md_warehouse WHERE type = 'FG'", Long.class);
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> dc : jdbc.queryForList("SELECT id, code, name, region FROM md_warehouse WHERE type = 'DC' ORDER BY id")) {
            long dcId = ((Number) dc.get("id")).longValue();
            String region = (String) dc.get("region");
            String cond = "EXPORT".equals(region) ? "c.is_domestic = 0" : "c.is_domestic = 1 AND c.region = '" + region.replace("'", "") + "'";
            List<Map<String, Object>> rows = jdbc.queryForList("""
                    SELECT dl.product_id, p.code, p.name, SUM(dl.qty) / 20 AS daily,
                      (SELECT COALESCE(SUM(s.qty), 0) FROM inv_stock s WHERE s.warehouse_id = ? AND s.item_type = 'PRODUCT' AND s.item_id = dl.product_id) AS on_hand,
                      (SELECT COALESCE(SUM(tl.qty), 0) FROM inv_transfer t JOIN inv_transfer_line tl ON tl.transfer_id = t.id
                        WHERE t.to_warehouse_id = ? AND t.status IN ('CREATED','SHIPPED') AND tl.item_type = 'PRODUCT' AND tl.item_id = dl.product_id) AS in_transit,
                      (SELECT COALESCE(SUM(s.qty), 0) FROM inv_stock s WHERE s.warehouse_id = ? AND s.item_type = 'PRODUCT' AND s.item_id = dl.product_id) AS main_stock
                    FROM sal_delivery d JOIN sal_delivery_line dl ON dl.delivery_id = d.id JOIN md_customer c ON c.id = d.customer_id
                    JOIN md_product p ON p.id = dl.product_id
                    WHERE d.delivery_date > ? AND d.delivery_date <= ? AND %s
                    GROUP BY dl.product_id, p.code, p.name ORDER BY p.code""".formatted(cond),
                    dcId, dcId, main, Date.valueOf(date.minusDays(DEMAND_WINDOW_DAYS)), Date.valueOf(date));
            for (Map<String, Object> r : rows) {
                BigDecimal daily = ((BigDecimal) r.get("daily")).setScale(3, java.math.RoundingMode.HALF_UP);
                BigDecimal qty = ReplenishmentCalculator.suggest(daily, REORDER_DAYS, TARGET_DAYS, (BigDecimal) r.get("on_hand"),
                        (BigDecimal) r.get("in_transit"), ((BigDecimal) r.get("main_stock")).multiply(new BigDecimal("0.5")), PACK);
                if (qty.signum() > 0) {
                    Map<String, Object> s = new LinkedHashMap<>(r);
                    s.put("daily", daily);
                    s.put("warehouse_id", dcId);
                    s.put("warehouse_name", dc.get("name"));
                    s.put("suggest_qty", qty);
                    out.add(s);
                }
            }
        }
        return out;
    }

    /** 按补货建议为每个前置仓生成一张调拨单（总仓 → 前置仓），返回调拨单 id */
    @Transactional
    public List<Long> createFromSuggestions(LocalDate date, String user) {
        long main = jdbc.queryForObject("SELECT id FROM md_warehouse WHERE type = 'FG'", Long.class);
        Map<Long, List<LineReq>> byDc = new LinkedHashMap<>();
        for (Map<String, Object> s : suggestions(date)) {
            byDc.computeIfAbsent(((Number) s.get("warehouse_id")).longValue(), k -> new ArrayList<>())
                    .add(new LineReq("PRODUCT", ((Number) s.get("product_id")).longValue(), (BigDecimal) s.get("suggest_qty")));
        }
        List<Long> ids = new ArrayList<>();
        byDc.forEach((dc, lines) -> ids.add(create(new CreateReq(main, dc, date, "前置仓补货（订货点法）", lines), user)));
        return ids;
    }

    private Map<String, Object> head(long id) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM v_transfer WHERE id = ?", id);
        if (r.isEmpty()) {
            throw new NotFoundException("调拨单", id);
        }
        return r.get(0);
    }

    private List<Line_> lines(long id) {
        return jdbc.query("SELECT id, item_type, item_id, qty, unit_cost, amount FROM inv_transfer_line WHERE transfer_id = ? ORDER BY id",
                (rs, i) -> new Line_(rs.getLong(1), rs.getString(2), rs.getLong(3), rs.getBigDecimal(4), rs.getBigDecimal(5), rs.getBigDecimal(6)), id);
    }

    private static void requireStatus(Map<String, Object> t, String status) {
        if (!status.equals(t.get("status"))) {
            throw new BizException("调拨单 " + t.get("transfer_no") + " 当前状态为 " + t.get("status") + "，不能执行此操作");
        }
    }
}
