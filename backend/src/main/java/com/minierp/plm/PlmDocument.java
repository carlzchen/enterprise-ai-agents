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
@Table(name = "plm_document")
public class PlmDocument {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String docNo;
    private Integer revision;
    private String title;
    private String docType;
    private Long productId;
    private String series;
    private String status;
    private String fileName;
    private String filePath;
    private Long fileSize;
    private String contentType;
    private String uploadedBy;
    private LocalDate uploadDate;
    @Column(insertable = false, updatable = false)
    private LocalDateTime createdAt;
}
