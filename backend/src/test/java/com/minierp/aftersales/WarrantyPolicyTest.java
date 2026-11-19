package com.minierp.aftersales;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;

class WarrantyPolicyTest {
    @Test
    void servoHasEighteenMonths() {
        LocalDate ship = LocalDate.of(2025, 1, 10);
        assertThat(WarrantyPolicy.end("SERVO", ship)).isEqualTo(LocalDate.of(2026, 7, 10));
        assertThat(WarrantyPolicy.inWarranty("SERVO", ship, LocalDate.of(2026, 7, 10))).isTrue();
        assertThat(WarrantyPolicy.inWarranty("SERVO", ship, LocalDate.of(2026, 7, 11))).isFalse();
        assertThat(WarrantyPolicy.inWarranty("BLDC", ship, LocalDate.of(2026, 2, 1))).isFalse();
    }

    @Test
    void provisionTrueUpAndNextRate() {
        // 近 12 个月索赔 120 万 → 月均 10 万 × 在保平均剩余 7.5 个月 = 75 万；账面 60 万需补提 15 万
        assertThat(WarrantyPolicy.requiredBalance(new BigDecimal("1200000"), 15)).isEqualByComparingTo("750000");
        assertThat(WarrantyPolicy.trueUp(new BigDecimal("600000"), new BigDecimal("1200000"), 15)).isEqualByComparingTo("150000");
        assertThat(WarrantyPolicy.nextRate(new BigDecimal("1500000"), new BigDecimal("300000000"))).isEqualByComparingTo("0.0055");
        assertThat(WarrantyPolicy.nextRate(BigDecimal.ZERO, new BigDecimal("300000000"))).isEqualByComparingTo("0.0005");
        assertThat(WarrantyPolicy.accrual(new BigDecimal("25000000"), new BigDecimal("0.006"))).isEqualByComparingTo("150000");
    }
}
