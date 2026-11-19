package com.minierp.sales;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;

class CreditPolicyTest {
    @Test
    void withinLimitIsAllowed() {
        assertThat(CreditPolicy.exceeds(new BigDecimal("1000"), new BigDecimal("600"), new BigDecimal("400"))).isFalse();
    }

    @Test
    void exceedingLimitIsBlocked() {
        assertThat(CreditPolicy.exceeds(new BigDecimal("1000"), new BigDecimal("600"), new BigDecimal("400.01"))).isTrue();
    }

    @Test
    void zeroLimitBlocksAnyCreditSale() {
        assertThat(CreditPolicy.exceeds(BigDecimal.ZERO, BigDecimal.ZERO, new BigDecimal("0.01"))).isTrue();
    }
}
