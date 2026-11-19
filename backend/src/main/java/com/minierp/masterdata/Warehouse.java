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
@Table(name = "md_warehouse")
public class Warehouse {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String code;
    private String name;
    /** RAW 原材料仓 / SEMI 半成品仓 / FG 成品总仓 / DC 前置仓 */
    private String type;
    private String region;
    private String locPrefix;
    private Integer transitDays;
}
