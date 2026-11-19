package com.minierp.purchase;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * 采购价差（纯函数）：发票金额 - 暂估金额（订单价 × 合格数量）。
 * 实际成本法下，该批次仍在库的部分调整存货成本，已领用的部分直接计入当期主营业务成本。
 */
public final class PriceVariance {
    private PriceVariance() {
    }

    public record Split(BigDecimal variance, BigDecimal toStock, BigDecimal toCogs) {
    }

    public static Split split(BigDecimal estimate, BigDecimal actual, BigDecimal inStockRatio) {
        BigDecimal variance = actual.subtract(estimate).setScale(2, RoundingMode.HALF_UP);
        BigDecimal ratio = inStockRatio.max(BigDecimal.ZERO).min(BigDecimal.ONE);
        BigDecimal toStock = variance.multiply(ratio).setScale(2, RoundingMode.HALF_UP);
        return new Split(variance, toStock, variance.subtract(toStock));
    }

    /** 价差率超过容差需要审批后才能记账 */
    public static boolean needsApproval(BigDecimal estimate, BigDecimal actual, BigDecimal tolerance) {
        if (estimate.signum() == 0) {
            return actual.signum() != 0;
        }
        return actual.subtract(estimate).abs().divide(estimate.abs(), 6, RoundingMode.HALF_UP).compareTo(tolerance) > 0;
    }
}
