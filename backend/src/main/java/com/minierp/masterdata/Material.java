package com.minierp.masterdata;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "md_material")
public class Material {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String code;
    private String name;
    private String category;
    private String uom;
    /** BUY 外购 / MAKE 自制半成品 */
    private String supplyType = "BUY";
    private String lineCode;
    private BigDecimal conversionCost;
    private Integer lotSize;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
