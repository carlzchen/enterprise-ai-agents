package com.minierp.srm;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class SupplierScoringTest {
    @Test
    void perfectSupplierGetsGradeA() {
        var s = SupplierScoring.score(1.0, 1.0, 0.98);
        assertThat(s.total()).isEqualByComparingTo("100.00");
        assertThat(s.grade()).isEqualTo("A");
    }

    @Test
    void priceAboveReferenceLosesTwoPointsPerPercent() {
        assertThat(SupplierScoring.score(1, 1, 1.05).price()).isEqualByComparingTo("10.00");
        assertThat(SupplierScoring.score(1, 1, 1.20).price()).isEqualByComparingTo("0.00");
    }

    @Test
    void poorQualityDropsToGradeD() {
        // 判退率 45%：质量 27.5 + 交付 27 + 价格 20 = 74.5 → C；再叠加交付差 → D
        assertThat(SupplierScoring.score(0.55, 0.9, 1.0).grade()).isEqualTo("C");
        assertThat(SupplierScoring.score(0.55, 0.5, 1.0).grade()).isEqualTo("D");
    }

    @Test
    void ratesAreClampedToZeroAndOne() {
        assertThat(SupplierScoring.score(1.5, -1, 1.0).quality()).isEqualByComparingTo("50.00");
        assertThat(SupplierScoring.score(1.5, -1, 1.0).delivery()).isEqualByComparingTo("0.00");
    }
}
