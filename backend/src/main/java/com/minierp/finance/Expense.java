package com.minierp.finance;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "fin_expense")
public class Expense {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String expenseNo;
    private LocalDate expenseDate;
    private String dept;
    private String category;
    private BigDecimal amount;
    private String employee;
    private String description;
    private String status;
    private String approvedBy;
    private Long employeeId;
    private Long wfInstanceId;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
