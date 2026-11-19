package com.minierp.purchase;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface PurchaseInvoiceRepository extends JpaRepository<PurchaseInvoice, Long> {
    List<PurchaseInvoice> findByStatusIn(List<String> statuses);
    boolean existsByGrIdAndStatusNot(Long grId, String status);
}
