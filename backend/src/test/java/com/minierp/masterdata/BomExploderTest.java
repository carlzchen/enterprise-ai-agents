package com.minierp.masterdata;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BomExploderTest {
    // 成品 P:1 → 定子 M:10 ×1、转子 M:11 ×1；转子 → 铁芯 M:12 ×1、磁钢 M:3 ×8；定子 → 铜线 M:1 ×0.25、硅钢 M:2 ×0.56；铁芯 → 硅钢 M:2 ×0.24
    private static Map<String, Map<Long, BigDecimal>> servo() {
        Map<String, Map<Long, BigDecimal>> s = new HashMap<>();
        s.put("P:1", Map.of(10L, BigDecimal.ONE, 11L, BigDecimal.ONE));
        s.put("M:10", Map.of(1L, new BigDecimal("0.25"), 2L, new BigDecimal("0.56")));
        s.put("M:11", Map.of(12L, BigDecimal.ONE, 3L, new BigDecimal("8")));
        s.put("M:12", Map.of(2L, new BigDecimal("0.24")));
        return s;
    }

    @Test
    void explodesThreeLevelsAndMultipliesQuantities() {
        var nodes = BomExploder.explode("P:1", new BigDecimal("100"), servo());
        assertThat(nodes).hasSize(7);
        assertThat(nodes.stream().mapToInt(BomExploder.Node::level).max().orElse(0)).isEqualTo(3);
        var core = nodes.stream().filter(n -> n.key().equals("M:12")).findFirst().orElseThrow();
        assertThat(core.level()).isEqualTo(2);
        assertThat(core.leaf()).isFalse();
    }

    @Test
    void flattenMergesSameLeafFromDifferentBranches() {
        Map<Long, BigDecimal> flat = BomExploder.flatten("P:1", new BigDecimal("100"), servo());
        // 硅钢：定子 0.56×100 + 铁芯 0.24×100 = 80
        assertThat(flat.get(2L)).isEqualByComparingTo("80");
        assertThat(flat.get(3L)).isEqualByComparingTo("800");
        assertThat(flat).doesNotContainKeys(10L, 11L, 12L);
    }

    @Test
    void lowLevelCodeIsDeepestOccurrence() {
        Map<String, Integer> llc = BomExploder.lowLevelCodes(servo());
        assertThat(llc.get("P:1")).isZero();
        assertThat(llc.get("M:11")).isEqualTo(1);
        assertThat(llc.get("M:12")).isEqualTo(2);
        assertThat(llc.get("M:2")).isEqualTo(3); // 硅钢既在第 2 层（定子下）也在第 3 层（铁芯下），取最深
    }

    @Test
    void detectsCycle() {
        Map<String, Map<Long, BigDecimal>> s = new HashMap<>(servo());
        s.put("M:12", Map.of(11L, BigDecimal.ONE));
        assertThatThrownBy(() -> BomExploder.explode("P:1", BigDecimal.ONE, s)).hasMessageContaining("循环");
    }
}
