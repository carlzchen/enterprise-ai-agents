package com.minierp.customs;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;

class ExportRefundCalculatorTest {
    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }

    @Test
    void textbookCaseWithRateDifferenceAndRefund() {
        // 教材例：征 13% 退 10%，内销销项 13 万，进项 40 万，出口 FOB 200 万，上期留抵 0
        var r = ExportRefundCalculator.compute(bd("130000"), bd("400000"), BigDecimal.ZERO, bd("2000000"), bd("0.13"), bd("0.10"));
        assertThat(r.nonDeductible()).isEqualByComparingTo("60000");  // 200 万 × 3%
        assertThat(r.payable()).isEqualByComparingTo("-210000");       // 13 − (40 − 6)
        assertThat(r.exemptRefund()).isEqualByComparingTo("200000");
        assertThat(r.refund()).isEqualByComparingTo("200000");         // 留抵 21 万 > 免抵退 20 万
        assertThat(r.exemptOffset()).isEqualByComparingTo("0");
        assertThat(r.endCredit()).isEqualByComparingTo("10000");
    }

    @Test
    void domesticPayableMeansAllExemptOffsetAndSurtaxBaseIncludesIt() {
        var r = ExportRefundCalculator.compute(bd("200000"), bd("150000"), BigDecimal.ZERO, bd("300000"), bd("0.13"), bd("0.13"));
        assertThat(r.payable()).isEqualByComparingTo("50000");
        assertThat(r.refund()).isZero();
        assertThat(r.exemptOffset()).isEqualByComparingTo("39000");
        assertThat(r.surtax()).isEqualByComparingTo("10680");           // (5 万 + 3.9 万) × 12%
    }

    @Test
    void partialRefundLimitedByCredit() {
        var r = ExportRefundCalculator.compute(bd("100000"), bd("150000"), bd("20000"), bd("1000000"), bd("0.13"), bd("0.13"));
        assertThat(r.payable()).isEqualByComparingTo("-70000");
        assertThat(r.refund()).isEqualByComparingTo("70000");
        assertThat(r.exemptOffset()).isEqualByComparingTo("60000");
        assertThat(r.endCredit()).isZero();
    }

    @Test
    void hsClassification() {
        assertThat(HsClassifier.classify("SERVO", bd("750")).code()).isEqualTo("8501510010");
        assertThat(HsClassifier.classify("SERVO", bd("1500")).code()).isEqualTo("8501520000");
        assertThat(HsClassifier.classify("CROSSFLOW", bd("20")).code()).startsWith("8414");
    }
}
