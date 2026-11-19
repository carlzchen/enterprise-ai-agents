package com.minierp.pm;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PmRulesTest {
    @Test
    void workflowTransitions() {
        assertThatCode(() -> IssueWorkflow.validate("STORY", "IN_REVIEW", "DONE", "FIXED")).doesNotThrowAnyException();
        assertThatCode(() -> IssueWorkflow.validate("TASK", "IN_PROGRESS", "DONE", "FIXED")).doesNotThrowAnyException();
        assertThatThrownBy(() -> IssueWorkflow.validate("BUG", "IN_PROGRESS", "DONE", "FIXED")).hasMessageContaining("评审");
        assertThatThrownBy(() -> IssueWorkflow.validate("STORY", "TODO", "IN_REVIEW", null)).hasMessageContaining("不允许");
        assertThatThrownBy(() -> IssueWorkflow.validate("STORY", "IN_REVIEW", "DONE", null)).hasMessageContaining("解决结果");
        assertThatThrownBy(() -> IssueWorkflow.validate("BUG", "TODO", "DONE", "FIXED")).hasMessageContaining("不修复");
        assertThatCode(() -> IssueWorkflow.validate("BUG", "TODO", "DONE", "DUPLICATE")).doesNotThrowAnyException();
        assertThatCode(() -> IssueWorkflow.validate("BUG", "DONE", "TODO", null)).doesNotThrowAnyException();
        assertThatThrownBy(() -> IssueWorkflow.validate("TASK", "DONE", "TODO", null)).hasMessageContaining("新建任务");
    }

    @Test
    void burndownWithScopeChange() {
        LocalDate d1 = LocalDate.of(2026, 3, 2);
        var days = List.of(d1, d1.plusDays(1), d1.plusDays(2));
        var b = AgileMetrics.burndown(days, 20, List.of(new AgileMetrics.Completion(d1.plusDays(1), 8)),
                List.of(new AgileMetrics.Completion(d1.plusDays(2), 3)));
        assertThat(b).extracting(AgileMetrics.BurnPoint::remaining).containsExactly(20, 12, 15);
        assertThat(b).extracting(AgileMetrics.BurnPoint::ideal).containsExactly(20.0, 10.0, 0.0);
    }

    @Test
    void velocityAndPercentiles() {
        var v = AgileMetrics.velocity(List.of(5, 20, 22, 18, 20), 3);
        assertThat(v.average()).isEqualTo(20.0);
        assertThat(v.stdDev()).isEqualTo(2.0);
        var p = AgileMetrics.percentiles(List.of(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0));
        assertThat(p).containsEntry("p50", 5.0).containsEntry("p85", 9.0).containsEntry("p95", 10.0);
    }

    @Test
    void cumulativeFlowCountsStatusAtEndOfDay() {
        LocalDateTime t = LocalDate.of(2026, 3, 2).atTime(9, 0);
        var cfd = AgileMetrics.cumulativeFlow(List.of(
                new AgileMetrics.Transition(1, t, "TODO"), new AgileMetrics.Transition(2, t, "TODO"),
                new AgileMetrics.Transition(1, t.plusDays(1), "IN_PROGRESS"), new AgileMetrics.Transition(1, t.plusDays(2), "DONE")),
                t.toLocalDate(), t.toLocalDate().plusDays(2));
        assertThat(cfd.get(t.toLocalDate())).containsEntry("TODO", 2);
        assertThat(cfd.get(t.toLocalDate().plusDays(1))).containsEntry("IN_PROGRESS", 1).containsEntry("TODO", 1);
        assertThat(cfd.get(t.toLocalDate().plusDays(2))).containsEntry("DONE", 1);
    }

    @Test
    void rndCostSplitsByHoursAndKeepsTotal() {
        var alloc = RndCostAllocator.allocate(List.of(
                new RndCostAllocator.EmpMonth(1, new BigDecimal("20000"), new BigDecimal("176"), Map.of("JM", new BigDecimal("132"))),
                new RndCostAllocator.EmpMonth(2, new BigDecimal("15000"), new BigDecimal("150"),
                        Map.of("JM", new BigDecimal("100"), "DH", new BigDecimal("80")))));
        assertThat(alloc.get("JM")).isEqualByComparingTo("23333.33"); // 15000 + 15000×100/180
        assertThat(alloc.get("DH")).isEqualByComparingTo("6666.67");
        assertThat(alloc.get(RndCostAllocator.NON_PROJECT)).isEqualByComparingTo("5000");    // 20000 × 44/176
        assertThat(alloc.values().stream().reduce(BigDecimal.ZERO, BigDecimal::add)).isEqualByComparingTo("35000");
    }
}
