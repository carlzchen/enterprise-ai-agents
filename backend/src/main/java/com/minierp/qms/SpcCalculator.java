package com.minierp.qms;

import java.util.ArrayList;
import java.util.List;

/**
 * SPC 均值-极差控制图与过程能力（纯函数）。
 * 控制限：X̄̄ ± A2·R̄，R 图 D3·R̄ ~ D4·R̄；组内 σ = R̄ / d2；Cp = (USL-LSL)/6σ，Cpk = min(USL-μ, μ-LSL)/3σ；Ppk 用整体标准差。
 * 判异（Western Electric）：1 点超出 3σ 控制限；连续 9 点在中心线同侧；连续 6 点持续上升或下降。
 */
public final class SpcCalculator {
    private SpcCalculator() {
    }

    // 下标 = 子组大小 n（2~10）
    private static final double[] A2 = {0, 0, 1.880, 1.023, 0.729, 0.577, 0.483, 0.419, 0.373, 0.337, 0.308};
    private static final double[] D3 = {0, 0, 0, 0, 0, 0, 0, 0.076, 0.136, 0.184, 0.223};
    private static final double[] D4 = {0, 0, 3.267, 2.574, 2.282, 2.114, 2.004, 1.924, 1.864, 1.816, 1.777};
    private static final double[] D2 = {0, 0, 1.128, 1.693, 2.059, 2.326, 2.534, 2.704, 2.847, 2.970, 3.078};

    public record Violation(int index, String rule) {
    }

    public record Result(List<Double> xbar, List<Double> range, double center, double ucl, double lcl, double rBar, double rUcl, double rLcl,
                         double sigmaWithin, double cp, double cpk, double ppk, List<Violation> violations) {
    }

    public static Result analyze(List<List<Double>> subgroups, double lsl, double usl) {
        List<List<Double>> groups = subgroups.stream().filter(g -> g.size() >= 2).toList();
        if (groups.isEmpty()) {
            throw new IllegalArgumentException("至少需要一个样本量 ≥ 2 的子组");
        }
        int n = Math.min(10, (int) Math.round(groups.stream().mapToInt(List::size).average().orElse(5)));
        List<Double> xbar = new ArrayList<>(), range = new ArrayList<>();
        List<Double> all = new ArrayList<>();
        for (List<Double> g : groups) {
            xbar.add(g.stream().mapToDouble(Double::doubleValue).average().orElseThrow());
            range.add(g.stream().mapToDouble(Double::doubleValue).max().orElseThrow() - g.stream().mapToDouble(Double::doubleValue).min().orElseThrow());
            all.addAll(g);
        }
        double xbb = xbar.stream().mapToDouble(Double::doubleValue).average().orElseThrow();
        double rBar = range.stream().mapToDouble(Double::doubleValue).average().orElseThrow();
        double sigma = rBar / D2[n];
        double mean = all.stream().mapToDouble(Double::doubleValue).average().orElseThrow();
        double sd = Math.sqrt(all.stream().mapToDouble(v -> (v - mean) * (v - mean)).sum() / Math.max(1, all.size() - 1));
        double cp = sigma > 0 ? (usl - lsl) / (6 * sigma) : Double.NaN;
        double cpk = sigma > 0 ? Math.min(usl - xbb, xbb - lsl) / (3 * sigma) : Double.NaN;
        double ppk = sd > 0 ? Math.min(usl - mean, mean - lsl) / (3 * sd) : Double.NaN;
        double ucl = xbb + A2[n] * rBar, lcl = xbb - A2[n] * rBar;
        return new Result(xbar, range, xbb, ucl, lcl, rBar, D4[n] * rBar, D3[n] * rBar, sigma, r3(cp), r3(cpk), r3(ppk),
                rules(xbar, xbb, ucl, lcl));
    }

    static List<Violation> rules(List<Double> x, double center, double ucl, double lcl) {
        List<Violation> v = new ArrayList<>();
        int side = 0, run = 0, trend = 0;
        for (int i = 0; i < x.size(); i++) {
            double p = x.get(i);
            if (p > ucl || p < lcl) {
                v.add(new Violation(i, "超出控制限"));
            }
            int s = Double.compare(p, center);
            run = s != 0 && s == side ? run + 1 : 1;
            side = s;
            if (run == 9) {
                v.add(new Violation(i, "连续 9 点在中心线同侧"));
            }
            if (i > 0) {
                int dir = Double.compare(p, x.get(i - 1));
                trend = dir != 0 && (trend == 0 || Integer.signum(trend) == dir) ? trend + dir : dir;
                if (Math.abs(trend) == 5) { // 6 个点 = 5 次同向变化
                    v.add(new Violation(i, "连续 6 点持续" + (trend > 0 ? "上升" : "下降")));
                }
            }
        }
        return v;
    }

    private static double r3(double v) {
        return Double.isNaN(v) ? v : Math.round(v * 1000) / 1000.0;
    }
}
