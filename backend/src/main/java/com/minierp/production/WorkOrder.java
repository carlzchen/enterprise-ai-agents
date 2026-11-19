package com.minierp.production;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "mes_work_order")
public class WorkOrder {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String woNo;
    /** PRODUCT 成品工单 / MATERIAL 半成品（组件）工单 */
    private String itemType = "PRODUCT";
    private Long productId;
    private Long materialId;
    private Long parentWoId;
    private String lineCode;
    private BigDecimal plannedQty;
    private BigDecimal goodQty = BigDecimal.ZERO;
    private BigDecimal scrapQty = BigDecimal.ZERO;
    private String status;
    private LocalDate planStart;
    private LocalDate planEnd;
    private LocalDate actualStart;
    private LocalDate actualEnd;
    private BigDecimal materialCost = BigDecimal.ZERO;
    private BigDecimal conversionCost = BigDecimal.ZERO;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
