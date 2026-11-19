package com.minierp.hr;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class PayrollRulesTest {
    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }

    @Test
    void cumulativeWithholdingAcrossBrackets() {
        // 1 月累计应税 30000 → 900；2 月累计 60000 → 60000×10%-2520 = 3480，本月扣 2580
        assertThat(IncomeTaxCalculator.monthlyWithholding(bd("30000"), BigDecimal.ZERO)).isEqualByComparingTo("900");
        assertThat(IncomeTaxCalculator.monthlyWithholding(bd("60000"), bd("900"))).isEqualByComparingTo("2580");
        assertThat(IncomeTaxCalculator.monthlyWithholding(bd("-2000"), BigDecimal.ZERO)).isEqualByComparingTo("0");
    }

    @Test
    void monthlyStaffWithUnpaidLeaveAndOvertime() {
        var r = PayrollCalculator.compute(new PayrollCalculator.Input("MONTHLY", bd("10000"), bd("10000"), 22, 22,
                bd("20"), BigDecimal.ZERO, bd("2"), bd("4"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO));
        assertThat(r.basePay()).isEqualByComparingTo("10000");
        assertThat(r.deduction()).isEqualByComparingTo("919.54"); // 10000/21.75×2
        assertThat(r.overtimePay()).isEqualByComparingTo("344.83"); // 10000/21.75/8×1.5×4
        assertThat(r.socialEmployee()).isEqualByComparingTo("1530");
        assertThat(r.gross().subtract(r.socialEmployee()).subtract(r.tax())).isEqualByComparingTo(r.net());
    }

    @Test
    void pieceWorkerGetsGuaranteeTopUp() {
        // 保底 2360，出勤 11/22 天 → 保底 1180；计件只有 900 → 补 280
        var r = PayrollCalculator.compute(new PayrollCalculator.Input("PIECE", bd("2360"), bd("2360"), 22, 22,
                bd("11"), BigDecimal.ZERO, bd("11"), BigDecimal.ZERO, bd("900"), BigDecimal.ZERO, BigDecimal.ZERO));
        assertThat(r.topup()).isEqualByComparingTo("280");
        assertThat(r.gross()).isEqualByComparingTo("1180");
        assertThat(r.tax()).isEqualByComparingTo("0");
    }

    @Test
    void midMonthHireProratesBaseAndSocialBaseIsClamped() {
        var r = PayrollCalculator.compute(new PayrollCalculator.Input("MONTHLY", bd("8800"), bd("50000"), 22, 11,
                bd("11"), BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO));
        assertThat(r.basePay()).isEqualByComparingTo("4400");
        assertThat(r.socialEmployee()).isEqualByComparingTo("4207.65"); // 上限 27501 × 15.3%
    }

    @Test
    void teamPieceWageSplitByHoursSumsExactly() {
        Map<Long, BigDecimal> alloc = PieceWageAllocator.allocate(bd("1000.00"), Map.of(1L, bd("176"), 2L, bd("176"), 3L, bd("88")));
        assertThat(alloc.get(1L)).isEqualByComparingTo("400");
        assertThat(alloc.get(3L)).isEqualByComparingTo("200");
        Map<Long, BigDecimal> odd = PieceWageAllocator.allocate(bd("100.00"), Map.of(1L, BigDecimal.ONE, 2L, BigDecimal.ONE, 3L, BigDecimal.ONE));
        assertThat(odd.values().stream().reduce(BigDecimal.ZERO, BigDecimal::add)).isEqualByComparingTo("100");
    }
}
