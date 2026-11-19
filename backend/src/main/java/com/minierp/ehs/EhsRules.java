package com.minierp.ehs;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.List;

/**
 * 安环规则（纯函数）。
 * 安全绩效：
 *   - LTIFR 百万工时损工事故率 = 损工事故数 × 1,000,000 ÷ 总工时；
 *   - TRIR 可记录事故率 = 可记录事故数（含损工）× 200,000 ÷ 总工时（OSHA 口径）；
 *   - 严重率 = 损失工作日 × 1,000,000 ÷ 总工时。
 * 隐患整改期限：一般隐患 7 天，重大隐患 30 天（需制定治理方案）。
 * 危险废物贮存：按先进先出计算最早一批的贮存天数，超过 270 天预警，超过 365 天违规（固废法：贮存不得超过一年）。
 */
public final class EhsRules {
    private EhsRules() {
    }

    public static final int WASTE_WARN_DAYS = 270, WASTE_LIMIT_DAYS = 365;

    public record SafetyKpi(double hours, int lostTime, int recordable, int lostDays, BigDecimal ltifr, BigDecimal trir, BigDecimal severity) {
    }

    public static SafetyKpi safety(double hours, int lostTime, int recordable, int lostDays) {
        if (hours <= 0) {
            return new SafetyKpi(hours, lostTime, recordable, lostDays, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO);
        }
        return new SafetyKpi(hours, lostTime, recordable, lostDays, rate(lostTime, 1_000_000, hours),
                rate(recordable + lostTime, 200_000, hours), rate(lostDays, 1_000_000, hours));
    }

    private static BigDecimal rate(int count, double base, double hours) {
        return BigDecimal.valueOf(count * base / hours).setScale(2, RoundingMode.HALF_UP);
    }

    public static LocalDate hazardDeadline(String level, LocalDate found) {
        return found.plusDays("MAJOR".equals(level) ? 30 : 7);
    }

    public static boolean overdue(LocalDate deadline, LocalDate rectified, LocalDate asOf) {
        return rectified == null ? asOf.isAfter(deadline) : rectified.isAfter(deadline);
    }

    /** 危废台账流水：GENERATE 为正，TRANSFER 为负（按日期排序） */
    public record WasteMove(LocalDate date, BigDecimal qty) {
    }

    public record WasteStock(BigDecimal qty, LocalDate oldest, long days, String status) {
    }

    /** 先进先出：转移优先消耗最早产生的批次，得出在库量和最早一批的贮存天数 */
    public static WasteStock wasteStock(List<WasteMove> moves, LocalDate asOf) {
        Deque<Object[]> lots = new ArrayDeque<>(); // {date, qty}
        for (WasteMove m : moves) {
            if (m.qty().signum() > 0) {
                lots.addLast(new Object[]{m.date(), m.qty()});
                continue;
            }
            BigDecimal out = m.qty().negate();
            while (out.signum() > 0 && !lots.isEmpty()) {
                Object[] lot = lots.peekFirst();
                BigDecimal q = (BigDecimal) lot[1];
                if (q.compareTo(out) <= 0) {
                    out = out.subtract(q);
                    lots.pollFirst();
                } else {
                    lot[1] = q.subtract(out);
                    out = BigDecimal.ZERO;
                }
            }
        }
        BigDecimal qty = lots.stream().map(l -> (BigDecimal) l[1]).reduce(BigDecimal.ZERO, BigDecimal::add);
        if (lots.isEmpty()) {
            return new WasteStock(BigDecimal.ZERO, null, 0, "OK");
        }
        LocalDate oldest = (LocalDate) lots.peekFirst()[0];
        long days = ChronoUnit.DAYS.between(oldest, asOf);
        return new WasteStock(qty, oldest, days, days > WASTE_LIMIT_DAYS ? "VIOLATION" : days > WASTE_WARN_DAYS ? "WARNING" : "OK");
    }
}
