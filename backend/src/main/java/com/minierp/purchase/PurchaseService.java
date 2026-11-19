package com.minierp.purchase;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import com.minierp.common.NotFoundException;
import com.minierp.masterdata.MasterDataService;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.masterdata.Supplier;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;

/**
 * 采购：采购订单 → 到货(待检) → IQC 合格入库暂估（见 QualityService）→ 收票(三单匹配、价差处理) → 付款。
 * 发票价与订单价的差异：价差率超过容差（3%）需财务审批后才记账；记账时该到货批次仍在库的部分调整存货成本，已领用部分计入主营业务成本。
 */
@Service
@RequiredArgsConstructor
public class PurchaseService {
    private static final BigDecimal VAT = new BigDecimal("0.13");
    public static final BigDecimal VARIANCE_TOLERANCE = new BigDecimal("0.03");

    private final PurchaseOrderRepository orders;
    private final PurchaseOrderLineRepository orderLines;
    private final GoodsReceiptRepository receipts;
    private final GoodsReceiptLineRepository receiptLines;
    private final PurchaseInvoiceRepository invoices;
    private final PaymentRepository payments;
    private final MasterDataService md;
    private final DocNoService docNo;
    private final JdbcTemplate jdbc;
    private final GlService gl;
    private final com.minierp.inventory.InventoryService inventory;

    public record PoLineReq(@NotNull Long materialId, @NotNull @Positive BigDecimal qty, BigDecimal unitPrice) {
    }

    public record CreatePoReq(@NotNull Long supplierId, @NotNull LocalDate orderDate, @NotNull LocalDate expectedDate,
                              String buyer, @NotEmpty List<@Valid PoLineReq> lines) {
    }

    public record ReceiveLineReq(@NotNull Long poLineId, @NotNull @Positive BigDecimal qty) {
    }

    public record ReceiveReq(@NotNull LocalDate date, @NotEmpty List<@Valid ReceiveLineReq> lines) {
    }

    @Transactional
    public PurchaseOrder createOrder(CreatePoReq req) {
        Supplier s = md.supplier(req.supplierId());
        if (!"ACTIVE".equals(s.getStatus())) {
            throw new BizException("供应商已停用：" + s.getName());
        }
        PurchaseOrder po = new PurchaseOrder();
        po.setPoNo(docNo.next("PO", req.orderDate()));
        po.setSupplierId(s.getId());
        po.setOrderDate(req.orderDate());
        po.setExpectedDate(req.expectedDate());
        po.setStatus("DRAFT");
        po.setBuyer(req.buyer());
        orders.save(po);
        int n = 1;
        for (PoLineReq l : req.lines()) {
            md.material(l.materialId());
            PurchaseOrderLine line = new PurchaseOrderLine();
            line.setPoId(po.getId());
            line.setLineNo(n++);
            line.setMaterialId(l.materialId());
            line.setQty(l.qty());
            // 未填单价时按当月参考价
            line.setUnitPrice(Money.price(l.unitPrice() != null ? l.unitPrice() : md.materialPrice(l.materialId(), req.orderDate())));
            orderLines.save(line);
        }
        return po;
    }

    @Transactional
    public PurchaseOrder confirm(Long poId) {
        PurchaseOrder po = order(poId);
        requireStatus(po, "DRAFT");
        po.setStatus("CONFIRMED");
        return po;
    }

    @Transactional
    public PurchaseOrder cancel(Long poId) {
        PurchaseOrder po = order(poId);
        requireStatus(po, "DRAFT", "CONFIRMED");
        po.setStatus("CANCELLED");
        return po;
    }

