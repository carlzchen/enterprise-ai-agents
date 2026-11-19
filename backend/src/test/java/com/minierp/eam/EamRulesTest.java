package com.minierp.eam;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class EamRulesTest {
    @Test
    void oeeMultipliesThreeFactors() {
        // 计划 480 分钟，停机损失 48 → A=0.9；产能 1000/天，实际 810 → P = 810/900 = 0.9；良品 792 → Q ≈ 0.9778
        var o = OeeCalculator.compute(480, 48, 810, 792, 1000);
        assertThat(o.availability()).isEqualTo(0.9);
        assertThat(o.performance()).isEqualTo(0.9);
        assertThat(o.oee()).isEqualTo(0.792);
    }

    @Test
    void oeePerformanceCappedAndZeroSafe() {
        assertThat(OeeCalculator.compute(480, 0, 1200, 1200, 1000).performance()).isEqualTo(1.0);
        assertThat(OeeCalculator.compute(480, 480, 0, 0, 1000).oee()).isZero();
    }

    @Test
    void mtbfMttrAndInherentAvailability() {
        var r = ReliabilityCalculator.compute(2000, 4, 480);
        assertThat(r.mtbfHours()).isEqualTo(500);
        assertThat(r.mttrHours()).isEqualTo(2);
        assertThat(r.availability()).isEqualTo(0.996);
        assertThat(ReliabilityCalculator.compute(800, 0, 0).mtbfHours()).isEqualTo(800);
    }
}
