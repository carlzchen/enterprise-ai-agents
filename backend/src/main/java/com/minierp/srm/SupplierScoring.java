package com.minierp.srm;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** 供应商绩效评分（纯函数）：质量 50 + 交付 30 + 价格 20 */
public final class SupplierScoring {
    private SupplierScoring() {
    }

    public record Score(BigDecimal quality, BigDecimal delivery, BigDecimal price, BigDecimal total, String grade) {
    }

    /** 价格：不高于参考价得满分，每高出 1% 扣 2 分，最低 0 分 */
    public static Score score(double passRate, double onTimeRate, double priceIndex) {
        double q = 50 * clamp(passRate), d = 30 * clamp(onTimeRate);
        double p = priceIndex <= 1.0 ? 20 : Math.max(0, 20 - (priceIndex - 1.0) * 200);
        double total = q + d + p;
        String grade = total >= 90 ? "A" : total >= 80 ? "B" : total >= 70 ? "C" : "D";
        return new Score(bd(q), bd(d), bd(p), bd(total), grade);
    }

    private static double clamp(double v) {
        return Math.max(0, Math.min(1, v));
    }

    private static BigDecimal bd(double v) {
        return BigDecimal.valueOf(v).setScale(2, RoundingMode.HALF_UP);
    }
}
