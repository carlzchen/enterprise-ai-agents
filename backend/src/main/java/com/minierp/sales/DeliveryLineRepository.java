package com.minierp.sales;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface DeliveryLineRepository extends JpaRepository<DeliveryLine, Long> {
    List<DeliveryLine> findByDeliveryId(Long deliveryId);
}
