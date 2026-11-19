package com.minierp.inventory;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** 存货计价（纯函数） */
public final class CostMath {
    private CostMath() {
    }

    /** 移动加权平均：(原数量×原单价 + 入库数量×入库单价) / (原数量 + 入库数量)；原数量为负时按 0 处理 */
    public static BigDecimal movingAverage(BigDecimal oldQty, BigDecimal oldAvg, BigDecimal inQty, BigDecimal inCost) {
        BigDecimal base = oldQty.max(BigDecimal.ZERO);
        BigDecimal total = base.add(inQty);
        if (total.signum() <= 0) {
            return inCost.setScale(4, RoundingMode.HALF_UP);
        }
        return base.multiply(oldAvg).add(inQty.multiply(inCost)).divide(total, 4, RoundingMode.HALF_UP);
    }
}
