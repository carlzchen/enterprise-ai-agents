package com.minierp.treasury;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * 预算控制（纯函数）：执行后使用率 = (已发生 + 审批中 + 本次) / 预算。
 * ≤ 90% 正常；90%~100% 预警；> 100% 超预算（报销流程增加总经理审批）；没有预算视为超预算。
 */
public final class BudgetControl {
    private BudgetControl() {
    }

    public record Check(String status, BigDecimal usageAfter, BigDecimal remaining) {
    }

    public static Check evaluate(BigDecimal budget, BigDecimal used, BigDecimal committed, BigDecimal amount) {
        BigDecimal after = used.add(committed).add(amount);
        if (budget == null || budget.signum() <= 0) {
            return new Check("OVER", null, BigDecimal.ZERO.subtract(after));
        }
        BigDecimal rate = after.divide(budget, 4, RoundingMode.HALF_UP);
        String status = rate.compareTo(new BigDecimal("0.9")) <= 0 ? "OK" : rate.compareTo(BigDecimal.ONE) <= 0 ? "WARN" : "OVER";
        return new Check(status, rate, budget.subtract(after));
    }
}
