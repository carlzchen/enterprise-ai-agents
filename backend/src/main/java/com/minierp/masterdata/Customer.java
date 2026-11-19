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
@Table(name = "md_customer")
public class Customer {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String code;
    private String name;
    private String industry;
    private String region;
    private String country;
    private boolean isDomestic;
    private String currency;
    private BigDecimal creditLimit;
    private Integer paymentTermsDays;
    private String status = "ACTIVE";
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
