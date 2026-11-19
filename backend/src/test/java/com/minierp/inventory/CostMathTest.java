package com.minierp.inventory;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;

class CostMathTest {
    private static BigDecimal d(String v) {
        return new BigDecimal(v);
    }

    @Test
    void weightedAverageOfExistingAndIncomingStock() {
        // 100 件 @10 + 50 件 @16 = 1800 / 150 = 12
        assertThat(CostMath.movingAverage(d("100"), d("10"), d("50"), d("16"))).isEqualByComparingTo("12.0000");
    }

    @Test
    void emptyStockTakesIncomingCost() {
        assertThat(CostMath.movingAverage(BigDecimal.ZERO, d("99"), d("10"), d("7.5"))).isEqualByComparingTo("7.5");
    }

    @Test
    void negativeStockIsTreatedAsZero() {
        assertThat(CostMath.movingAverage(d("-5"), d("20"), d("10"), d("8"))).isEqualByComparingTo("8");
    }

    @Test
    void resultIsRoundedToFourDecimals() {
        assertThat(CostMath.movingAverage(d("3"), d("1"), d("0"), d("0")).scale()).isEqualTo(4);
        assertThat(CostMath.movingAverage(d("1"), d("1"), d("2"), d("2"))).isEqualByComparingTo("1.6667");
    }
}
