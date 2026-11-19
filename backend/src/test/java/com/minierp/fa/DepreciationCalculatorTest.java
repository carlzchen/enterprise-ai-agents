package com.minierp.fa;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.YearMonth;

import static org.assertj.core.api.Assertions.assertThat;

class DepreciationCalculatorTest {
    private static final BigDecimal COST = new BigDecimal("120000"), R5 = new BigDecimal("0.05");

    @Test
    void straightLineStartsNextMonthAndSumsToDepreciable() {
        var rows = DepreciationCalculator.schedule("SL", COST, R5, 60, LocalDate.of(2025, 3, 15));
        assertThat(rows.get(0).period()).isEqualTo(YearMonth.of(2025, 4)); // 当月增加次月起提
        assertThat(rows.get(0).amount()).isEqualByComparingTo("1900");      // 120000×95%/60
        assertThat(rows.get(rows.size() - 1).accumulated()).isEqualByComparingTo("114000");
    }

    @Test
    void doubleDecliningSwitchesToStraightLineInLastTwoYears() {
        // 5 年、原值 120000、残值 6000：前三年 48000、28800、17280；后两年各 (120000−6000−94080)/2 = 9960
        var rows = DepreciationCalculator.schedule("DDB", COST, R5, 60, LocalDate.of(2024, 12, 20));
        BigDecimal y1 = rows.subList(0, 12).stream().map(DepreciationCalculator.Row::amount).reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal y5 = rows.subList(48, 60).stream().map(DepreciationCalculator.Row::amount).reduce(BigDecimal.ZERO, BigDecimal::add);
        assertThat(y1).isEqualByComparingTo("48000");
        assertThat(y5).isEqualByComparingTo("9960");
        assertThat(rows.get(59).accumulated()).isEqualByComparingTo("114000");
    }

    @Test
    void noDepreciationOutsideLifeAndDisposalGain() {
        assertThat(DepreciationCalculator.forPeriod("SL", COST, R5, 60, LocalDate.of(2025, 3, 1), BigDecimal.ZERO, YearMonth.of(2025, 3))).isZero();
        assertThat(DepreciationCalculator.forPeriod("SL", COST, R5, 60, LocalDate.of(2020, 1, 1), new BigDecimal("114000"), YearMonth.of(2025, 6))).isZero();
        // 原值 120000，已提 100000，卖 30000，清理费 2000 → 收益 8000
        assertThat(DepreciationCalculator.disposalGain(COST, new BigDecimal("100000"), new BigDecimal("30000"), new BigDecimal("2000"))).isEqualByComparingTo("8000");
    }
}
