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
@Table(name = "sal_delivery")
public class Delivery {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String deliveryNo;
    private Long orderId;
    private Long customerId;
    /** 发货仓库：成品总仓或客户所在区域的前置仓 */
    private Long warehouseId;
    private LocalDate deliveryDate;
    private String status;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
