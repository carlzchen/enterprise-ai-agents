package com.minierp.quality;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "qms_iqc")
public class IqcInspection {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String iqcNo;
    private Long grId;
    private Long grLineId;
    private Long materialId;
    private Long supplierId;
    private LocalDate inspectDate;
    private BigDecimal lotQty;
    private Integer sampleQty;
    private Integer defectQty;
    private String result;
    private String defectDesc;
    private String inspector;
}
