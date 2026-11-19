package com.minierp.gl;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

class ClosingCalculatorTest {
    private static final Set<String> ACCOUNTS = Set.of("5101", "6001", "6401", "6601", "6602", "6603", "6711", "4103");

    @Test
    void profitIsTransferredAndVouchersBalance() {
        // 收入 1000（贷方），成本 700，销售费用 100，管理费用 50；制造费用多分配 20（贷方余额）
        var r = ClosingCalculator.close(Map.of("6001", new BigDecimal("-1000"), "6401", new BigDecimal("700"),
                "6601", new BigDecimal("100"), "6602", new BigDecimal("50"), "5101", new BigDecimal("-20")));
        assertThat(r.profit()).isEqualByComparingTo("170"); // 1000 - (700 - 20) - 100 - 50
        VoucherValidator.validate(r.overheadLines(), ACCOUNTS);
        VoucherValidator.validate(r.profitLines(), ACCOUNTS);
        assertThat(r.profitLines()).anySatisfy(l -> {
            assertThat(l.account()).isEqualTo("4103");
            assertThat(l.credit()).isEqualByComparingTo("170");
        });
    }

    @Test
    void lossIsDebitedToCurrentYearProfit() {
        var r = ClosingCalculator.close(Map.of("6001", new BigDecimal("-100"), "6602", new BigDecimal("300")));
        assertThat(r.profit()).isEqualByComparingTo("-200");
        assertThat(r.profitLines()).anySatisfy(l -> assertThat(l.account() + l.debit()).isEqualTo("4103200"));
        assertThat(r.overheadLines()).isEmpty();
    }

    @Test
    void underAppliedOverheadIncreasesCost() {
        var r = ClosingCalculator.close(Map.of("5101", new BigDecimal("30"), "6001", new BigDecimal("-100")));
        assertThat(r.overheadLines()).extracting(VoucherValidator.Line::account).containsExactly("6401", "5101");
        assertThat(r.profit()).isEqualByComparingTo("70");
    }

    @Test
    void emptyPeriodProducesNothing() {
        var r = ClosingCalculator.close(Map.of());
        assertThat(r.overheadLines()).isEmpty();
        assertThat(r.profitLines()).isEqualTo(List.of());
    }
}
