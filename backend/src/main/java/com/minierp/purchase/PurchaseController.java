package com.minierp.purchase;

import com.minierp.system.Audited;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.Map;

@RestController
@RequestMapping("/api/purchase")
@RequiredArgsConstructor
public class PurchaseController {
    private final PurchaseService purchase;

    public record DateReq(LocalDate date) {
    }

    public record InvoiceReq(LocalDate date, Map<Long, BigDecimal> prices) {
    }

    public record PayReq(LocalDate date, @NotNull BigDecimal amount) {
    }

    @PostMapping("/orders")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("purchase.createOrder")
    public PurchaseOrder create(@RequestBody @Valid PurchaseService.CreatePoReq req) {
        return purchase.createOrder(req);
    }

    @GetMapping("/orders/{id}")
    public Map<String, Object> detail(@PathVariable Long id) {
        return purchase.orderDetail(id);
    }

    @PostMapping("/orders/{id}/confirm")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("purchase.confirmOrder")
    public PurchaseOrder confirm(@PathVariable Long id) {
        return purchase.confirm(id);
    }

    @PostMapping("/orders/{id}/cancel")
    @PreAuthorize("hasAnyRole('ADMIN','PURCHASE')")
    @Audited("purchase.cancelOrder")
    public PurchaseOrder cancel(@PathVariable Long id) {
        return purchase.cancel(id);
    }

    @PostMapping("/orders/{id}/receive")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE','PURCHASE')")
    @Audited("purchase.receive")
    public GoodsReceipt receive(@PathVariable Long id, @RequestBody @Valid PurchaseService.ReceiveReq req) {
        return purchase.receive(id, req);
    }

    @GetMapping("/receipts/{id}")
    public Map<String, Object> receiptDetail(@PathVariable Long id) {
        return purchase.receiptDetail(id);
    }

    @PostMapping("/receipts/{id}/invoice")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("purchase.invoice")
    public PurchaseInvoice invoice(@PathVariable Long id, @RequestBody(required = false) InvoiceReq req) {
        return purchase.invoice(id, req == null || req.date() == null ? LocalDate.now() : req.date(),
                req == null || req.prices() == null ? Map.of() : req.prices());
    }

    @PostMapping("/invoices/{id}/approve")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE','MANAGEMENT')")
    @Audited("purchase.approveVariance")
    public PurchaseInvoice approve(@PathVariable Long id, @RequestBody(required = false) DateReq req, org.springframework.security.core.Authentication auth) {
        return purchase.approve(id, req == null || req.date() == null ? LocalDate.now() : req.date(), auth.getName());
    }

    @PostMapping("/invoices/{id}/reject")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE','MANAGEMENT')")
    @Audited("purchase.rejectVariance")
    public PurchaseInvoice reject(@PathVariable Long id, @RequestBody(required = false) DateReq req, org.springframework.security.core.Authentication auth) {
        return purchase.reject(id, req == null || req.date() == null ? LocalDate.now() : req.date(), auth.getName());
    }

    @GetMapping("/invoices/{id}/lines")
    public java.util.List<Map<String, Object>> invoiceLines(@PathVariable Long id) {
        return purchase.invoiceLines(id);
    }

    @PostMapping("/invoices/{id}/payments")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("purchase.pay")
    public Payment pay(@PathVariable Long id, @RequestBody @Valid PayReq req) {
        return purchase.pay(id, req.date() == null ? LocalDate.now() : req.date(), req.amount());
    }
}
