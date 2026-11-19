package com.minierp.eam;

/**
 * OEE = 时间开动率 × 性能开动率 × 合格品率（纯函数）。
 * 时间开动率 = (计划时间 - 停机损失) / 计划时间；性能开动率 = 实际产量 / (理论产能 × 时间开动率)，封顶 1；合格品率 = 良品 / 总产量。
 */
public final class OeeCalculator {
    private OeeCalculator() {
    }

    public record Oee(double availability, double performance, double quality, double oee) {
    }

    public static Oee compute(int plannedMin, int lossMin, double totalQty, double goodQty, double capacityPerDay) {
        if (plannedMin <= 0) {
            return new Oee(0, 0, 0, 0);
        }
        double a = Math.max(0, Math.min(1, (plannedMin - lossMin) / (double) plannedMin));
        double p = a <= 0 || capacityPerDay <= 0 ? 0 : Math.min(1, totalQty / (capacityPerDay * a));
        double q = totalQty <= 0 ? 0 : Math.min(1, goodQty / totalQty);
        return new Oee(round(a), round(p), round(q), round(a * p * q));
    }

    private static double round(double v) {
        return Math.round(v * 10000) / 10000.0;
    }
}
