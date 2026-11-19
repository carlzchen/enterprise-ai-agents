package com.minierp.common;

import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;

/**
 * SLA 计时（纯函数）：售后工单与 IT 工单共用。
 * 两种时钟：7×24（紧急工单，自然时间）与工作时间（工作日 8:30–17:30，按 WorkCalendar 跳过周末和法定节假日）。
 */
public final class SlaClock {
    private SlaClock() {
    }

    public static final LocalTime DAY_START = LocalTime.of(8, 30), DAY_END = LocalTime.of(17, 30);

    /** 从 start 起计 hours 小时后的到期时间 */
    public static LocalDateTime due(LocalDateTime start, double hours, boolean businessHours) {
        long minutes = Math.round(hours * 60);
        if (!businessHours) {
            return start.plusMinutes(minutes);
        }
        LocalDateTime t = normalize(start);
        while (minutes > 0) {
            long left = Duration.between(t, t.toLocalDate().atTime(DAY_END)).toMinutes();
            if (minutes <= left) {
                return t.plusMinutes(minutes);
            }
            minutes -= left;
            t = nextWorkday(t.toLocalDate().plusDays(1)).atTime(DAY_START);
        }
        return t;
    }

    /** from → to 之间经过的小时数（工作时间时钟只计工作日 8:30–17:30 内的部分） */
    public static double elapsedHours(LocalDateTime from, LocalDateTime to, boolean businessHours) {
        if (!to.isAfter(from)) {
            return 0;
        }
        if (!businessHours) {
            return Duration.between(from, to).toMinutes() / 60.0;
        }
        long minutes = 0;
        for (LocalDate d = from.toLocalDate(); !d.isAfter(to.toLocalDate()); d = d.plusDays(1)) {
            if (!WorkCalendar.isWorkday(d)) {
                continue;
            }
            LocalDateTime s = max(from, d.atTime(DAY_START)), e = min(to, d.atTime(DAY_END));
            if (e.isAfter(s)) {
                minutes += Duration.between(s, e).toMinutes();
            }
        }
        return minutes / 60.0;
    }

    /** 落到工作时间内：非工作日或下班后顺延到下一个工作日 8:30，上班前取当天 8:30 */
    static LocalDateTime normalize(LocalDateTime t) {
        LocalDate d = t.toLocalDate();
        if (!WorkCalendar.isWorkday(d) || !t.toLocalTime().isBefore(DAY_END)) {
            return nextWorkday(WorkCalendar.isWorkday(d) ? d.plusDays(1) : d).atTime(DAY_START);
        }
        return t.toLocalTime().isBefore(DAY_START) ? d.atTime(DAY_START) : t;
    }

    private static LocalDate nextWorkday(LocalDate d) {
        while (!WorkCalendar.isWorkday(d)) {
            d = d.plusDays(1);
        }
        return d;
    }

    private static LocalDateTime max(LocalDateTime a, LocalDateTime b) {
        return a.isAfter(b) ? a : b;
    }

    private static LocalDateTime min(LocalDateTime a, LocalDateTime b) {
        return a.isBefore(b) ? a : b;
    }
}