    /** 到货登记：物料进入待检状态，IQC 合格后才入库 */
    @Transactional
    public GoodsReceipt receive(Long poId, ReceiveReq req) {
        PurchaseOrder po = order(poId);
        requireStatus(po, "CONFIRMED", "PARTIAL");
        Map<Long, PurchaseOrderLine> lines = orderLines.findByPoIdOrderByLineNo(poId).stream()
                .collect(Collectors.toMap(PurchaseOrderLine::getId, Function.identity()));
        GoodsReceipt gr = new GoodsReceipt();
        gr.setGrNo(docNo.next("GR", req.date()));
        gr.setPoId(poId);
        gr.setSupplierId(po.getSupplierId());
        gr.setReceiptDate(req.date());
        gr.setStatus("PENDING_IQC");
        receipts.save(gr);
        for (ReceiveLineReq r : req.lines()) {
            PurchaseOrderLine pl = lines.get(r.poLineId());
            if (pl == null) {
                throw new BizException("采购行不属于该订单：" + r.poLineId());
            }
            if (r.qty().compareTo(pl.getQty().subtract(pl.getReceivedQty())) > 0) {
                throw new BizException("到货数量超过未到数量：行 " + pl.getLineNo());
            }
            GoodsReceiptLine gl = new GoodsReceiptLine();
            gl.setGrId(gr.getId());
            gl.setPoLineId(pl.getId());
            gl.setMaterialId(pl.getMaterialId());
            gl.setQty(r.qty());
            gl.setUnitPrice(pl.getUnitPrice());
            receiptLines.save(gl);
            pl.setReceivedQty(pl.getReceivedQty().add(r.qty()));
        }
        boolean all = lines.values().stream().allMatch(l -> l.getReceivedQty().compareTo(l.getQty()) >= 0);
        po.setStatus(all ? "RECEIVED" : "PARTIAL");
        return gr;
    }

    /** 收到供应商发票：发票价与订单价一致 */
    @Transactional
    public PurchaseInvoice invoice(Long grId, LocalDate date) {
        return invoice(grId, date, Map.of());
    }

    /**
     * 收到供应商发票。
     * @param invoicePrices 到货单行 id → 发票单价（不含税）；未给出的行按订单价
     */
    @Transactional
    public PurchaseInvoice invoice(Long grId, LocalDate date, Map<Long, BigDecimal> invoicePrices) {
        GoodsReceipt gr = receipts.findById(grId).orElseThrow(() -> new NotFoundException("到货单", grId));
        if (!"COMPLETED".equals(gr.getStatus())) {
            throw new BizException("到货单尚未完成检验入库或已收票");
        }
        if (invoices.existsByGrIdAndStatusNot(grId, "REJECTED")) {
            throw new BizException("该到货单已收票");
        }
        List<GoodsReceiptLine> grl = receiptLines.findByGrId(grId).stream().filter(l -> l.getAcceptedQty().signum() > 0).toList();
        if (grl.isEmpty()) {
            throw new BizException("该到货单全部判退，无需收票");
        }
        BigDecimal estimate = BigDecimal.ZERO, actual = BigDecimal.ZERO;
        for (GoodsReceiptLine l : grl) {
            BigDecimal price = invoicePrices.getOrDefault(l.getId(), l.getUnitPrice());
            if (price == null || price.signum() < 0) {
                throw new BizException("发票单价无效：行 " + l.getId());
            }
            estimate = estimate.add(l.getAcceptedQty().multiply(l.getUnitPrice()));
            actual = actual.add(l.getAcceptedQty().multiply(price));
        }
        Supplier s = md.supplier(gr.getSupplierId());
        PurchaseInvoice inv = new PurchaseInvoice();
        inv.setInvoiceNo(docNo.next("PI", date));
        inv.setSupplierId(s.getId());
        inv.setGrId(grId);
        inv.setInvoiceDate(date);
        inv.setDueDate(date.plusDays(s.getPaymentTermsDays()));
        inv.setEstimateAmount(Money.amt(estimate));
        inv.setAmount(Money.amt(actual));
        inv.setTaxAmount(Money.amt(actual.multiply(VAT)));
        inv.setTotal(inv.getAmount().add(inv.getTaxAmount()));
        inv.setVariance(inv.getAmount().subtract(inv.getEstimateAmount()));
        boolean hold = PriceVariance.needsApproval(inv.getEstimateAmount(), inv.getAmount(), VARIANCE_TOLERANCE);
        inv.setStatus(hold ? "PENDING_APPROVAL" : "OPEN");
        invoices.save(inv);
        for (GoodsReceiptLine l : grl) {
            BigDecimal price = Money.price(invoicePrices.getOrDefault(l.getId(), l.getUnitPrice()));
            BigDecimal est = Money.amt(l.getAcceptedQty().multiply(l.getUnitPrice()));
            BigDecimal amt = Money.amt(l.getAcceptedQty().multiply(price));
            jdbc.update("""
                    INSERT INTO pur_invoice_line (invoice_id, gr_line_id, material_id, qty, po_price, invoice_price, estimate_amount, amount, variance)
                    VALUES (?,?,?,?,?,?,?,?,?)""", inv.getId(), l.getId(), l.getMaterialId(), l.getAcceptedQty(), l.getUnitPrice(), price, est, amt, amt.subtract(est));
        }
        if (!hold) {
            post(inv, gr, date);
        }
        return inv;
    }

