package com.minierp.sales;

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
@RequestMapping("/api/sales")
@RequiredArgsConstructor
public class SalesController {
    private final SalesService sales;

    public record InvoiceReq(LocalDate date) {
    }

    public record ReceiveReq(LocalDate date, @NotNull BigDecimal amount, String method) {
    }

    @PostMapping("/orders")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("sales.createOrder")
    public SalesOrder create(@RequestBody @Valid SalesService.CreateOrderReq req) {
        return sales.createOrder(req);
    }

    @GetMapping("/orders/{id}")
    public Map<String, Object> detail(@PathVariable Long id) {
        return sales.orderDetail(id);
    }

    @PostMapping("/orders/{id}/confirm")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("sales.confirmOrder")
    public SalesOrder confirm(@PathVariable Long id) {
        return sales.confirm(id);
    }

    @PostMapping("/orders/{id}/cancel")
    @PreAuthorize("hasAnyRole('ADMIN','SALES')")
    @Audited("sales.cancelOrder")
    public SalesOrder cancel(@PathVariable Long id) {
        return sales.cancel(id);
    }

    @PostMapping("/orders/{id}/ship")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE','SALES')")
    @Audited("sales.ship")
    public Delivery ship(@PathVariable Long id, @RequestBody @Valid SalesService.ShipReq req) {
        return sales.ship(id, req);
    }

    @PostMapping("/deliveries/{id}/invoice")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("sales.invoice")
    public SalesInvoice invoice(@PathVariable Long id, @RequestBody(required = false) InvoiceReq req) {
        return sales.invoice(id, req == null || req.date() == null ? LocalDate.now() : req.date());
    }

    @PostMapping("/invoices/{id}/receipts")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("sales.receive")
    public Receipt receive(@PathVariable Long id, @RequestBody @Valid ReceiveReq req) {
        return sales.receive(id, req.date() == null ? LocalDate.now() : req.date(), req.amount(), req.method());
    }
}
