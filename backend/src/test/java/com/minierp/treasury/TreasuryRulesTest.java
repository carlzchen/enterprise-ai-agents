package com.minierp.treasury;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class TreasuryRulesTest {
    private static final LocalDate D = LocalDate.of(2026, 8, 31);

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }

    @Test
    void bankReconciliationWithOutstandingItems() {
        // 企业：收 1000（8/30）、付 −300（8/31，银行 9/2 才扣）；银行：收 1000（8/31）、利息 +50、手续费 −20
        var book = List.of(new BankReconciler.Item(1, D.minusDays(1), bd("1000")), new BankReconciler.Item(2, D, bd("-300")));
        var bank = List.of(new BankReconciler.Item(11, D, bd("1000")), new BankReconciler.Item(12, D, bd("50")), new BankReconciler.Item(13, D, bd("-20")));
        var r = BankReconciler.reconcile(book, bank, 3, bd("5700"), bd("6030"));
        assertThat(r.matches()).containsExactly(new BankReconciler.Match(1, 11));
        assertThat(r.bookOnly()).extracting(BankReconciler.Item::id).containsExactly(2L);
        assertThat(r.bankOnly()).extracting(BankReconciler.Item::id).containsExactlyInAnyOrder(12L, 13L);
        assertThat(r.adjustedBook()).isEqualByComparingTo("5730");
        assertThat(r.adjustedBank()).isEqualByComparingTo("5730");
        assertThat(r.balanced()).isTrue();
    }

    @Test
    void reconcilerPrefersNearestDateAndRespectsWindow() {
        var book = List.of(new BankReconciler.Item(1, D.minusDays(3), bd("-500")), new BankReconciler.Item(2, D, bd("-500")));
        var bank = List.of(new BankReconciler.Item(11, D.plusDays(1), bd("-500")));
        var r = BankReconciler.reconcile(book, bank, 2, BigDecimal.ZERO, BigDecimal.ZERO);
        assertThat(r.matches()).containsExactly(new BankReconciler.Match(2, 11));
    }

    @Test
    void cashForecastWeeksAndSafetyAlert() {
        var f = CashForecaster.weekly(D, 3, bd("1000"), List.of(
                new CashForecaster.Flow(D.minusDays(5), bd("200"), "逾期应收"), new CashForecaster.Flow(D.plusDays(8), bd("-1500"), "应付"),
                new CashForecaster.Flow(D.plusDays(16), bd("900"), "应收")), bd("0"));
        assertThat(f.weeks()).extracting(CashForecaster.Week::ending).usingElementComparator(BigDecimal::compareTo)
                .containsExactly(bd("1200"), bd("-300"), bd("600"));
        assertThat(f.minBalance()).isEqualByComparingTo("-300");
        assertThat(f.firstBelowSafety()).isEqualTo(D.plusWeeks(1));
    }

    @Test
    void budgetStatus() {
        assertThat(BudgetControl.evaluate(bd("10000"), bd("6000"), bd("1000"), bd("1500")).status()).isEqualTo("OK");
        assertThat(BudgetControl.evaluate(bd("10000"), bd("6000"), bd("2000"), bd("1500")).status()).isEqualTo("WARN");
        assertThat(BudgetControl.evaluate(bd("10000"), bd("9000"), bd("0"), bd("1500")).status()).isEqualTo("OVER");
        assertThat(BudgetControl.evaluate(null, bd("0"), bd("0"), bd("1")).status()).isEqualTo("OVER");
    }

    @Test
    void referenceMatchSpansHolidaysAndUntanglesSameAmounts() {
        // 两张同金额发票：按日期会交叉配对，按附言则各自对上；春节延迟 9 天也能靠附言匹配
        var book = List.of(new BankReconciler.Item(1, D, bd("-100"), "支付货款 PI-A"), new BankReconciler.Item(2, D.plusDays(5), bd("-100"), "支付货款 PI-B"),
                new BankReconciler.Item(3, D, bd("-900"), "支付货款 PI-C"));
        var bank = List.of(new BankReconciler.Item(11, D.plusDays(6), bd("-100"), "支付货款 PI-A"), new BankReconciler.Item(12, D.plusDays(8), bd("-100"), "支付货款 PI-B"),
                new BankReconciler.Item(13, D.plusDays(9), bd("-900"), "支付货款 PI-C"));
        var r = BankReconciler.reconcile(book, bank, 7, BigDecimal.ZERO, BigDecimal.ZERO);
        assertThat(r.matches()).containsExactlyInAnyOrder(new BankReconciler.Match(1, 11), new BankReconciler.Match(2, 12), new BankReconciler.Match(3, 13));
        assertThat(r.bookOnly()).isEmpty();
        assertThat(r.bankOnly()).isEmpty();
    }
}
