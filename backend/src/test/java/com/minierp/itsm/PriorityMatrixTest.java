package com.minierp.itsm;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class PriorityMatrixTest {
    @Test
    void impactTimesUrgency() {
        assertThat(PriorityMatrix.priority("HIGH", "HIGH")).isEqualTo("P1");
        assertThat(PriorityMatrix.priority("MEDIUM", "HIGH")).isEqualTo("P2");
        assertThat(PriorityMatrix.priority("LOW", "HIGH")).isEqualTo("P3");
        assertThat(PriorityMatrix.priority("LOW", "LOW")).isEqualTo("P4");
        assertThat(PriorityMatrix.sla("P1").businessHours()).isFalse();
        assertThat(PriorityMatrix.sla("P3").resolveHours()).isEqualTo(24);
        assertThatThrownBy(() -> PriorityMatrix.priority("CRITICAL", "HIGH")).isInstanceOf(IllegalArgumentException.class);
    }
}
