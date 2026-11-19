package com.minierp.masterdata;

import org.springframework.data.jpa.repository.JpaRepository;

public interface SupplierRepository extends JpaRepository<Supplier, Long> {
    boolean existsByCode(String code);
    java.util.List<Supplier> findByCategoryAndStatus(String category, String status);
}
