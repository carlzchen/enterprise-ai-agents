package com.minierp.purchase;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface GoodsReceiptRepository extends JpaRepository<GoodsReceipt, Long> {
    List<GoodsReceipt> findByStatus(String status);
}
