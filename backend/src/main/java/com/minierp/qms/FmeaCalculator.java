package com.minierp.qms;

/**
 * FMEA 风险评估（纯函数）：RPN = 严重度 × 频度 × 探测度（各 1~10）。
 * 措施优先级（参考 AIAG-VDA 思路的简化版）：严重度 9~10 且频度 ≥ 4，或 RPN ≥ 200 → 高；RPN ≥ 100 或严重度 ≥ 9 → 中；其余 → 低。
 */
public final class FmeaCalculator {
    private FmeaCalculator() {
    }

    public static int rpn(int s, int o, int d) {
        for (int v : new int[]{s, o, d}) {
            if (v < 1 || v > 10) {
                throw new IllegalArgumentException("S/O/D 取值必须在 1~10");
            }
        }
        return s * o * d;
    }

    public static String priority(int s, int o, int d) {
        int rpn = rpn(s, o, d);
        if (s >= 9 && o >= 4 || rpn >= 200) {
            return "HIGH";
        }
        if (rpn >= 100 || s >= 9) {
            return "MEDIUM";
        }
        return "LOW";
    }
}
