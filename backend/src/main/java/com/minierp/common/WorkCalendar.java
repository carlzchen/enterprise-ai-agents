package com.minierp.common;

import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.HashSet;
import java.util.Set;

/** 工作日历：周末 + 法定节假日（请假天数、考勤、工资应出勤天数、模拟器共用） */
public final class WorkCalendar {
    private WorkCalendar() {
    }

    private static final Set<LocalDate> HOLIDAYS = new HashSet<>();

    static {
        range("2024-10-01", "2024-10-07");
        range("2025-01-01", "2025-01-01");
        range("2025-01-28", "2025-02-04");
        range("2025-04-04", "2025-04-04");
        range("2025-05-01", "2025-05-05");
        range("2025-05-31", "2025-06-02");
        range("2025-10-01", "2025-10-08");
        range("2026-01-01", "2026-01-01");
        range("2026-02-15", "2026-02-23");
        range("2026-04-05", "2026-04-06");
        range("2026-05-01", "2026-05-05");
        range("2026-06-19", "2026-06-19");
        range("2026-09-25", "2026-09-25");
        range("2026-10-01", "2026-10-08");
    }

    public static boolean isWorkday(LocalDate d) {
        return d.getDayOfWeek() != DayOfWeek.SATURDAY && d.getDayOfWeek() != DayOfWeek.SUNDAY && !HOLIDAYS.contains(d);
    }

    public static boolean isHoliday(LocalDate d) {
        return HOLIDAYS.contains(d);
    }

    /** [from, to] 闭区间内的工作日数 */
    public static int workdays(LocalDate from, LocalDate to) {
        int n = 0;
        for (LocalDate d = from; !d.isAfter(to); d = d.plusDays(1)) {
            if (isWorkday(d)) {
                n++;
            }
        }
        return n;
    }

    public static int workdays(YearMonth ym) {
        return workdays(ym.atDay(1), ym.atEndOfMonth());
    }

    public static LocalDate lastWorkday(YearMonth ym) {
        LocalDate d = ym.atEndOfMonth();
        while (!isWorkday(d)) {
            d = d.minusDays(1);
        }
        return d;
    }

    private static void range(String from, String to) {
        for (LocalDate d = LocalDate.parse(from); !d.isAfter(LocalDate.parse(to)); d = d.plusDays(1)) {
            HOLIDAYS.add(d);
        }
    }
}
