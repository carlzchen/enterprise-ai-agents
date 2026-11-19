package com.minierp.pm;

import java.util.Map;
import java.util.Set;

/**
 * 问题状态流转规则（纯函数）：
 * 待办 → 进行中 → 评审中 → 完成；评审不通过退回进行中；进行中可退回待办；
 * 任务（TASK）可跳过评审直接完成；待办可直接以"不修复 / 重复"关闭；已完成的缺陷和需求可重新打开；史诗自由流转。
 * 进入完成必须给出解决结果；从评审或进行中完成的解决结果只能是 FIXED。
 */
public final class IssueWorkflow {
    private IssueWorkflow() {
    }

    private static final Map<String, Set<String>> NEXT = Map.of(
            "TODO", Set.of("IN_PROGRESS", "DONE"),
            "IN_PROGRESS", Set.of("IN_REVIEW", "TODO", "DONE"),
            "IN_REVIEW", Set.of("IN_PROGRESS", "DONE"),
            "DONE", Set.of("TODO"));

    public static void validate(String type, String from, String to, String resolution) {
        if (from.equals(to)) {
            throw new IllegalArgumentException("状态未变化");
        }
        if ("EPIC".equals(type)) {
            if (!Set.of("TODO", "IN_PROGRESS", "DONE").contains(to)) {
                throw new IllegalArgumentException("史诗只有 待办 / 进行中 / 完成 三种状态");
            }
        } else {
            if (!NEXT.getOrDefault(from, Set.of()).contains(to)) {
                throw new IllegalArgumentException("不允许从 " + from + " 变更为 " + to);
            }
            if ("IN_PROGRESS".equals(from) && "DONE".equals(to) && !"TASK".equals(type)) {
                throw new IllegalArgumentException("需求和缺陷必须经过评审才能完成");
            }
            if ("DONE".equals(from) && "TODO".equals(to) && "TASK".equals(type)) {
                throw new IllegalArgumentException("任务完成后不能重新打开，请新建任务");
            }
        }
        if ("DONE".equals(to)) {
            if (resolution == null || resolution.isBlank()) {
                throw new IllegalArgumentException("完成时必须选择解决结果");
            }
            if ("TODO".equals(from) && "FIXED".equals(resolution)) {
                throw new IllegalArgumentException("未开始的问题只能以 不修复 / 重复 关闭");
            }
            if (!"TODO".equals(from) && !"FIXED".equals(resolution) && !"EPIC".equals(type)) {
                throw new IllegalArgumentException("已开始处理的问题完成时解决结果应为 FIXED");
            }
        }
    }
}
