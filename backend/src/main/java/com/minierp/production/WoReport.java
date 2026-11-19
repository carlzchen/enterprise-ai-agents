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
@Table(name = "mes_wo_report")
public class WoReport {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long woId;
    private LocalDate reportDate;
    private BigDecimal goodQty;
    private BigDecimal scrapQty;
    private String defectCode;
    private String operator;
}
