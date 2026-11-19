package com.minierp.eam;

/**
 * 设备可靠性（纯函数）：MTBF = 运行时间 / 故障次数，MTTR = 维修总时间 / 故障次数，
 * 固有可用度 = MTBF / (MTBF + MTTR)。无故障时 MTBF 记为运行时间（下限估计），MTTR 为 0。
 */
public final class ReliabilityCalculator {
    private ReliabilityCalculator() {
    }

    public record Reliability(double mtbfHours, double mttrHours, double availability, int failures) {
    }

    public static Reliability compute(double operatingHours, int failures, double repairMinutes) {
        double mtbf = failures == 0 ? operatingHours : operatingHours / failures;
        double mttr = failures == 0 ? 0 : repairMinutes / 60.0 / failures;
        double a = mtbf + mttr <= 0 ? 0 : mtbf / (mtbf + mttr);
        return new Reliability(Math.round(mtbf * 10) / 10.0, Math.round(mttr * 100) / 100.0, Math.round(a * 10000) / 10000.0, failures);
    }
}
