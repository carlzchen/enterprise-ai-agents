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
@Table(name = "plm_ecn")
public class Ecn {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String ecnNo;
    private String title;
    private String reason;
    private String description;
    private String status;
    private String requestedBy;
    private LocalDate requestDate;
    private LocalDate effectiveDate;
    private BigDecimal unitCostDelta;
    private BigDecimal annualImpact;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
