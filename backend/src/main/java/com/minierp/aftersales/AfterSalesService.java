package com.minierp.aftersales;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import com.minierp.common.NotFoundException;
import com.minierp.common.SlaClock;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.inventory.InventoryService;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 售后服务：
 * 1. 服务工单：按序列号查保修（伺服 18 个月 / 其他 12 个月），按优先级计算响应、解决 SLA。
 * 2. RMA：来源于单台工单（先换后修，开 RMA 即算工单解决）或批量客诉，退回后检验处置：
 *    - 换新：成品出库；
 *    - 维修：按故障类型领用 BOM 中对应部件；
 *    - 无故障：原样退回。
 * 3. 保内成本冲减质量保证金（2801）；保外维修向客户收费，计其他业务收入 / 成本。
 * 4. 质量保证金：每月按收入计提；年末按近 12 个月索赔测算应有余额，补提或冲回，并确定下年计提比例。
 */
@Service
@RequiredArgsConstructor
public class AfterSalesService {
    public static final String PROVISION = "2801", OTHER_REVENUE = "6051", OTHER_COST = "6402";
    static final BigDecimal DEFAULT_RATE = new BigDecimal("0.0010");
    private static final Map<String, String> FAULT_PART = Map.of("NOISE", "BEARING", "ENCODER", "ENCODER", "NO_START", "PCB",
            "OVERHEAT", "COPPER", "SPEED", "MAGNET", "APPEARANCE", "PLASTIC");

    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final GlService gl;
    private final InventoryService inventory;

    public record TicketReq(Long customerId, Long productId, String sn, String channel, String type, String priority, String symptom, String faultCode) {
    }

    // ================= 保修查询 =================

