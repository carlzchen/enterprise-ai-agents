package com.minierp.masterdata;

import org.springframework.data.jpa.repository.JpaRepository;

public interface BomItemRepository extends JpaRepository<BomItem, Long> {
    java.util.List<BomItem> findByProductId(Long productId);
    void deleteByProductId(Long productId);
}
