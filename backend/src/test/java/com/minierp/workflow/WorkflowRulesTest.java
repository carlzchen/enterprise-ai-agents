package com.minierp.workflow;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class WorkflowRulesTest {
    private static WorkflowRouter.NodeDef node(String name, String type, String cond) {
        return new WorkflowRouter.NodeDef(name, type, 1, "FIN", "FINANCE", "gm", "OR", cond);
    }

    /** 申请人 10 → 上级 20 → 部门负责人 20（同一人）；财务 30、31；财务负责人 30；总经理 99 */
    private static final WorkflowRouter.ApproverResolver R = new WorkflowRouter.ApproverResolver() {
        public List<Long> manager(long a, int level) { return a == 10 ? List.of(20L) : List.of(99L); }
        public List<Long> deptHead(long a) { return a == 10 ? List.of(20L) : List.of(); }
        public List<Long> headOf(String dept) { return List.of(30L); }
        public List<Long> role(String role) { return List.of(30L, 31L); }
        public List<Long> user(String u) { return List.of(99L); }
    };

    private static final List<WorkflowRouter.NodeDef> EXPENSE = List.of(
            node("直属上级", "MANAGER", null), node("部门负责人", "DEPT_HEAD", "amount >= 5000"),
            node("财务审核", "ROLE", null), node("总经理", "USER", "amount >= 50000"));

    @Test
    void conditionSkipsNodesAndDuplicateApproverAutoPasses() {
        var small = WorkflowRouter.route(EXPENSE, Map.of("amount", 1200), 10, R);
        assertThat(small).extracting(WorkflowRouter.RouteNode::name).containsExactly("直属上级", "财务审核");
        var big = WorkflowRouter.route(EXPENSE, Map.of("amount", 80000), 10, R);
        assertThat(big).extracting(WorkflowRouter.RouteNode::name).containsExactly("直属上级", "部门负责人", "财务审核", "总经理");
        assertThat(big.get(1).autoPass()).isTrue(); // 部门负责人 = 直属上级，去重
        assertThat(big.get(1).note()).contains("前序");
    }

    @Test
    void applicantNeverApprovesOwnRequest() {
        // 财务人员 30 自己报销：财务审核节点只剩 31
        var r = WorkflowRouter.route(EXPENSE, Map.of("amount", 100), 30, R);
        assertThat(r.get(1).approvers()).containsExactly(31L);
        // 总经理本人报销大额：总经理节点自动跳过
        var gm = WorkflowRouter.route(EXPENSE, Map.of("amount", 60000), 99, R);
        assertThat(gm.get(gm.size() - 1).note()).contains("申请人本人");
    }

    @Test
    void orSignNeedsOneAndSignNeedsAllAddedSignMustApprove() {
        var P = "PENDING";
        var A = "APPROVED";
        assertThat(NodeDecision.evaluate("OR", List.of(new NodeDecision.TaskState(A, false), new NodeDecision.TaskState(P, false))))
                .isEqualTo(NodeDecision.Outcome.APPROVED);
        assertThat(NodeDecision.evaluate("AND", List.of(new NodeDecision.TaskState(A, false), new NodeDecision.TaskState(P, false))))
                .isEqualTo(NodeDecision.Outcome.PENDING);
        assertThat(NodeDecision.evaluate("OR", List.of(new NodeDecision.TaskState(A, false), new NodeDecision.TaskState(P, true))))
                .isEqualTo(NodeDecision.Outcome.PENDING); // 加签人未同意
        assertThat(NodeDecision.evaluate("AND", List.of(new NodeDecision.TaskState("TRANSFERRED", false), new NodeDecision.TaskState(A, false))))
                .isEqualTo(NodeDecision.Outcome.APPROVED); // 转交后由接收人决定
        assertThat(NodeDecision.evaluate("AND", List.of(new NodeDecision.TaskState(A, false), new NodeDecision.TaskState("REJECTED", false))))
                .isEqualTo(NodeDecision.Outcome.REJECTED);
    }

    @Test
    void conditionExpressions() {
        Map<String, Object> v = Map.of("amount", "12000.50", "days", 3, "leaveType", "SICK");
        assertThat(ConditionEvaluator.eval("amount >= 5000 && days > 2", v)).isTrue();
        assertThat(ConditionEvaluator.eval("amount >= 50000 || leaveType == 'SICK'", v)).isTrue();
        assertThat(ConditionEvaluator.eval("leaveType != 'SICK'", v)).isFalse();
        assertThat(ConditionEvaluator.eval("missing > 1", v)).isFalse();
        assertThat(ConditionEvaluator.eval("", v)).isTrue();
        assertThatThrownBy(() -> ConditionEvaluator.validate("amount >= 1; DROP TABLE x")).isInstanceOf(IllegalArgumentException.class);
    }
}
