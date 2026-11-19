package com.minierp.fa;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.ArrayList;
import java.util.List;

/**
 * 固定资产折旧（纯函数）：
 * - 当月增加的固定资产当月不提、次月起提；当月减少的当月照提、次月停提；提足后不再计提；
 * - 年限平均法：月折旧额 = 原值 × (1 − 净残值率) / 使用月数；
 * - 双倍余额递减法：年折旧率 = 2 / 使用年数，按期初账面净值计算，最后两年改为年限平均法把账面净值扣除净残值后平均摊销；
 * - 月折旧额按"计划到该月的累计折旧 − 已提累计折旧"计算，四舍五入误差不会累积，最后一个月自动补齐尾差。
 */
public final class DepreciationCalculator {
    private DepreciationCalculator() {
    }

    public static YearMonth firstPeriod(LocalDate inService) {
        return YearMonth.from(inService).plusMonths(1);
    }

    /** 计划累计折旧：计提到第 n 个月（n 从 1 开始）为止 */
    public static BigDecimal plannedAccumulated(String method, BigDecimal cost, BigDecimal residualRate, int lifeMonths, int n) {
        BigDecimal depreciable = cost.multiply(BigDecimal.ONE.subtract(residualRate));
        if (n <= 0) {
            return BigDecimal.ZERO;
        }
        if (n >= lifeMonths) {
            return depreciable.setScale(2, RoundingMode.HALF_UP);
        }
        if ("SL".equals(method)) {
            return depreciable.multiply(BigDecimal.valueOf(n)).divide(BigDecimal.valueOf(lifeMonths), 2, RoundingMode.HALF_UP);
        }
        int years = lifeMonths / 12;
        double residual = cost.doubleValue() * residualRate.doubleValue();
        double nbv = cost.doubleValue(), acc = 0;
        for (int y = 0; y < years; y++) {
            double yearly = y < years - 2 ? nbv * 2.0 / years : (cost.doubleValue() - residual - sumBefore(cost.doubleValue(), years, years - 2)) / 2;
            int monthsInYear = Math.min(12, n - y * 12);
            if (monthsInYear <= 0) {
                break;
            }
            acc += yearly * monthsInYear / 12.0;
            nbv -= yearly;
        }
        return BigDecimal.valueOf(acc).setScale(2, RoundingMode.HALF_UP).min(depreciable.setScale(2, RoundingMode.HALF_UP));
    }

    /** 双倍余额递减法前 k 年累计折旧 */
    private static double sumBefore(double cost, int years, int k) {
        double nbv = cost, acc = 0;
        for (int y = 0; y < k; y++) {
            double d = nbv * 2.0 / years;
            acc += d;
            nbv -= d;
        }
        return acc;
    }

    /** 某期间应提折旧额：period 不在计提区间内返回 0 */
    public static BigDecimal forPeriod(String method, BigDecimal cost, BigDecimal residualRate, int lifeMonths, LocalDate inService,
                                       BigDecimal accumulatedBefore, YearMonth period) {
        int n = (int) (period.getYear() * 12L + period.getMonthValue() - (firstPeriod(inService).getYear() * 12L + firstPeriod(inService).getMonthValue())) + 1;
        if (n < 1 || n > lifeMonths) {
            return BigDecimal.ZERO;
        }
        return plannedAccumulated(method, cost, residualRate, lifeMonths, n).subtract(accumulatedBefore).max(BigDecimal.ZERO);
    }

    public record Row(YearMonth period, BigDecimal amount, BigDecimal accumulated) {
    }

    public static List<Row> schedule(String method, BigDecimal cost, BigDecimal residualRate, int lifeMonths, LocalDate inService) {
        List<Row> rows = new ArrayList<>();
        BigDecimal acc = BigDecimal.ZERO;
        YearMonth p = firstPeriod(inService);
        for (int i = 0; i < lifeMonths; i++, p = p.plusMonths(1)) {
            BigDecimal amt = forPeriod(method, cost, residualRate, lifeMonths, inService, acc, p);
            acc = acc.add(amt);
            rows.add(new Row(p, amt, acc));
        }
        return rows;
    }

    /** 处置损益 = 处置收入 − 账面净值 − 清理费用（正数为收益） */
    public static BigDecimal disposalGain(BigDecimal cost, BigDecimal accumulated, BigDecimal proceeds, BigDecimal clearingCost) {
        return proceeds.subtract(cost.subtract(accumulated)).subtract(clearingCost).setScale(2, RoundingMode.HALF_UP);
    }
}
