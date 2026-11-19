package com.minierp.sales;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import com.minierp.common.NotFoundException;
import com.minierp.inventory.InventoryService;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.masterdata.Customer;
import com.minierp.masterdata.MasterDataService;
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

/** 销售：订单 → 信用检查 → 发货(出库) → 开票(应收) → 收款 */
@Service
@RequiredArgsConstructor
public class SalesService {
    private static final BigDecimal VAT = new BigDecimal("0.13");

    private final SalesOrderRepository orders;
    private final SalesOrderLineRepository orderLines;
    private final DeliveryRepository deliveries;
    private final DeliveryLineRepository deliveryLines;
    private final SalesInvoiceRepository invoices;
    private final ReceiptRepository receipts;
    private final MasterDataService md;
    private final InventoryService inventory;
    private final DocNoService docNo;
    private final JdbcTemplate jdbc;
    private final GlService gl;
    private final com.minierp.mes.MesService mes;

    public record OrderLineReq(@NotNull Long productId, @NotNull @Positive BigDecimal qty,
                               @NotNull @Positive BigDecimal unitPrice) {
    }

    public record CreateOrderReq(@NotNull Long customerId, @NotNull LocalDate orderDate, @NotNull LocalDate requiredDate,
                                 String salesperson, String remark, @NotEmpty List<@Valid OrderLineReq> lines) {
    }

    public record ShipLineReq(@NotNull Long orderLineId, @NotNull @Positive BigDecimal qty) {
    }

    /** @param warehouseId 为空时自动选择：客户所在区域前置仓整单有货则就近发货，否则总仓发货 */
    public record ShipReq(@NotNull LocalDate date, @NotEmpty List<@Valid ShipLineReq> lines, Long warehouseId) {
        public ShipReq(LocalDate date, List<ShipLineReq> lines) {
            this(date, lines, null);
        }
    }

    // ---------- 订单 ----------

    @Transactional
    public SalesOrder createOrder(CreateOrderReq req) {
        Customer c = md.customer(req.customerId());
        if (!"ACTIVE".equals(c.getStatus())) {
            throw new BizException("客户已停用：" + c.getName());
        }
        if (req.requiredDate().isBefore(req.orderDate())) {
            throw new BizException("交期不能早于下单日期");
        }
        SalesOrder o = new SalesOrder();
        o.setOrderNo(docNo.next("SO", req.orderDate()));
        o.setCustomerId(c.getId());
        o.setOrderDate(req.orderDate());
        o.setRequiredDate(req.requiredDate());
        o.setCurrency(c.getCurrency());
        o.setFxRate(md.fxRate(c.getCurrency(), req.orderDate()));
        o.setStatus("DRAFT");
        o.setSalesperson(req.salesperson());
        o.setRemark(req.remark());
        orders.save(o);
        int n = 1;
        for (OrderLineReq l : req.lines()) {
            md.product(l.productId());
            SalesOrderLine line = new SalesOrderLine();
            line.setOrderId(o.getId());
            line.setLineNo(n++);
            line.setProductId(l.productId());
            line.setQty(l.qty());
            line.setUnitPrice(Money.price(l.unitPrice()));
            orderLines.save(line);
        }
        return o;
    }

    /** 确认订单：检查信用额度 = 未收应收 + 已确认未开票订单 + 本单（含税）≤ 信用额度 */
    @Transactional
    public SalesOrder confirm(Long orderId) {
        SalesOrder o = order(orderId);
        requireStatus(o, "DRAFT");
        Customer c = md.customer(o.getCustomerId());
        BigDecimal taxFactor = BigDecimal.ONE.add(c.isDomestic() ? VAT : BigDecimal.ZERO);
        BigDecimal thisOrder = orderAmount(o.getId()).multiply(o.getFxRate()).multiply(taxFactor);
        BigDecimal exposure = creditExposure(c.getId(), taxFactor);
        if (CreditPolicy.exceeds(c.getCreditLimit(), exposure, thisOrder)) {
            throw new BizException(String.format("超出信用额度：客户 %s 额度 %s，已占用 %s，本单 %s",
                    c.getName(), Money.amt(c.getCreditLimit()), Money.amt(exposure), Money.amt(thisOrder)));
        }
        o.setStatus("CONFIRMED");
        return o;
    }

    @Transactional
    public SalesOrder cancel(Long orderId) {
        SalesOrder o = order(orderId);
        requireStatus(o, "DRAFT", "CONFIRMED");
        o.setStatus("CANCELLED");
        return o;
    }

    // ---------- 发货 ----------