    /** 价差超容差的发票审批通过后记账（记账日 = 审批日，价差按审批时点的批次在库比例拆分） */
    @Transactional
    public PurchaseInvoice approve(Long invoiceId, LocalDate date, String user) {
        PurchaseInvoice inv = invoices.findById(invoiceId).orElseThrow(() -> new NotFoundException("采购发票", invoiceId));
        if (!"PENDING_APPROVAL".equals(inv.getStatus())) {
            throw new BizException("发票不在待审批状态");
        }
        inv.setStatus("OPEN");
        inv.setApprovedBy(user);
        inv.setApprovedDate(date);
        post(inv, receipts.findById(inv.getGrId()).orElseThrow(), date);
        return inv;
    }

    /** 驳回：发票作废，到货单保持暂估状态，等待供应商重开发票 */
    @Transactional
    public PurchaseInvoice reject(Long invoiceId, LocalDate date, String user) {
        PurchaseInvoice inv = invoices.findById(invoiceId).orElseThrow(() -> new NotFoundException("采购发票", invoiceId));
        if (!"PENDING_APPROVAL".equals(inv.getStatus())) {
            throw new BizException("发票不在待审批状态");
        }
        inv.setStatus("REJECTED");
        inv.setApprovedBy(user);
        inv.setApprovedDate(date);
        return inv;
    }

    /**
     * 记账：冲回暂估、确认应付（价税合计）；价差 = 发票金额 - 暂估金额，
     * 按到货批次仍在库比例拆分：在库部分调整存货成本（1403，并更新移动平均单价），已领用部分计入主营业务成本（6401）。
     */
    private void post(PurchaseInvoice inv, GoodsReceipt gr, LocalDate date) {
        BigDecimal toStock = BigDecimal.ZERO;
        for (Map<String, Object> l : jdbc.queryForList("SELECT * FROM pur_invoice_line WHERE invoice_id = ?", inv.getId())) {
            BigDecimal variance = (BigDecimal) l.get("variance");
            long materialId = ((Number) l.get("material_id")).longValue();
            BigDecimal qty = (BigDecimal) l.get("qty");
            List<Map<String, Object>> lots = jdbc.queryForList("""
                    SELECT warehouse_id, qty_remaining FROM inv_lot WHERE lot_no = ? AND item_type = 'MATERIAL' AND item_id = ? AND qty_remaining > 0""",
                    gr.getGrNo(), materialId);
            BigDecimal remaining = lots.stream().map(r -> (BigDecimal) r.get("qty_remaining")).reduce(BigDecimal.ZERO, BigDecimal::add);
            BigDecimal ratio = remaining.divide(qty, 4, java.math.RoundingMode.HALF_UP).min(BigDecimal.ONE);
            PriceVariance.Split split = PriceVariance.split((BigDecimal) l.get("estimate_amount"), (BigDecimal) l.get("amount"), ratio);
            jdbc.update("UPDATE pur_invoice_line SET stock_ratio = ?, variance_stock = ?, variance_cogs = ? WHERE id = ?",
                    ratio, split.toStock(), split.toCogs(), l.get("id"));
            if (split.toStock().signum() != 0) {
                // 按各仓库剩余量分摊到库存成本，最后一个仓库承担尾差
                BigDecimal left = split.toStock();
                for (int i = 0; i < lots.size(); i++) {
                    Map<String, Object> lot = lots.get(i);
                    BigDecimal part = i == lots.size() - 1 ? left
                            : Money.amt(split.toStock().multiply((BigDecimal) lot.get("qty_remaining")).divide(remaining, 6, java.math.RoundingMode.HALF_UP));
                    inventory.adjustCost(((Number) lot.get("warehouse_id")).longValue(), "MATERIAL", materialId, part, date, "PURCHASE_INVOICE", inv.getInvoiceNo());
                    left = left.subtract(part);
                }
            }
            toStock = toStock.add(split.toStock());
        }
        inv.setVarianceStock(toStock);
        inv.setVarianceCogs(inv.getVariance().subtract(toStock));
        gr.setStatus("INVOICED");
        Supplier s = md.supplier(inv.getSupplierId());
        gl.post(date, "PURCHASE_INVOICE", inv.getInvoiceNo(), "收到采购发票 " + s.getName(), List.of(
                Line.dr(GlService.AP_ESTIMATE, inv.getEstimateAmount(), "冲回暂估"),
                Line.signed(GlService.RAW, inv.getVarianceStock(), "采购价差-在库部分调整存货"),
                Line.signed(GlService.COGS, inv.getVarianceCogs(), "采购价差-已领用部分"),
                Line.dr(GlService.VAT_IN, inv.getTaxAmount(), "进项税额"),
                Line.cr(GlService.AP, inv.getTotal(), "应付 " + s.getName())), null);
    }

