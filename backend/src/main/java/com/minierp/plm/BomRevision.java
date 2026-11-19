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
@Table(name = "plm_bom_revision")
public class BomRevision {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long productId;
    private String revision;
    private String status;
    private LocalDate effectiveDate;
    private LocalDate obsoleteDate;
    private Long ecnId;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
