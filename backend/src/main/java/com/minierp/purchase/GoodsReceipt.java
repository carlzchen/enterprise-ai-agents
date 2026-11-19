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
@Table(name = "pur_receipt")
public class GoodsReceipt {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String grNo;
    private Long poId;
    private Long supplierId;
    private LocalDate receiptDate;
    private String status;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
