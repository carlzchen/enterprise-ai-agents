package com.minierp.plm;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BomChangeCalculatorTest {
    private final Map<Long, BigDecimal> bom = new LinkedHashMap<>(Map.of(1L, new BigDecimal("0.10"), 2L, new BigDecimal("4")));

    @Test
    void appliesAddRemoveAndChangeTogether() {
        Map<Long, BigDecimal> out = BomChangeCalculator.apply(bom, List.of(
                new BomChangeCalculator.Change("CHANGE", 1L, new BigDecimal("0.092")),
                new BomChangeCalculator.Change("REMOVE", 2L, null),
                new BomChangeCalculator.Change("ADD", 3L, new BigDecimal("4"))));
        assertThat(out).containsOnlyKeys(1L, 3L);
        assertThat(out.get(1L)).isEqualByComparingTo("0.092");
        assertThat(bom).containsKey(2L); // 原 BOM 不被修改
    }

    @Test
    void rejectsAddingExistingMaterial() {
        assertThatThrownBy(() -> BomChangeCalculator.apply(bom, List.of(new BomChangeCalculator.Change("ADD", 1L, BigDecimal.ONE))))
                .hasMessageContaining("CHANGE");
    }

    @Test
    void rejectsRemovingOrChangingMissingMaterial() {
        assertThatThrownBy(() -> BomChangeCalculator.apply(bom, List.of(new BomChangeCalculator.Change("REMOVE", 9L, null))))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> BomChangeCalculator.apply(bom, List.of(new BomChangeCalculator.Change("CHANGE", 9L, BigDecimal.ONE))))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void rejectsNonPositiveQuantityAndEmptyResult() {
        assertThatThrownBy(() -> BomChangeCalculator.apply(bom, List.of(new BomChangeCalculator.Change("CHANGE", 1L, BigDecimal.ZERO))))
                .hasMessageContaining("大于 0");
        assertThatThrownBy(() -> BomChangeCalculator.apply(bom, List.of(
                new BomChangeCalculator.Change("REMOVE", 1L, null), new BomChangeCalculator.Change("REMOVE", 2L, null))))
                .hasMessageContaining("不能为空");
    }

    @Test
    void revisionAdvancesAlphabeticallyAndStopsAtZ() {
        assertThat(BomChangeCalculator.nextRevision("A")).isEqualTo("B");
        assertThatThrownBy(() -> BomChangeCalculator.nextRevision("Z")).isInstanceOf(IllegalArgumentException.class);
    }
}
