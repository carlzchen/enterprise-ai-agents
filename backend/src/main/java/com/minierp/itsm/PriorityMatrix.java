package com.minierp.itsm;

import java.util.List;

/**
 * ITIL 优先级矩阵（纯函数）：影响范围 × 紧急程度 → P1~P4，并给出响应 / 解决 SLA。
 * P1 按 7×24 计时，其余按工作时间计时（SlaClock）。
 */
public final class PriorityMatrix {
    private PriorityMatrix() {
    }

    public record Sla(String priority, double responseHours, double resolveHours, boolean businessHours) {
    }

    private static final List<String> LEVELS = List.of("HIGH", "MEDIUM", "LOW");
    private static final String[][] MATRIX = {
            // urgency:  HIGH   MEDIUM  LOW
            {"P1", "P2", "P3"},   // impact HIGH（全厂 / 产线停摆）
            {"P2", "P3", "P4"},   // impact MEDIUM（一个部门）
            {"P3", "P4", "P4"}};  // impact LOW（个人）

    public static String priority(String impact, String urgency) {
        int i = LEVELS.indexOf(impact), u = LEVELS.indexOf(urgency);
        if (i < 0 || u < 0) {
            throw new IllegalArgumentException("impact / urgency 取值为 HIGH / MEDIUM / LOW");
        }
        return MATRIX[i][u];
    }

    public static Sla sla(String priority) {
        return switch (priority) {
            case "P1" -> new Sla("P1", 0.5, 4, false);
            case "P2" -> new Sla("P2", 1, 8, true);
            case "P3" -> new Sla("P3", 4, 24, true);
            case "P4" -> new Sla("P4", 8, 40, true);
            default -> throw new IllegalArgumentException("未知优先级 " + priority);
        };
    }
}
