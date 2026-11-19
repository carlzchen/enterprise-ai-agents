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
@Table(name = "plm_ecn_review")
public class EcnReview {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long ecnId;
    private String reviewRole;
    private String decision;
    private String reviewer;
    private String comment;
    private LocalDate reviewDate;
}
