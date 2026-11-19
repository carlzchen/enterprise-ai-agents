package com.minierp.inventory;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.math.BigDecimal;
import java.time.LocalDate;

@Getter
@Setter
@Entity
@Table(name = "inv_txn")
public class InventoryTxn {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private LocalDate txnDate;
    private String txnType;
    private Long warehouseId;
    private String itemType;
    private Long itemId;
    private BigDecimal qty;
    private BigDecimal unitCost;
    private BigDecimal amount;
    private String refType;
    private String refNo;
}
