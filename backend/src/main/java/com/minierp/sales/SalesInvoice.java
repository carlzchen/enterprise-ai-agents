package com.minierp.sales;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "sal_invoice")
public class SalesInvoice {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String invoiceNo;
    private Long customerId;
    private Long deliveryId;
    private LocalDate invoiceDate;
    private LocalDate dueDate;
    private String currency;
    private BigDecimal fxRate;
    private BigDecimal amount;
    private BigDecimal taxRate;
    private BigDecimal amountCny;
    private BigDecimal totalCny;
    private BigDecimal receivedCny = BigDecimal.ZERO;
    private String status;
}
