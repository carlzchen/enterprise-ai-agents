package com.minierp.srm;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.purchase.PurchaseOrder;
import com.minierp.purchase.PurchaseService;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

/** SRM：月度绩效考核与准入状态调整、询价比价与定标（定标后自动生成采购单） */
@Service
@RequiredArgsConstructor
public class SrmService {
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final PurchaseService purchase;

    /** 计算某月所有有到货的供应商绩效；最近两个月均为 D 级转为"观察"，恢复到 B 级以上转回"合格" */
    @Transactional
    public int computeMonthly(YearMonth month) {
        LocalDate from = month.atDay(1), to = month.atEndOfMonth();
        List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT g.supplier_id, COUNT(DISTINCT g.id) AS lots,
                       (SELECT AVG(q.result = 'PASS') FROM qms_iqc q WHERE q.supplier_id = g.supplier_id AND q.inspect_date BETWEEN ? AND ?) AS pass_rate,
                       AVG(g.receipt_date <= DATE_ADD(o.expected_date, INTERVAL 2 DAY)) AS on_time,
                       (SELECT SUM(l.qty * l.unit_price) / NULLIF(SUM(l.qty * mp.price), 0)
                          FROM pur_receipt g2 JOIN pur_receipt_line l ON l.gr_id = g2.id
                          JOIN md_material_price mp ON mp.material_id = l.material_id AND mp.price_month = ?
                          WHERE g2.supplier_id = g.supplier_id AND g2.receipt_date BETWEEN ? AND ?) AS price_index
                FROM pur_receipt g JOIN pur_order o ON o.id = g.po_id
                WHERE g.receipt_date BETWEEN ? AND ? GROUP BY g.supplier_id""", from, to, Date.valueOf(from), from, to, from, to);
        for (Map<String, Object> r : rows) {
            double pass = num(r.get("pass_rate"), 1), onTime = num(r.get("on_time"), 1), idx = num(r.get("price_index"), 1);
            SupplierScoring.Score s = SupplierScoring.score(pass, onTime, idx);
            jdbc.update("""
                    INSERT INTO srm_supplier_score (supplier_id, score_month, lots, pass_rate, on_time_rate, price_index,
                      quality_score, delivery_score, price_score, total_score, grade) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON DUPLICATE KEY UPDATE lots=VALUES(lots), pass_rate=VALUES(pass_rate), on_time_rate=VALUES(on_time_rate),
                      price_index=VALUES(price_index), quality_score=VALUES(quality_score), delivery_score=VALUES(delivery_score),
                      price_score=VALUES(price_score), total_score=VALUES(total_score), grade=VALUES(grade)""",
                    r.get("supplier_id"), Date.valueOf(from), r.get("lots"), pass, onTime, idx, s.quality(), s.delivery(), s.price(), s.total(), s.grade());
            List<String> recent = jdbc.queryForList("SELECT grade FROM srm_supplier_score WHERE supplier_id = ? ORDER BY score_month DESC LIMIT 2",
                    String.class, r.get("supplier_id"));
            if (recent.size() == 2 && recent.stream().allMatch("D"::equals)) {
                jdbc.update("UPDATE md_supplier SET qualification = 'OBSERVATION' WHERE id = ? AND qualification = 'QUALIFIED'", r.get("supplier_id"));
            } else if (!recent.isEmpty() && ("A".equals(recent.get(0)) || "B".equals(recent.get(0)))) {
                jdbc.update("UPDATE md_supplier SET qualification = 'QUALIFIED' WHERE id = ? AND qualification = 'OBSERVATION'", r.get("supplier_id"));
            }
        }
        return rows.size();
    }

    @Transactional
    public long createRfq(long materialId, BigDecimal qty, LocalDate needDate, LocalDate date, String user) {
        if (qty == null || qty.signum() <= 0) {
            throw new BizException("询价数量必须大于 0");
        }
        String no = docNo.next("RFQ", date);
        jdbc.update("INSERT INTO srm_rfq (rfq_no, material_id, qty, need_date, status, created_by, created_date) VALUES (?,?,?,?, 'OPEN', ?, ?)",
                no, materialId, qty, Date.valueOf(needDate), user, Date.valueOf(date));
        return jdbc.queryForObject("SELECT id FROM srm_rfq WHERE rfq_no = ?", Long.class, no);
    }

    @Transactional
    public void quote(long rfqId, long supplierId, BigDecimal price, int leadDays, LocalDate date, String remark) {
        Map<String, Object> rfq = rfq(rfqId);
        requireOpen(rfq);
        Map<String, Object> s = jdbc.queryForMap("SELECT category, qualification FROM md_supplier WHERE id = ?", supplierId);
        String category = jdbc.queryForObject("SELECT category FROM md_material WHERE id = ?", String.class, rfq.get("material_id"));
        if (!category.equals(s.get("category"))) {
            throw new BizException("该供应商不供应此类物料");
        }
        if ("DISQUALIFIED".equals(s.get("qualification"))) {
            throw new BizException("已淘汰供应商不能参与报价");
        }
        if (price == null || price.signum() <= 0 || leadDays <= 0) {
            throw new BizException("报价单价与交期必须大于 0");
        }
        jdbc.update("INSERT INTO srm_quote (rfq_id, supplier_id, unit_price, lead_days, quote_date, remark) VALUES (?,?,?,?,?,?) "
                + "ON DUPLICATE KEY UPDATE unit_price=VALUES(unit_price), lead_days=VALUES(lead_days), quote_date=VALUES(quote_date), remark=VALUES(remark)",
                rfqId, supplierId, price, leadDays, Date.valueOf(date), remark);
    }

    /** 比价推荐：综合价格（60%）、交期能否满足需求（20%）、最近绩效（20%） */
    public List<Map<String, Object>> compare(long rfqId) {
        Map<String, Object> rfq = rfq(rfqId);
        LocalDate created = ((Date) rfq.get("created_date")).toLocalDate(), need = ((Date) rfq.get("need_date")).toLocalDate();
        List<Map<String, Object>> quotes = new ArrayList<>(jdbc.queryForList("""
                SELECT q.*, s.name AS supplier_name, s.qualification,
                       (SELECT total_score FROM srm_supplier_score sc WHERE sc.supplier_id = q.supplier_id ORDER BY score_month DESC LIMIT 1) AS last_score
                FROM srm_quote q JOIN md_supplier s ON s.id = q.supplier_id WHERE q.rfq_id = ?""", rfqId));
        if (quotes.isEmpty()) {
            return quotes;
        }
        double minPrice = quotes.stream().mapToDouble(q -> ((BigDecimal) q.get("unit_price")).doubleValue()).min().orElse(1);
        for (Map<String, Object> q : quotes) {
            double price = ((BigDecimal) q.get("unit_price")).doubleValue();
            boolean inTime = !created.plusDays(((Number) q.get("lead_days")).intValue()).isAfter(need);
            double perf = q.get("last_score") == null ? 80 : ((BigDecimal) q.get("last_score")).doubleValue();
            double score = 60 * minPrice / price + (inTime ? 20 : 0) + 20 * perf / 100;
            q.put("meets_need_date", inTime);
            q.put("recommend_score", BigDecimal.valueOf(score).setScale(2, java.math.RoundingMode.HALF_UP));
        }
        quotes.sort(Comparator.comparing((Map<String, Object> q) -> (BigDecimal) q.get("recommend_score")).reversed());
        quotes.get(0).put("recommended", true);
        return quotes;
    }

    /** 定标：选择非推荐供应商时必须填写理由；自动生成并确认采购单 */
    @Transactional
    public PurchaseOrder award(long rfqId, long supplierId, String reason, LocalDate date, String user) {
        Map<String, Object> rfq = rfq(rfqId);
        requireOpen(rfq);
        List<Map<String, Object>> ranked = compare(rfqId);
        Map<String, Object> chosen = ranked.stream().filter(q -> ((Number) q.get("supplier_id")).longValue() == supplierId).findFirst()
                .orElseThrow(() -> new BizException("该供应商未报价"));
        if (!Boolean.TRUE.equals(chosen.get("recommended")) && (reason == null || reason.isBlank())) {
            throw new BizException("未选择推荐供应商，请填写定标理由");
        }
        int lead = ((Number) chosen.get("lead_days")).intValue();
        PurchaseOrder po = purchase.createOrder(new PurchaseService.CreatePoReq(supplierId, date, date.plusDays(lead), user,
                List.of(new PurchaseService.PoLineReq(((Number) rfq.get("material_id")).longValue(), (BigDecimal) rfq.get("qty"),
                        (BigDecimal) chosen.get("unit_price")))));
        purchase.confirm(po.getId());
        jdbc.update("UPDATE srm_rfq SET status='AWARDED', awarded_supplier_id=?, awarded_price=?, award_reason=?, po_id=? WHERE id=?",
                supplierId, chosen.get("unit_price"), reason, po.getId(), rfqId);
        return po;
    }

    private Map<String, Object> rfq(long id) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM srm_rfq WHERE id = ?", id);
        if (r.isEmpty()) {
            throw new NotFoundException("询价单", id);
        }
        return r.get(0);
    }

    private static void requireOpen(Map<String, Object> rfq) {
        if (!"OPEN".equals(rfq.get("status"))) {
            throw new BizException("询价单已结束");
        }
    }

    private static double num(Object o, double dflt) {
        return o == null ? dflt : ((Number) o).doubleValue();
    }
}
