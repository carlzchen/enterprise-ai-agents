package com.minierp.qms;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class QmsRulesTest {
    @Test
    void aqlPlansMatchStandardTable() {
        assertThat(AqlSampling.plan(1000, 1.0)).isEqualTo(new AqlSampling.Plan('J', 80, 2, 3));
        assertThat(AqlSampling.plan(3000, 0.65)).isEqualTo(new AqlSampling.Plan('K', 125, 2, 3));
        assertThat(AqlSampling.plan(40, 1.0)).isEqualTo(new AqlSampling.Plan('D', 13, 0, 1)); // ↓ 箭头：改用 n=13
        assertThat(AqlSampling.plan(100, 1.0)).isEqualTo(new AqlSampling.Plan('F', 13, 0, 1)); // ↑ 箭头：用上一方案
        assertThat(AqlSampling.plan(200, 1.0)).isEqualTo(new AqlSampling.Plan('G', 50, 1, 2)); // ↓ 箭头：改用 n=50
        assertThat(AqlSampling.plan(6, 0.65).sampleSize()).isEqualTo(6); // 样本量 ≥ 批量 → 全检
    }

    @Test
    void spcLimitsAndCapability() {
        List<List<Double>> groups = new ArrayList<>();
        double[][] data = {{10.1, 9.9, 10.0, 10.2, 9.8}, {10.0, 10.1, 9.9, 10.0, 10.0}, {9.9, 10.0, 10.1, 9.8, 10.2}};
        for (double[] g : data) {
            groups.add(java.util.Arrays.stream(g).boxed().toList());
        }
        var r = SpcCalculator.analyze(groups, 9.0, 11.0);
        assertThat(r.center()).isCloseTo(10.0, org.assertj.core.data.Offset.offset(1e-9));
        assertThat(r.rBar()).isCloseTo(0.3333, org.assertj.core.data.Offset.offset(1e-3));
        assertThat(r.ucl()).isCloseTo(10.0 + 0.577 * r.rBar(), org.assertj.core.data.Offset.offset(1e-9));
        assertThat(r.cpk()).isGreaterThan(2.0); // σ ≈ 0.143，规格 ±1
    }

    @Test
    void westernElectricRules() {
        List<Double> x = new ArrayList<>(List.of(10.0, 10.1, 9.9, 13.5));      // 第 4 点超上限
        for (int i = 0; i < 9; i++) {
            x.add(10.2);                                                     // 连续 9 点在中心线上方
        }
        var v = SpcCalculator.rules(x, 10.0, 11.0, 9.0);
        assertThat(v).extracting(SpcCalculator.Violation::rule).contains("超出控制限", "连续 9 点在中心线同侧");
        var trend = SpcCalculator.rules(List.of(1.0, 2.0, 3.0, 4.0, 5.0, 6.0), 3.5, 10, -10);
        assertThat(trend).extracting(SpcCalculator.Violation::rule).containsExactly("连续 6 点持续上升");
    }

    @Test
    void fmeaPriority() {
        assertThat(FmeaCalculator.rpn(8, 5, 6)).isEqualTo(240);
        assertThat(FmeaCalculator.priority(8, 5, 6)).isEqualTo("HIGH");
        assertThat(FmeaCalculator.priority(9, 2, 3)).isEqualTo("MEDIUM");
        assertThat(FmeaCalculator.priority(5, 3, 4)).isEqualTo("LOW");
        assertThatThrownBy(() -> FmeaCalculator.rpn(11, 1, 1)).isInstanceOf(IllegalArgumentException.class);
    }
}
