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
@Table(name = "md_product")
public class Product {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String code;
    private String name;
    private String series;
    private String businessUnit;
    private String voltage;
    @Column(name = "power_w")
    private BigDecimal powerW;
    private Integer ratedSpeedRpm;
    private BigDecimal conversionCost;
    private BigDecimal listPrice;
    private Integer lotSize = 100;
    private String revision = "A";
    private String status = "ACTIVE";
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
