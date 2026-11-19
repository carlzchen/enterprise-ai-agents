package com.minierp.plm;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "plm_project")
public class PlmProject {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String projectNo;
    private String name;
    private String series;
    private String stage;
    private String status;
    private String owner;
    private String targetCustomer;
    private LocalDate startDate;
    private LocalDate targetMpDate;
    private BigDecimal budget;
    private BigDecimal spent = BigDecimal.ZERO;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