    public Map<String, Object> warranty(String sn, LocalDate asOf) {
        Map<String, Object> s = jdbc.queryForList("""
                SELECT s.sn, s.product_id, p.code AS product_code, p.name AS product_name, p.series, s.wo_no, s.produced_date, s.result,
                       d.id AS delivery_id, d.delivery_no, d.delivery_date, d.customer_id, c.name AS customer_name
                FROM mes_serial s JOIN md_product p ON p.id = s.product_id LEFT JOIN sal_delivery d ON d.id = s.delivery_id
                LEFT JOIN md_customer c ON c.id = d.customer_id WHERE s.sn = ?""", sn).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("序列号", sn));
        Map<String, Object> r = new LinkedHashMap<>(s);
        LocalDate ship = s.get("delivery_date") == null ? null : ((Date) s.get("delivery_date")).toLocalDate();
        String series = (String) s.get("series");
        r.put("warrantyMonths", WarrantyPolicy.months(series));
        r.put("warrantyEnd", ship == null ? null : WarrantyPolicy.end(series, ship));
        r.put("inWarranty", WarrantyPolicy.inWarranty(series, ship, asOf));
        r.put("tickets", jdbc.queryForList("SELECT ticket_no, created_at, type, fault_code, status, resolution FROM svc_ticket WHERE sn = ? ORDER BY id", sn));
        return r;
    }

    // ================= 服务工单 =================

    @Transactional
    public long createTicket(TicketReq r, LocalDateTime now) {
        if (!List.of("P1", "P2", "P3").contains(r.priority())) {
            throw new BizException("优先级为 P1 / P2 / P3");
        }
        Long customer = r.customerId(), product = r.productId(), delivery = null;
        String warranty = "NA";
        LocalDate end = null;
        if (r.sn() != null && !r.sn().isBlank()) {
            Map<String, Object> w = warranty(r.sn(), now.toLocalDate());
            if (w.get("customer_id") == null) {
                throw new BizException("序列号 " + r.sn() + " 尚未发货");
            }
            customer = ((Number) w.get("customer_id")).longValue();
            product = ((Number) w.get("product_id")).longValue();
            delivery = ((Number) w.get("delivery_id")).longValue();
            end = (LocalDate) w.get("warrantyEnd");
            warranty = Boolean.TRUE.equals(w.get("inWarranty")) ? "IN" : "OUT";
        } else if ("FAULT".equals(r.type())) {
            warranty = "IN"; // 批次问题按客诉处理，默认保内
        }
        if (customer == null) {
            throw new BizException("请提供客户或序列号");
        }
        WarrantyPolicy.TicketSla sla = WarrantyPolicy.sla(r.priority());
        String no = docNo.next("SV", now.toLocalDate());
        jdbc.update("""
                INSERT INTO svc_ticket (ticket_no, customer_id, product_id, sn, delivery_id, channel, type, priority, symptom, fault_code, warranty_status,
                  warranty_end, status, created_at, response_due, resolve_due) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'NEW',?,?,?)""",
                no, customer, product, r.sn(), delivery, r.channel() == null ? "PHONE" : r.channel(), r.type(), r.priority(), r.symptom(), r.faultCode(),
                warranty, end == null ? null : Date.valueOf(end), Timestamp.valueOf(now),
                Timestamp.valueOf(SlaClock.due(now, sla.responseHours(), sla.businessHours())), Timestamp.valueOf(SlaClock.due(now, sla.resolveHours(), sla.businessHours())));
        return jdbc.queryForObject("SELECT id FROM svc_ticket WHERE ticket_no = ?", Long.class, no);
    }

    @Transactional
    public void assign(long id, long engineerEmpId, LocalDateTime at) {
        if (jdbc.update("UPDATE svc_ticket SET engineer_emp_id = ?, status = 'ASSIGNED', responded_at = COALESCE(responded_at, ?) WHERE id = ? AND status = 'NEW'",
                engineerEmpId, Timestamp.valueOf(at), id) == 0) {
            throw new BizException("只有新建工单可以派工");
        }
    }

    /**
     * 处理完成。resolution 为 RMA 时开具退货授权，工单等 RMA 处理完才算解决；
     * 保外维修 fee > 0 时收取服务费（现结，含 13% 增值税）。
     */
    @Transactional
    public Long resolve(long id, String resolution, String note, BigDecimal fee, LocalDateTime at) {
        Map<String, Object> t = ticket(id);
        if (!"ASSIGNED".equals(t.get("status"))) {
            throw new BizException("工单未派工或已处理");
        }
        if ("RMA".equals(resolution)) {
            long rma = createRma("TICKET", id, null, ((Number) t.get("customer_id")).longValue(), ((Number) t.get("product_id")).longValue(),
                    (String) t.get("sn"), BigDecimal.ONE, "IN".equals(t.get("warranty_status")), at.toLocalDate());
            // 先换后修：开出 RMA 即向客户承诺处理方案，工单视为解决；退回件检验与成本在 RMA 上跟踪
            jdbc.update("UPDATE svc_ticket SET status = 'RESOLVED', resolution = 'RMA', resolution_note = ?, resolved_at = ? WHERE id = ?", note, Timestamp.valueOf(at), id);
            markBreach(id);
            return rma;
        }
        BigDecimal f = fee == null ? BigDecimal.ZERO : Money.amt(fee);
        jdbc.update("UPDATE svc_ticket SET status = 'RESOLVED', resolution = ?, resolution_note = ?, resolved_at = ?, service_fee = ? WHERE id = ?",
                resolution, note, Timestamp.valueOf(at), f, id);
        markBreach(id);
        if (f.signum() > 0) {
            BigDecimal vat = Money.amt(f.multiply(new BigDecimal("0.13")));
            gl.post(at.toLocalDate(), "SVC", (String) t.get("ticket_no"), "保外维修服务费 " + t.get("ticket_no"), List.of(
                    Line.dr(GlService.BANK, f.add(vat), "客户现结"), Line.cr(OTHER_REVENUE, f, "维修服务收入"), Line.cr(GlService.VAT_OUT, vat, "销项税额")), null);
        }
        return null;
    }

    @Transactional
    public void close(long id, Integer satisfaction, LocalDateTime at) {
        if (jdbc.update("UPDATE svc_ticket SET status = 'CLOSED', closed_at = ?, satisfaction = ? WHERE id = ? AND status = 'RESOLVED'",
                Timestamp.valueOf(at), satisfaction, id) == 0) {
            throw new BizException("只有已解决的工单可以关闭");
        }
    }

    private void markBreach(long id) {
        jdbc.update("""
                UPDATE svc_ticket SET sla_breached = (responded_at IS NULL OR responded_at > response_due OR (resolved_at IS NOT NULL AND resolved_at > resolve_due))
                WHERE id = ?""", id);
    }

    // ================= RMA =================

    @Transactional
    public long createRma(String source, Long ticketId, Long complaintId, long customerId, long productId, String sn, BigDecimal qty, boolean inWarranty, LocalDate date) {
        String no = docNo.next("RMA", date);
        jdbc.update("""
                INSERT INTO svc_rma (rma_no, source, ticket_id, complaint_id, customer_id, product_id, sn, qty, in_warranty, status, created_date)
                VALUES (?,?,?,?,?,?,?,?,?,'OPEN',?)""", no, source, ticketId, complaintId, customerId, productId, sn, qty, inWarranty, Date.valueOf(date));
        return jdbc.queryForObject("SELECT id FROM svc_rma WHERE rma_no = ?", Long.class, no);
    }

    @Transactional
    public void receiveRma(long id, LocalDate date) {
        if (jdbc.update("UPDATE svc_rma SET status = 'RECEIVED', received_date = ? WHERE id = ? AND status = 'OPEN'", Date.valueOf(date), id) == 0) {
            throw new BizException("RMA 不是待退回状态");
        }
    }

    /**
     * 检验处置。换新需成品库存 ≥ 3 倍退货量（不挤占正常发货），否则改为维修；
     * 维修按故障类型领用 BOM 中对应部件，缺料时只计人工（不入账）。
     */
    @Transactional
    public Map<String, Object> inspectRma(long id, String disposition, String finding, LocalDate date) {
        Map<String, Object> r = jdbc.queryForList("""
                SELECT r.*, t.fault_code AS ticket_fault, q.defect_type FROM svc_rma r LEFT JOIN svc_ticket t ON t.id = r.ticket_id
                LEFT JOIN qms_complaint q ON q.id = r.complaint_id WHERE r.id = ?""", id).stream().findFirst().orElseThrow(() -> new NotFoundException("RMA", id));
        if (!"RECEIVED".equals(r.get("status"))) {
            throw new BizException("RMA 尚未收货或已处理");
        }
        long product = ((Number) r.get("product_id")).longValue();
        BigDecimal qty = (BigDecimal) r.get("qty");
        String no = (String) r.get("rma_no");
        boolean warranty = Boolean.TRUE.equals(r.get("in_warranty")) || Integer.valueOf(1).equals(r.get("in_warranty"));
        String disp = disposition;
        BigDecimal cost = BigDecimal.ZERO;
        String credit = null;
        if ("REPLACE".equals(disp)) {
            if (inventory.available(InventoryService.PRODUCT, product).compareTo(qty.multiply(BigDecimal.valueOf(3))) >= 0) {
                BigDecimal unit = inventory.issue(new InventoryService.Movement(InventoryService.PRODUCT, product, qty, date, "RMA_REPLACE", "RMA", no));
                cost = Money.amt(qty.multiply(unit));
                credit = GlService.FG;
            } else {
                disp = "REPAIR";
                finding = (finding == null ? "" : finding + "；") + "成品库存紧张，改为维修";
            }
        }
        if ("REPAIR".equals(disp)) {
            String fault = (String) (r.get("ticket_fault") != null ? r.get("ticket_fault") : r.get("defect_type"));
            Map<String, Object> part = repairPart(product, fault);
            if (part != null) {
                long mat = ((Number) part.get("material_id")).longValue();
                BigDecimal need = ((BigDecimal) part.get("qty_per")).multiply(qty).setScale(3, RoundingMode.UP);
                if (inventory.available(InventoryService.MATERIAL, mat).compareTo(need) >= 0) {
                    BigDecimal unit = inventory.issue(new InventoryService.Movement(InventoryService.MATERIAL, mat, need, date, "RMA_REPAIR", "RMA", no));
                    cost = Money.amt(need.multiply(unit));
                    credit = inventory.stockAccount(InventoryService.MATERIAL, mat);
                    finding = (finding == null ? "" : finding + "；") + "更换 " + part.get("material_name");
                }
            }
        }
        if (cost.signum() > 0) {
            String debit = warranty ? PROVISION : OTHER_COST;
            gl.post(date, "SVC", no, ("REPLACE".equals(disp) ? "RMA 换新 " : "RMA 维修 ") + no, List.of(
                    Line.dr(debit, cost, warranty ? "质量保证金耗用" : "保外维修成本"), Line.cr(credit, cost, "售后出库")), null);
            if (warranty) {
                ledger(date, "CLAIM", cost.negate(), null, no, disp + " " + qty.stripTrailingZeros().toPlainString() + " 台");
            }
        }
        jdbc.update("UPDATE svc_rma SET status = 'CLOSED', closed_date = ?, disposition = ?, finding = ?, cost = ? WHERE id = ?", Date.valueOf(date), disp, finding, cost, id);
        return Map.of("rmaNo", no, "disposition", disp, "cost", cost);
    }

    private Map<String, Object> repairPart(long productId, String fault) {
        String category = fault == null ? null : FAULT_PART.get(fault);
        List<Map<String, Object>> parts = jdbc.queryForList("""
                SELECT b.material_id, m.name AS material_name, m.category, b.qty_per FROM md_bom b JOIN md_material m ON m.id = b.material_id
                WHERE b.product_id = ? AND m.supply_type = 'BUY' AND m.category NOT IN ('PACKING') ORDER BY (m.category = ?) DESC, b.id""", productId, category);
        return parts.isEmpty() ? null : parts.get(0);
    }

    // ================= 质量保证金 =================

    /** 期初余额：系统上线前已售产品的在保责任 */
    @Transactional
    public void opening(BigDecimal amount, LocalDate date) {
        gl.post(date, "ADJUST", "期初", "期初预计负债（产品质量保证）", List.of(Line.dr(GlService.CAPITAL, amount, "实收资本"), Line.cr(PROVISION, amount, "预计负债")), null);
        ledger(date, "OPENING", amount, DEFAULT_RATE, "期初", "上线前已售产品的在保责任");
    }

    /** 客户质量索赔（停线损失、挑选返工费用）：银行支付，冲减质量保证金 */
    @Transactional
    public void payClaim(long rmaId, BigDecimal amount, LocalDate date) {
        String no = jdbc.queryForObject("SELECT rma_no FROM svc_rma WHERE id = ?", String.class, rmaId);
        BigDecimal a = Money.amt(amount);
        gl.post(date, "SVC", no, "支付客户质量索赔 " + no, List.of(Line.dr(PROVISION, a, "质量保证金耗用"), Line.cr(GlService.BANK, a, "银行付款")), null);
        ledger(date, "CLAIM", a.negate(), null, no, "客户索赔");
        jdbc.update("UPDATE svc_rma SET claim_amount = claim_amount + ? WHERE id = ?", a, rmaId);
    }

    public BigDecimal currentRate() {
        return jdbc.queryForList("SELECT rate FROM svc_warranty_ledger WHERE rate IS NOT NULL ORDER BY entry_date DESC, id DESC LIMIT 1", BigDecimal.class)
                .stream().findFirst().orElse(DEFAULT_RATE);
    }

    /** 月末计提：当月主营业务收入 × 计提比例 */
    @Transactional
    public BigDecimal accrue(YearMonth ym, LocalDate date) {
        BigDecimal revenue = jdbc.queryForObject("""
                SELECT COALESCE(SUM(e.credit - e.debit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE e.account_code = ? AND v.voucher_date BETWEEN ? AND ? AND v.source_type <> 'CLOSING'""", BigDecimal.class,
                GlService.REVENUE, Date.valueOf(ym.atDay(1)), Date.valueOf(ym.atEndOfMonth()));
        BigDecimal rate = currentRate();
        BigDecimal amt = WarrantyPolicy.accrual(revenue, rate);
        if (amt.signum() > 0) {
            gl.post(date, "SVC", "WP-" + ym, "计提产品质量保证金 " + ym, List.of(Line.dr(GlService.SELLING, amt, "售后服务费"), Line.cr(PROVISION, amt, "预计负债")), null);
            ledger(date, "ACCRUAL", amt, rate, "WP-" + ym, "收入 " + revenue.setScale(0, RoundingMode.HALF_UP) + " × " + rate);
        }
        // 余额保底：批次质量问题集中索赔时余额可能耗尽，低于近 3 个月索赔额的部分当月补提（预计负债不能出现借方余额）
        BigDecimal claims3m = jdbc.queryForObject("SELECT COALESCE(-SUM(amount), 0) FROM svc_warranty_ledger WHERE type = 'CLAIM' AND entry_date > ?",
                BigDecimal.class, Date.valueOf(date.minusMonths(3)));
        BigDecimal topUp = claims3m.subtract(balance());
        if (topUp.signum() > 0) {
            topUp = Money.amt(topUp);
            gl.post(date, "SVC", "WP-" + ym, "质量保证金余额不足补提 " + ym, List.of(Line.dr(GlService.SELLING, topUp, "售后服务费"), Line.cr(PROVISION, topUp, "预计负债")), null);
            ledger(date, "TOP_UP", topUp, null, "WP-" + ym, "余额低于近 3 个月索赔 " + claims3m.setScale(0, RoundingMode.HALF_UP) + "，补提差额");
            amt = amt.add(topUp);
        }
        return amt;
    }

    /** 年末调整：按近 12 个月索赔测算应有余额（在保平均剩余 = 保修期一半，按伺服 / 非伺服索赔加权），并确定下年计提比例 */
    @Transactional
    public Map<String, Object> yearEnd(int year, LocalDate date) {
        LocalDate to = LocalDate.of(year, 12, 31);
        LocalDate first = jdbc.queryForObject("SELECT MIN(entry_date) FROM svc_warranty_ledger WHERE type <> 'OPENING'", LocalDate.class);
        LocalDate from = first == null || first.isBefore(LocalDate.of(year, 1, 1)) ? LocalDate.of(year, 1, 1) : first.withDayOfMonth(1);
        // 不足 12 个月（上线当年）按实际月数年化
        long covered = java.time.temporal.ChronoUnit.MONTHS.between(YearMonth.from(from), YearMonth.from(to)) + 1;
        BigDecimal annualize = BigDecimal.valueOf(12.0 / Math.max(1, covered));
        BigDecimal claims = jdbc.queryForObject("SELECT COALESCE(-SUM(amount), 0) FROM svc_warranty_ledger WHERE type = 'CLAIM' AND entry_date BETWEEN ? AND ?",
                BigDecimal.class, Date.valueOf(from), Date.valueOf(to));
        BigDecimal servoClaims = jdbc.queryForObject("""
                SELECT COALESCE(SUM(r.cost + r.claim_amount), 0) FROM svc_rma r JOIN md_product p ON p.id = r.product_id
                WHERE r.in_warranty = 1 AND p.series = 'SERVO' AND r.closed_date BETWEEN ? AND ?""", BigDecimal.class, Date.valueOf(from), Date.valueOf(to));
        double servoShare = claims.signum() > 0 ? servoClaims.divide(claims, 4, RoundingMode.HALF_UP).doubleValue() : 0.5;
        double months = 18 * servoShare + 12 * (1 - servoShare);
        BigDecimal revenue = jdbc.queryForObject("""
                SELECT COALESCE(SUM(e.credit - e.debit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE e.account_code = ? AND v.voucher_date BETWEEN ? AND ? AND v.source_type <> 'CLOSING'""", BigDecimal.class,
                GlService.REVENUE, Date.valueOf(from), Date.valueOf(to));
        claims = claims.multiply(annualize).setScale(2, RoundingMode.HALF_UP);
        revenue = revenue.multiply(annualize).setScale(2, RoundingMode.HALF_UP);
        BigDecimal balance = balance();
        boolean enough = covered >= 12; // 上线不足 12 个月，索赔数据不具代表性，不调整、沿用原比例
        BigDecimal adj = enough ? WarrantyPolicy.trueUp(balance, claims, months) : BigDecimal.ZERO;
        BigDecimal next = enough ? WarrantyPolicy.nextRate(claims, revenue) : currentRate();
        String ref = "WT-" + year;
        if (adj.signum() > 0) {
            gl.post(date, "SVC", ref, year + " 年质量保证金补提", List.of(Line.dr(GlService.SELLING, adj, "售后服务费"), Line.cr(PROVISION, adj, "预计负债")), null);
        } else if (adj.signum() < 0) {
            gl.post(date, "SVC", ref, year + " 年质量保证金冲回", List.of(Line.dr(PROVISION, adj.negate(), "预计负债"), Line.cr(GlService.SELLING, adj.negate(), "冲减售后服务费")), null);
        }
        ledger(date, "TRUE_UP", adj, next, ref, !enough ? "上线不足 12 个月（" + covered + " 个月），不作调整，沿用计提比例 " + next
                : "近 12 个月索赔 " + claims.setScale(0, RoundingMode.HALF_UP) + "，在保平均 " + String.format("%.1f", months)
                + " 个月，应有余额 " + WarrantyPolicy.requiredBalance(claims, months) + "；下年计提比例 " + next);
        return Map.of("claims", claims, "revenue", revenue, "balanceBefore", balance, "adjustment", adj, "nextRate", next);
    }

    public BigDecimal balance() {
        return jdbc.queryForObject("SELECT COALESCE(SUM(amount), 0) FROM svc_warranty_ledger", BigDecimal.class);
    }

    private void ledger(LocalDate date, String type, BigDecimal amount, BigDecimal rate, String ref, String note) {
        jdbc.update("INSERT INTO svc_warranty_ledger (entry_date, period, type, amount, rate, ref_no, note) VALUES (?,?,?,?,?,?,?)",
                Date.valueOf(date), YearMonth.from(date).toString(), type, amount, rate, ref, note);
    }

    // ================= 看板 =================

    public Map<String, Object> dashboard(LocalDate from, LocalDate to) {
        Date f = Date.valueOf(from), t = Date.valueOf(to.plusDays(1));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("summary", jdbc.queryForMap("""
                SELECT COUNT(*) AS tickets, SUM(type = 'FAULT') AS faults, SUM(priority = 'P1') AS p1,
                       ROUND(AVG(sla_breached = 0 AND status IN ('RESOLVED','CLOSED')) / NULLIF(AVG(status IN ('RESOLVED','CLOSED')), 0), 4) AS sla_rate,
                       ROUND(AVG(TIMESTAMPDIFF(MINUTE, created_at, responded_at)) / 60, 1) AS avg_response_h,
                       ROUND(AVG(TIMESTAMPDIFF(MINUTE, created_at, resolved_at)) / 60, 1) AS avg_resolve_h,
                       ROUND(SUM(type = 'FAULT' AND resolution IN ('REMOTE','ONSITE')) / NULLIF(SUM(type = 'FAULT' AND resolution IS NOT NULL), 0), 4) AS first_fix_rate,
                       ROUND(AVG(satisfaction), 2) AS csat, SUM(service_fee) AS service_fee,
                       SUM(status NOT IN ('RESOLVED','CLOSED')) AS open_tickets
                FROM svc_ticket WHERE created_at >= ? AND created_at < ?""", f, t));
        out.put("rma", jdbc.queryForMap("""
                SELECT COUNT(*) AS rmas, COALESCE(SUM(qty), 0) AS qty, COALESCE(SUM(cost), 0) AS cost, COALESCE(SUM(claim_amount), 0) AS claims, SUM(disposition = 'REPLACE') AS replaced,
                       SUM(disposition = 'REPAIR') AS repaired, SUM(disposition = 'NO_FAULT') AS no_fault, SUM(status <> 'CLOSED') AS open_rmas
                FROM svc_rma WHERE created_date >= ? AND created_date < ?""", f, t));
        out.put("provisionBalance", balance());
        out.put("provisionRate", currentRate());
        out.put("monthly", jdbc.queryForList("""
                SELECT m.period, m.tickets, m.p1, m.breached, COALESCE(w.accrual, 0) AS accrual, COALESCE(w.claims, 0) AS claims FROM (
                  SELECT DATE_FORMAT(created_at, '%Y-%m') AS period, COUNT(*) AS tickets, SUM(priority = 'P1') AS p1, SUM(sla_breached) AS breached
                  FROM svc_ticket WHERE created_at >= ? AND created_at < ? GROUP BY period) m
                LEFT JOIN (SELECT period, SUM(CASE WHEN type IN ('ACCRUAL','TOP_UP') THEN amount END) AS accrual, -SUM(CASE WHEN type = 'CLAIM' THEN amount END) AS claims
                           FROM svc_warranty_ledger GROUP BY period) w ON w.period = m.period ORDER BY m.period""", f, t));
        out.put("faults", jdbc.queryForList("""
                SELECT COALESCE(t.fault_code, 'OTHER') AS fault_code, COUNT(*) AS n, SUM(p.series = 'SERVO') AS servo FROM svc_ticket t LEFT JOIN md_product p ON p.id = t.product_id
                WHERE t.type = 'FAULT' AND t.created_at >= ? AND t.created_at < ? GROUP BY fault_code ORDER BY n DESC""", f, t));
        out.put("engineers", jdbc.queryForList("""
                SELECT e.name, COUNT(*) AS tickets, SUM(t.sla_breached) AS breached, ROUND(AVG(t.satisfaction), 2) AS csat,
                       SUM(t.resolution = 'ONSITE') AS onsite FROM svc_ticket t JOIN hr_employee e ON e.id = t.engineer_emp_id
                WHERE t.created_at >= ? AND t.created_at < ? GROUP BY e.name ORDER BY tickets DESC""", f, t));
        // 伺服按生产月份的现场故障率（每千台）：同批次故障集中的一眼就能看出来
        out.put("servoFieldFailure", jdbc.queryForList("""
                SELECT DATE_FORMAT(s.produced_date, '%Y-%m') AS build_month, COUNT(DISTINCT s.sn) AS shipped, COUNT(DISTINCT t.sn) AS failed,
                       ROUND(COUNT(DISTINCT t.sn) * 1000 / COUNT(DISTINCT s.sn), 2) AS per_thousand
                FROM mes_serial s LEFT JOIN svc_ticket t ON t.sn = s.sn AND t.type = 'FAULT' WHERE s.delivery_id IS NOT NULL
                GROUP BY build_month ORDER BY build_month"""));
        return out;
    }

    private Map<String, Object> ticket(long id) {
        return jdbc.queryForList("SELECT * FROM svc_ticket WHERE id = ?", id).stream().findFirst().orElseThrow(() -> new NotFoundException("服务工单", id));
    }
}
