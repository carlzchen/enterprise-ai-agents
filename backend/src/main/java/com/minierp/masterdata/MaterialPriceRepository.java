package com.minierp.masterdata;

import org.springframework.data.jpa.repository.JpaRepository;

public interface MaterialPriceRepository extends JpaRepository<MaterialPrice, Long> {
    java.util.Optional<MaterialPrice> findTopByMaterialIdAndPriceMonthLessThanEqualOrderByPriceMonthDesc(Long materialId, java.time.LocalDate month);
}
