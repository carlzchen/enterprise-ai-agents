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
@Table(name = "qms_complaint")
public class Complaint {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String complaintNo;
    private Long customerId;
    private Long productId;
    private Long deliveryId;
    private LocalDate receivedDate;
    private String defectType;
    private BigDecimal qtyAffected;
    private String severity;
    private String status;
    private String rootCause;
    private String correctiveAction;
    private LocalDate closedDate;
    private String owner;
}
