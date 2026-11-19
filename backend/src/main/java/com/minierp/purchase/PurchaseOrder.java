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
@Table(name = "pur_order")
public class PurchaseOrder {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String poNo;
    private Long supplierId;
    private LocalDate orderDate;
    private LocalDate expectedDate;
    private String status;
    private String buyer;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
