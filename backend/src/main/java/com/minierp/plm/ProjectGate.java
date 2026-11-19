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
@Table(name = "plm_project_gate")
public class ProjectGate {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long projectId;
    private String fromStage;
    private String toStage;
    private String decision;
    private String comment;
    private String reviewer;
    private LocalDate gateDate;
}
