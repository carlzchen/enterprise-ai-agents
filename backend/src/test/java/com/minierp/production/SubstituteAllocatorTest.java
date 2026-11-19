package com.minierp.production;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SubstituteAllocatorTest {
    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }

    @Test
    void primaryEnoughUsesNoSubstitute() {
        var takes = SubstituteAllocator.allocate(1, bd("100"), bd("150"), List.of(new SubstituteAllocator.Option(2, BigDecimal.ONE, bd("500"))));
        assertThat(takes).containsExactly(new SubstituteAllocator.Take(1, bd("100"), bd("100"), false));
    }

    @Test
    void shortfallCoveredByPriorityAndRatio() {
        // 主料只有 60，缺 40；第一替代料按 0.42 折算只够覆盖 20，剩下 20 用第二替代料
        var takes = SubstituteAllocator.allocate(1, bd("100"), bd("60"), List.of(
                new SubstituteAllocator.Option(2, bd("0.42"), bd("8.4")),
                new SubstituteAllocator.Option(3, BigDecimal.ONE, bd("100"))));
        assertThat(takes).hasSize(3);
        assertThat(takes.get(1).materialId()).isEqualTo(2);
        assertThat(takes.get(1).qty()).isEqualByComparingTo("8.4");
        assertThat(takes.get(1).coversPrimary()).isEqualByComparingTo("20");
        assertThat(takes.get(2).qty()).isEqualByComparingTo("20");
        assertThat(takes.stream().filter(SubstituteAllocator.Take::substitute).count()).isEqualTo(2);
    }

    @Test
    void throwsWhenPrimaryAndSubstitutesInsufficient() {
        assertThatThrownBy(() -> SubstituteAllocator.allocate(1, bd("100"), bd("30"),
                List.of(new SubstituteAllocator.Option(2, BigDecimal.ONE, bd("50")))))
                .isInstanceOf(IllegalStateException.class).hasMessageContaining("20");
    }
}
