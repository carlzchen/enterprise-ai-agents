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
@Table(name = "pur_receipt_line")
public class GoodsReceiptLine {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long grId;
    private Long poLineId;
    private Long materialId;
    private BigDecimal qty;
    private BigDecimal acceptedQty = BigDecimal.ZERO;
    private BigDecimal rejectedQty = BigDecimal.ZERO;
    private BigDecimal unitPrice;
    private boolean inspected;
}
