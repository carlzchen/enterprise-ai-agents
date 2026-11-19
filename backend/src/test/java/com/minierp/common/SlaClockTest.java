package com.minierp.common;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class SlaClockTest {
    @Test
    void businessHoursSkipHolidayAndWeekend() {
        // 2026-09-24（周四）16:30 起 4 个工作小时：当天 1 小时，9/25 节假日 + 周末跳过，9/28（周一）8:30 再计 3 小时
        LocalDateTime start = LocalDateTime.of(2026, 9, 24, 16, 30);
        assertThat(SlaClock.due(start, 4, true)).isEqualTo(LocalDateTime.of(2026, 9, 28, 11, 30));
        assertThat(SlaClock.elapsedHours(start, LocalDateTime.of(2026, 9, 28, 11, 30), true)).isEqualTo(4.0);
        assertThat(SlaClock.due(start, 2, false)).isEqualTo(LocalDateTime.of(2026, 9, 24, 18, 30));
    }

    @Test
    void startOutsideWorkingHoursIsNormalized() {
        assertThat(SlaClock.due(LocalDateTime.of(2026, 9, 28, 7, 0), 1, true)).isEqualTo(LocalDateTime.of(2026, 9, 28, 9, 30));
        assertThat(SlaClock.due(LocalDateTime.of(2026, 9, 26, 10, 0), 1, true)).isEqualTo(LocalDateTime.of(2026, 9, 28, 9, 30));
        assertThat(SlaClock.elapsedHours(LocalDateTime.of(2026, 9, 26, 10, 0), LocalDateTime.of(2026, 9, 27, 18, 0), true)).isZero();
    }
}
