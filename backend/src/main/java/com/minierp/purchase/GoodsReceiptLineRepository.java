package com.minierp.purchase;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface GoodsReceiptLineRepository extends JpaRepository<GoodsReceiptLine, Long> {
    List<GoodsReceiptLine> findByGrId(Long grId);
}