    @Transactional
    public Delivery ship(Long orderId, ShipReq req) {
        SalesOrder o = order(orderId);
        requireStatus(o, "CONFIRMED", "PARTIAL");
        Map<Long, SalesOrderLine> lines = orderLines.findByOrderIdOrderByLineNo(orderId).stream()
                .collect(Collectors.toMap(SalesOrderLine::getId, Function.identity()));
        Customer customer = md.customer(o.getCustomerId());
        long warehouseId = req.warehouseId() != null ? req.warehouseId() : routeWarehouse(customer, req.lines(), lines);
        Delivery d = new Delivery();
        d.setWarehouseId(warehouseId);
        d.setDeliveryNo(docNo.next("DN", req.date()));
        d.setOrderId(orderId);
        d.setCustomerId(o.getCustomerId());
        d.setDeliveryDate(req.date());
        d.setStatus("SHIPPED");
        deliveries.save(d);
        BigDecimal totalCost = BigDecimal.ZERO;
        for (ShipLineReq s : req.lines()) {
            SalesOrderLine ol = lines.get(s.orderLineId());
            if (ol == null) {
                throw new BizException("订单行不属于该订单：" + s.orderLineId());
            }
            BigDecimal open = ol.getQty().subtract(ol.getShippedQty());
            if (s.qty().compareTo(open) > 0) {
                throw new BizException("发货数量超过未发数量：行 " + ol.getLineNo());
            }
            BigDecimal unitCost = inventory.issue(new InventoryService.Movement(InventoryService.PRODUCT,
                    ol.getProductId(), s.qty(), req.date(), "SO_DELIVERY", "DELIVERY", d.getDeliveryNo(), warehouseId));
            DeliveryLine dl = new DeliveryLine();
            dl.setDeliveryId(d.getId());
            dl.setOrderLineId(ol.getId());
            dl.setProductId(ol.getProductId());
            dl.setQty(s.qty());
            dl.setUnitPrice(ol.getUnitPrice());
            dl.setUnitCost(unitCost);
            deliveryLines.save(dl);
            ol.setShippedQty(ol.getShippedQty().add(s.qty()));
            mes.assignSerials(d.getId(), ol.getProductId(), s.qty()); // 有序列号的产品（伺服）按先进先出绑定整机 SN
            totalCost = totalCost.add(s.qty().multiply(unitCost));
        }
        gl.post(req.date(), "DELIVERY", d.getDeliveryNo(), "销售出库结转成本", List.of(
                Line.dr(GlService.COGS, totalCost, "结转主营业务成本"), Line.cr(GlService.FG, totalCost, "库存商品出库")), null);
        boolean allShipped = lines.values().stream().allMatch(l -> l.getShippedQty().compareTo(l.getQty()) >= 0);
        o.setStatus(allShipped ? "SHIPPED" : "PARTIAL");
        return d;
    }

    /** 客户服务区域对应的前置仓（外销客户 → 香港出口仓），没有则 null */
    public Long regionalWarehouse(Customer c) {
        String region = c.isDomestic() ? c.getRegion() : "EXPORT";
        return jdbc.queryForList("SELECT id FROM md_warehouse WHERE type = 'DC' AND region = ?", Long.class, region)
                .stream().findFirst().orElse(null);
    }

    private long routeWarehouse(Customer c, List<ShipLineReq> req, Map<Long, SalesOrderLine> lines) {
        Long dc = regionalWarehouse(c);
        Map<Long, BigDecimal> need = new HashMap<>();
        Map<Long, BigDecimal> dcStock = new HashMap<>();
        for (ShipLineReq s : req) {
            SalesOrderLine ol = lines.get(s.orderLineId());
            if (ol != null) {
                need.merge(ol.getProductId(), s.qty(), BigDecimal::add);
                if (dc != null) {
                    dcStock.put(ol.getProductId(), inventory.availableIn(dc, InventoryService.PRODUCT, ol.getProductId()));
                }
            }
        }
        return com.minierp.transfer.WarehouseRouter.choose(dc, dcStock, need, md.warehouseId("FG"));
    }

    // ---------- 开票 / 收款 ----------

    @Transactional
    public SalesInvoice invoice(Long deliveryId, LocalDate date) {
        Delivery d = deliveries.findById(deliveryId).orElseThrow(() -> new NotFoundException("发货单", deliveryId));
        if (invoices.existsByDeliveryId(deliveryId)) {
            throw new BizException("该发货单已开票");
        }
        SalesOrder o = order(d.getOrderId());
        Customer c = md.customer(d.getCustomerId());
        BigDecimal amount = deliveryLines.findByDeliveryId(deliveryId).stream()
                .map(l -> l.getQty().multiply(l.getUnitPrice())).reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal taxRate = c.isDomestic() ? VAT : BigDecimal.ZERO;
        SalesInvoice inv = new SalesInvoice();
        inv.setInvoiceNo(docNo.next("SI", date));
        inv.setCustomerId(c.getId());
        inv.setDeliveryId(deliveryId);
        inv.setInvoiceDate(date);
        inv.setDueDate(date.plusDays(c.getPaymentTermsDays()));
        inv.setCurrency(o.getCurrency());
        inv.setFxRate(o.getFxRate());
        inv.setAmount(Money.amt(amount));
        inv.setTaxRate(taxRate);
        inv.setAmountCny(Money.amt(amount.multiply(o.getFxRate())));
        inv.setTotalCny(Money.amt(inv.getAmountCny().multiply(BigDecimal.ONE.add(taxRate))));
        inv.setStatus("OPEN");
        invoices.save(inv);
        d.setStatus("INVOICED");
        gl.post(date, "SALES_INVOICE", inv.getInvoiceNo(), "销售开票确认收入 " + c.getName(), List.of(
                Line.dr(GlService.AR, inv.getTotalCny(), "应收 " + c.getName()),
                Line.cr(GlService.REVENUE, inv.getAmountCny(), "主营业务收入"),
                Line.cr(GlService.VAT_OUT, inv.getTotalCny().subtract(inv.getAmountCny()), "销项税额")), null);
        return inv;
    }

