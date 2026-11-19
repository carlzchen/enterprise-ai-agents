package com.minierp.masterdata;

import org.springframework.data.jpa.repository.JpaRepository;

public interface WarehouseRepository extends JpaRepository<Warehouse, Long> {
    java.util.Optional<Warehouse> findByType(String type);
}
