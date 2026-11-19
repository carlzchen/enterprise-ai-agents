package com.minierp.purchase;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "pur_invoice")
public class PurchaseInvoice {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String invoiceNo;
    private Long supplierId;
    private Long grId;
    private LocalDate invoiceDate;
    private LocalDate dueDate;
    private BigDecimal amount;
    private BigDecimal taxAmount;
    private BigDecimal total;
    private BigDecimal paid = BigDecimal.ZERO;
    /** PENDING_APPROVAL 价差待审批 / OPEN / PARTIAL / PAID / REJECTED */
    private String status;
    private BigDecimal estimateAmount = BigDecimal.ZERO;
    private BigDecimal variance = BigDecimal.ZERO;
    private BigDecimal varianceStock = BigDecimal.ZERO;
    private BigDecimal varianceCogs = BigDecimal.ZERO;
    private String approvedBy;
    private LocalDate approvedDate;
}
