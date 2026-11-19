package com.minierp.masterdata;

import org.springframework.data.jpa.repository.JpaRepository;

public interface FxRateRepository extends JpaRepository<FxRate, Long> {
    java.util.Optional<FxRate> findTopByCurrencyAndRateMonthLessThanEqualOrderByRateMonthDesc(String currency, java.time.LocalDate month);
}
