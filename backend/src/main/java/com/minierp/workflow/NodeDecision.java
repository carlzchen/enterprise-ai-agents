package com.minierp.workflow;

import java.util.List;

/**
 * 节点结论（纯函数）。
 * - 任一任务驳回 → 驳回；
 * - 原始审批人：或签至少一人同意、会签全部同意（被转交的任务由接收人的新任务替代）；
 * - 加签产生的任务必须全部同意（加签人把关，不受或签影响）。
 */
public final class NodeDecision {
    private NodeDecision() {
    }

    public enum Outcome { PENDING, APPROVED, REJECTED }

    public record TaskState(String status, boolean added) {
    }

    public static Outcome evaluate(String mode, List<TaskState> tasks) {
        if (tasks.stream().anyMatch(t -> "REJECTED".equals(t.status()))) {
            return Outcome.REJECTED;
        }
        List<TaskState> base = tasks.stream().filter(t -> !t.added() && live(t.status())).toList();
        List<TaskState> added = tasks.stream().filter(t -> t.added() && live(t.status())).toList();
        boolean baseOk = "AND".equals(mode)
                ? !base.isEmpty() && base.stream().allMatch(t -> "APPROVED".equals(t.status()))
                : base.stream().anyMatch(t -> "APPROVED".equals(t.status()));
        boolean addedOk = added.stream().allMatch(t -> "APPROVED".equals(t.status()));
        return baseOk && addedOk ? Outcome.APPROVED : Outcome.PENDING;
    }

    /** 转交、取消、跳过的任务不参与判定 */
    private static boolean live(String status) {
        return "PENDING".equals(status) || "APPROVED".equals(status);
    }
}