    @Transactional
    public Receipt receive(Long invoiceId, LocalDate date, BigDecimal amount, String method) {
        SalesInvoice inv = invoices.findById(invoiceId).orElseThrow(() -> new NotFoundException("发票", invoiceId));
        BigDecimal outstanding = inv.getTotalCny().subtract(inv.getReceivedCny());
        if (amount == null || amount.signum() <= 0 || amount.compareTo(outstanding) > 0) {
            throw new BizException("收款金额需大于 0 且不超过未收金额 " + outstanding);
        }
        Receipt r = new Receipt();
        r.setReceiptNo(docNo.next("RC", date));
        r.setCustomerId(inv.getCustomerId());
        r.setInvoiceId(invoiceId);
        r.setReceiptDate(date);
        r.setAmountCny(Money.amt(amount));
        r.setMethod(method == null ? "BANK" : method);
        receipts.save(r);
        gl.post(date, "RECEIPT", r.getReceiptNo(), "收到货款 " + inv.getInvoiceNo(), List.of(
                Line.dr("ACCEPTANCE".equals(r.getMethod()) ? GlService.NOTES_RECEIVABLE : GlService.BANK, r.getAmountCny(), "收款"),
                Line.cr(GlService.AR, r.getAmountCny(), "冲减应收")), null);
        inv.setReceivedCny(inv.getReceivedCny().add(r.getAmountCny()));
        inv.setStatus(inv.getReceivedCny().compareTo(inv.getTotalCny()) >= 0 ? "PAID" : "PARTIAL");
        return r;
    }

    // ---------- 查询 ----------

    public Map<String, Object> orderDetail(Long id) {
        List<Map<String, Object>> head = jdbc.queryForList("SELECT * FROM v_sales_order WHERE id = ?", id);
        if (head.isEmpty()) {
            throw new NotFoundException("销售订单", id);
        }
        Map<String, Object> result = new HashMap<>(head.get(0));
        result.put("lines", jdbc.queryForList("SELECT * FROM v_sales_order_line WHERE order_id = ? ORDER BY line_no", id));
        result.put("deliveries", jdbc.queryForList("SELECT * FROM v_delivery WHERE order_id = ? ORDER BY id", id));
        return result;
    }

    // ---------- 内部 ----------

    private SalesOrder order(Long id) {
        return orders.findById(id).orElseThrow(() -> new NotFoundException("销售订单", id));
    }

    private BigDecimal orderAmount(Long orderId) {
        return orderLines.findByOrderIdOrderByLineNo(orderId).stream()
                .map(l -> l.getQty().multiply(l.getUnitPrice())).reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    /** 信用占用：未结清发票余额 + 已确认订单中尚未发货部分（按含税人民币估算） */
    private BigDecimal creditExposure(Long customerId, BigDecimal taxFactor) {
        BigDecimal ar = jdbc.queryForObject(
                "SELECT COALESCE(SUM(total_cny - received_cny), 0) FROM sal_invoice WHERE customer_id = ? AND status <> 'PAID'",
                BigDecimal.class, customerId);
        BigDecimal openOrders = jdbc.queryForObject(
                "SELECT COALESCE(SUM((l.qty - l.shipped_qty) * l.unit_price * o.fx_rate), 0) FROM sal_order o "
                        + "JOIN sal_order_line l ON l.order_id = o.id WHERE o.customer_id = ? AND o.status IN ('CONFIRMED','PARTIAL')",
                BigDecimal.class, customerId);
        BigDecimal uninvoiced = jdbc.queryForObject(
                "SELECT COALESCE(SUM(dl.qty * dl.unit_price * o.fx_rate), 0) FROM sal_delivery d JOIN sal_order o ON o.id = d.order_id "
                        + "JOIN sal_delivery_line dl ON dl.delivery_id = d.id WHERE d.customer_id = ? AND d.status = 'SHIPPED'",
                BigDecimal.class, customerId);
        return ar.add(openOrders.add(uninvoiced).multiply(taxFactor));
    }

    private static void requireStatus(SalesOrder o, String... allowed) {
        for (String s : allowed) {
            if (s.equals(o.getStatus())) {
                return;
            }
        }
        throw new BizException("订单 " + o.getOrderNo() + " 当前状态为 " + o.getStatus() + "，不能执行此操作");
    }
}
