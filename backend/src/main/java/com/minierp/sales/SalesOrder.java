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
@Table(name = "sal_order")
public class SalesOrder {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String orderNo;
    private Long customerId;
    private LocalDate orderDate;
    private LocalDate requiredDate;
    private String currency;
    private BigDecimal fxRate;
    private String status;
    private String salesperson;
    private String remark;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