    @Transactional
    public Payment pay(Long invoiceId, LocalDate date, BigDecimal amount) {
        PurchaseInvoice inv = invoices.findById(invoiceId).orElseThrow(() -> new NotFoundException("采购发票", invoiceId));
        if (!"OPEN".equals(inv.getStatus()) && !"PARTIAL".equals(inv.getStatus())) {
            throw new BizException("发票状态为 " + inv.getStatus() + "，不能付款");
        }
        BigDecimal outstanding = inv.getTotal().subtract(inv.getPaid());
        if (amount == null || amount.signum() <= 0 || amount.compareTo(outstanding) > 0) {
            throw new BizException("付款金额需大于 0 且不超过未付金额 " + outstanding);
        }
        Payment p = new Payment();
        p.setPaymentNo(docNo.next("PY", date));
        p.setSupplierId(inv.getSupplierId());
        p.setInvoiceId(invoiceId);
        p.setPaymentDate(date);
        p.setAmount(Money.amt(amount));
        payments.save(p);
        gl.post(date, "PAYMENT", p.getPaymentNo(), "支付货款 " + inv.getInvoiceNo(), List.of(
                Line.dr(GlService.AP, p.getAmount(), "冲减应付"), Line.cr(GlService.BANK, p.getAmount(), "银行付款")), null);
        inv.setPaid(inv.getPaid().add(p.getAmount()));
        inv.setStatus(inv.getPaid().compareTo(inv.getTotal()) >= 0 ? "PAID" : "PARTIAL");
        return p;
    }

    public Map<String, Object> orderDetail(Long id) {
        List<Map<String, Object>> head = jdbc.queryForList("SELECT * FROM v_purchase_order WHERE id = ?", id);
        if (head.isEmpty()) {
            throw new NotFoundException("采购订单", id);
        }
        Map<String, Object> result = new HashMap<>(head.get(0));
        result.put("lines", jdbc.queryForList("SELECT * FROM v_purchase_order_line WHERE po_id = ? ORDER BY line_no", id));
        result.put("receipts", jdbc.queryForList("SELECT * FROM v_goods_receipt WHERE po_id = ? ORDER BY id", id));
        return result;
    }

    public List<Map<String, Object>> invoiceLines(Long invoiceId) {
        return jdbc.queryForList("""
                SELECT l.*, m.code AS material_code, m.name AS material_name FROM pur_invoice_line l JOIN md_material m ON m.id = l.material_id
                WHERE l.invoice_id = ? ORDER BY l.id""", invoiceId);
    }

    public Map<String, Object> receiptDetail(Long id) {
        List<Map<String, Object>> head = jdbc.queryForList("SELECT * FROM v_goods_receipt WHERE id = ?", id);
        if (head.isEmpty()) {
            throw new NotFoundException("到货单", id);
        }
        Map<String, Object> result = new HashMap<>(head.get(0));
        result.put("lines", jdbc.queryForList("SELECT * FROM v_goods_receipt_line WHERE gr_id = ? ORDER BY id", id));
        return result;
    }

    private PurchaseOrder order(Long id) {
        return orders.findById(id).orElseThrow(() -> new NotFoundException("采购订单", id));
    }

    private static void requireStatus(PurchaseOrder po, String... allowed) {
        for (String s : allowed) {
            if (s.equals(po.getStatus())) {
                return;
            }
        }
        throw new BizException("采购订单 " + po.getPoNo() + " 当前状态为 " + po.getStatus() + "，不能执行此操作");
    }
}
