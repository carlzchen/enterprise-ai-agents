package com.minierp.transfer;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class ReplenishmentCalculatorTest {
    private static final BigDecimal TEN = BigDecimal.TEN;

    @Test
    void noReplenishmentAboveReorderPoint() {
        // 日均 50，订货点 7 天 = 350；在库 300 + 在途 100 = 400
        assertThat(ReplenishmentCalculator.suggest(new BigDecimal("50"), 7, 14, new BigDecimal("300"), new BigDecimal("100"),
                new BigDecimal("9999"), TEN)).isZero();
    }

    @Test
    void replenishesToTargetRoundedToPack() {
        // 目标 14 天 = 700，可用 205 → 缺 495 → 按 10 取整 500
        assertThat(ReplenishmentCalculator.suggest(new BigDecimal("50"), 7, 14, new BigDecimal("205"), BigDecimal.ZERO,
                new BigDecimal("9999"), TEN)).isEqualByComparingTo("500");
    }

    @Test
    void cappedBySourceAvailability() {
        assertThat(ReplenishmentCalculator.suggest(new BigDecimal("50"), 7, 14, BigDecimal.ZERO, BigDecimal.ZERO,
                new BigDecimal("237"), TEN)).isEqualByComparingTo("230");
    }

    @Test
    void routerShipsFromRegionalWarehouseOnlyWhenWholeOrderCovered() {
        Map<Long, BigDecimal> dcStock = Map.of(1L, new BigDecimal("100"), 2L, new BigDecimal("5"));
        assertThat(WarehouseRouter.choose(9L, dcStock, Map.of(1L, new BigDecimal("80")), 1L)).isEqualTo(9L);
        assertThat(WarehouseRouter.choose(9L, dcStock, Map.of(1L, new BigDecimal("80"), 2L, TEN), 1L)).isEqualTo(1L);
        assertThat(WarehouseRouter.choose(null, dcStock, Map.of(1L, BigDecimal.ONE), 1L)).isEqualTo(1L);
    }
}
