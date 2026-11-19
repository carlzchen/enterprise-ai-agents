package com.minierp.purchase;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;

class PriceVarianceTest {
    @Test
    void splitsVarianceByInStockRatio() {
        var s = PriceVariance.split(new BigDecimal("10000.00"), new BigDecimal("10450.00"), new BigDecimal("0.4"));
        assertThat(s.variance()).isEqualByComparingTo("450");
        assertThat(s.toStock()).isEqualByComparingTo("180");
        assertThat(s.toCogs()).isEqualByComparingTo("270");
    }

    @Test
    void negativeVarianceAndFullyConsumedLot() {
        var s = PriceVariance.split(new BigDecimal("5000"), new BigDecimal("4900"), BigDecimal.ZERO);
        assertThat(s.toStock()).isEqualByComparingTo("0");
        assertThat(s.toCogs()).isEqualByComparingTo("-100");
    }

    @Test
    void roundingKeepsPartsSummingToVariance() {
        var s = PriceVariance.split(new BigDecimal("100"), new BigDecimal("100.01"), new BigDecimal("0.5"));
        assertThat(s.toStock().add(s.toCogs())).isEqualByComparingTo(s.variance());
    }

    @Test
    void approvalRequiredOnlyAboveTolerance() {
        BigDecimal tol = new BigDecimal("0.03");
        assertThat(PriceVariance.needsApproval(new BigDecimal("1000"), new BigDecimal("1030"), tol)).isFalse();
        assertThat(PriceVariance.needsApproval(new BigDecimal("1000"), new BigDecimal("1030.01"), tol)).isTrue();
        assertThat(PriceVariance.needsApproval(new BigDecimal("1000"), new BigDecimal("960"), tol)).isTrue();
    }
}
