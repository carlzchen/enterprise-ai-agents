package com.minierp.transfer;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * 前置仓补货（纯函数，订货点法）：可用量（在库 + 在途）低于"日均需求 × 订货点天数"时，
 * 补到"日均需求 × 目标覆盖天数"，按包装倍数取整，且不超过总仓可调出数量。
 */
public final class ReplenishmentCalculator {
    private ReplenishmentCalculator() {
    }

    public static BigDecimal suggest(BigDecimal dailyDemand, int reorderDays, int targetDays, BigDecimal onHand, BigDecimal inTransit,
                                     BigDecimal sourceAvailable, BigDecimal packSize) {
        if (dailyDemand.signum() <= 0) {
            return BigDecimal.ZERO;
        }
        BigDecimal position = onHand.add(inTransit);
        if (position.compareTo(dailyDemand.multiply(BigDecimal.valueOf(reorderDays))) >= 0) {
            return BigDecimal.ZERO;
        }
        BigDecimal need = dailyDemand.multiply(BigDecimal.valueOf(targetDays)).subtract(position);
        BigDecimal packs = need.divide(packSize, 0, RoundingMode.CEILING);
        BigDecimal qty = packs.multiply(packSize);
        BigDecimal cap = sourceAvailable.max(BigDecimal.ZERO).divide(packSize, 0, RoundingMode.FLOOR).multiply(packSize);
        return qty.min(cap).max(BigDecimal.ZERO);
    }
}
