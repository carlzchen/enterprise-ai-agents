package com.minierp.hr;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * 工资薪金个税：累计预扣法（纯函数）。
 * 本期应预扣 = 累计应纳税所得额 × 预扣率 - 速算扣除数 - 本年已预扣；
 * 累计应纳税所得额 = 累计收入 - 累计减除费用（5000 元/月）- 累计专项扣除（个人社保公积金）。
 */
public final class IncomeTaxCalculator {
    private IncomeTaxCalculator() {
    }

    private static final long[][] BRACKETS = { // 上限, 税率%, 速算扣除数
            {36_000, 3, 0}, {144_000, 10, 2_520}, {300_000, 20, 16_920}, {420_000, 25, 31_920},
            {660_000, 30, 52_920}, {960_000, 35, 85_920}, {Long.MAX_VALUE, 45, 181_920}};

    public static final BigDecimal MONTHLY_DEDUCTION = new BigDecimal("5000");

    /** 按累计应纳税所得额计算累计应纳税额 */
    public static BigDecimal cumulativeTax(BigDecimal ytdTaxable) {
        if (ytdTaxable.signum() <= 0) {
            return BigDecimal.ZERO;
        }
        for (long[] b : BRACKETS) {
            if (ytdTaxable.compareTo(BigDecimal.valueOf(b[0])) <= 0) {
                return ytdTaxable.multiply(BigDecimal.valueOf(b[1])).divide(BigDecimal.valueOf(100))
                        .subtract(BigDecimal.valueOf(b[2])).setScale(2, RoundingMode.HALF_UP);
            }
        }
        throw new IllegalStateException();
    }

    /** 本月应预扣个税，不为负（累计已多扣时本月为 0，差额年度汇算退还） */
    public static BigDecimal monthlyWithholding(BigDecimal ytdTaxableIncludingThisMonth, BigDecimal ytdTaxBefore) {
        return cumulativeTax(ytdTaxableIncludingThisMonth).subtract(ytdTaxBefore).max(BigDecimal.ZERO).setScale(2, RoundingMode.HALF_UP);
    }
}
