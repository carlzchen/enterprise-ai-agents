package com.minierp.sales;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface SalesInvoiceRepository extends JpaRepository<SalesInvoice, Long> {
    List<SalesInvoice> findByStatusIn(List<String> statuses);
    boolean existsByDeliveryId(Long deliveryId);
}
