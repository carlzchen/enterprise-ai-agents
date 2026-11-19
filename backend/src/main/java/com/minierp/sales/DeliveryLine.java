package com.minierp.sales;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

@Getter
@Setter
@Entity
@Table(name = "sal_delivery_line")
public class DeliveryLine {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private Long deliveryId;
    private Long orderLineId;
    private Long productId;
    private BigDecimal qty;
    private BigDecimal unitPrice;
    private BigDecimal unitCost;
}
