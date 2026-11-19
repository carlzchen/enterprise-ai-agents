package com.minierp.ehs;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class EhsRulesTest {
    @Test
    void safetyRates() {
        var k = EhsRules.safety(1_000_000, 2, 3, 45);
        assertThat(k.ltifr()).isEqualByComparingTo("2.00");
        assertThat(k.trir()).isEqualByComparingTo("1.00");   // (3 + 2) × 200,000 ÷ 1,000,000
        assertThat(k.severity()).isEqualByComparingTo("45.00");
        assertThat(EhsRules.safety(0, 1, 0, 0).ltifr()).isZero();
    }

    @Test
    void hazardDeadlines() {
        LocalDate found = LocalDate.of(2026, 3, 2);
        assertThat(EhsRules.hazardDeadline("GENERAL", found)).isEqualTo(LocalDate.of(2026, 3, 9));
        assertThat(EhsRules.hazardDeadline("MAJOR", found)).isEqualTo(LocalDate.of(2026, 4, 1));
        assertThat(EhsRules.overdue(LocalDate.of(2026, 3, 9), null, LocalDate.of(2026, 3, 10))).isTrue();
        assertThat(EhsRules.overdue(LocalDate.of(2026, 3, 9), LocalDate.of(2026, 3, 9), LocalDate.of(2026, 4, 1))).isFalse();
    }

    @Test
    void wasteStorageFifo() {
        var moves = List.of(new EhsRules.WasteMove(LocalDate.of(2025, 1, 10), new BigDecimal("1.0")),
                new EhsRules.WasteMove(LocalDate.of(2025, 4, 10), new BigDecimal("2.0")),
                new EhsRules.WasteMove(LocalDate.of(2025, 6, 1), new BigDecimal("-1.5")));
        var s = EhsRules.wasteStock(moves, LocalDate.of(2025, 12, 31));
        assertThat(s.qty()).isEqualByComparingTo("1.5");
        assertThat(s.oldest()).isEqualTo(LocalDate.of(2025, 4, 10));
        assertThat(s.days()).isEqualTo(265);
        assertThat(s.status()).isEqualTo("OK");
        assertThat(EhsRules.wasteStock(moves, LocalDate.of(2026, 1, 10)).status()).isEqualTo("WARNING");
        assertThat(EhsRules.wasteStock(moves, LocalDate.of(2026, 4, 11)).status()).isEqualTo("VIOLATION");
    }
}
