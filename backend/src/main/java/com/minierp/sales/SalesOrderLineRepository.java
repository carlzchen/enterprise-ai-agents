package com.minierp.sales;

import org.springframework.data.jpa.repository.JpaRepository;
import java.util.List;

public interface SalesOrderLineRepository extends JpaRepository<SalesOrderLine, Long> {
    List<SalesOrderLine> findByOrderIdOrderByLineNo(Long orderId);
    List<SalesOrderLine> findByOrderIdIn(java.util.Collection<Long> orderIds);
}
