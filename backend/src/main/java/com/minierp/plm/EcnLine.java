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
@Table(name = "plm_ecn_line")
public class EcnLine {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long ecnId;
    private Long productId;
    private String action;
    private Long materialId;
    private BigDecimal newQty;
}
