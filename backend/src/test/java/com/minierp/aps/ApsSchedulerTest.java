package com.minierp.aps;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ApsSchedulerTest {
    private static final LocalDate MON = LocalDate.of(2026, 9, 28); // 周一

    @Test
    void earliestDueDateFirstAndCapacityIsRespected() {
        var result = ApsScheduler.schedule(Map.of("L5", 100), List.of(
                new ApsScheduler.Order(1, "L5", new BigDecimal("150"), MON.plusDays(10)),
                new ApsScheduler.Order(2, "L5", new BigDecimal("50"), MON.plusDays(1))), MON, Set.of());
        var first = result.slots().get(0);
        assertThat(first.woId()).isEqualTo(2);                       // 交期早的先排
        assertThat(first.start()).isEqualTo(MON);
        var second = result.slots().get(1);
        assertThat(second.start()).isEqualTo(MON);                   // 同一天剩余 50 产能
        assertThat(second.end()).isEqualTo(MON.plusDays(1));         // 跨到第二天
        assertThat(result.load().get("L5").get(MON)).isEqualByComparingTo("100");
        assertThat(result.load().get("L5").values()).allSatisfy(v -> assertThat(v).isLessThanOrEqualTo(new BigDecimal("100")));
    }

    @Test
    void weekendsAndHolidaysAreSkippedAndLatenessComputed() {
        LocalDate fri = MON.plusDays(4);
        var result = ApsScheduler.schedule(Map.of("L1", 10), List.of(
                new ApsScheduler.Order(1, "L1", new BigDecimal("20"), fri)), fri, Set.of(MON.plusDays(7)));
        var slot = result.slots().get(0);
        assertThat(slot.start()).isEqualTo(fri);
        assertThat(slot.end()).isEqualTo(MON.plusDays(8)); // 周末 + 下周一假日跳过，排到周二
        assertThat(slot.lateDays()).isEqualTo(4);
    }

    @Test
    void linesAreScheduledIndependently() {
        var result = ApsScheduler.schedule(Map.of("L1", 10, "L2", 10), List.of(
                new ApsScheduler.Order(1, "L1", new BigDecimal("10"), MON), new ApsScheduler.Order(2, "L2", new BigDecimal("10"), MON)), MON, Set.of());
        assertThat(result.slots()).allSatisfy(s -> assertThat(s.start()).isEqualTo(MON));
    }

    @Test
    void orderWaitingForMaterialYieldsToLaterReadyOrder() {
        var result = ApsScheduler.schedule(Map.of("L3", 100), List.of(
                new ApsScheduler.Order(1, "L3", new BigDecimal("100"), MON.plusDays(1), MON.plusDays(2)),  // 缺料，周三才齐套
                new ApsScheduler.Order(2, "L3", new BigDecimal("100"), MON.plusDays(9))), MON, Set.of());
        var byId = new java.util.HashMap<Long, ApsScheduler.Slot>();
        result.slots().forEach(s -> byId.put(s.woId(), s));
        assertThat(byId.get(2L).start()).isEqualTo(MON);            // 后面的齐套工单先做
        assertThat(byId.get(1L).start()).isEqualTo(MON.plusDays(2)); // 齐套后才开工
        assertThat(byId.get(1L).lateDays()).isEqualTo(1);
    }

    @Test
    void missingCapacityIsAnError() {
        assertThatThrownBy(() -> ApsScheduler.schedule(Map.of(), List.of(new ApsScheduler.Order(1, "L9", BigDecimal.ONE, MON)), MON, Set.of()))
                .hasMessageContaining("L9");
    }
}
