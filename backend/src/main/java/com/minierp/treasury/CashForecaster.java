package com.minierp.treasury;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.*;

/** 资金滚动预测（纯函数）：按周汇总预计收支，给出每周期末余额、最低余额周与安全余额预警 */
public final class CashForecaster {
    private CashForecaster() {
    }

    public record Flow(LocalDate date, BigDecimal amount, String category) {
    }

    public record Week(int index, LocalDate start, BigDecimal inflow, BigDecimal outflow, BigDecimal ending, Map<String, BigDecimal> byCategory) {
    }

    public record Forecast(List<Week> weeks, BigDecimal minBalance, LocalDate minWeek, LocalDate firstBelowSafety) {
    }

    /** 早于起始日的流量（已逾期未收 / 未付）计入第一周 */
    public static Forecast weekly(LocalDate start, int weeks, BigDecimal opening, List<Flow> flows, BigDecimal safetyBalance) {
        List<Week> out = new ArrayList<>();
        BigDecimal bal = opening, min = opening;
        LocalDate minWeek = start, below = null;
        for (int w = 0; w < weeks; w++) {
            LocalDate ws = start.plusWeeks(w), we = ws.plusDays(6);
            BigDecimal in = BigDecimal.ZERO, outAmt = BigDecimal.ZERO;
            Map<String, BigDecimal> byCat = new TreeMap<>();
            for (Flow f : flows) {
                boolean inWeek = w == 0 ? !f.date().isAfter(we) : !f.date().isBefore(ws) && !f.date().isAfter(we);
                if (!inWeek) {
                    continue;
                }
                if (f.amount().signum() >= 0) {
                    in = in.add(f.amount());
                } else {
                    outAmt = outAmt.add(f.amount().negate());
                }
                byCat.merge(f.category(), f.amount(), BigDecimal::add);
            }
            bal = bal.add(in).subtract(outAmt);
            if (bal.compareTo(min) < 0) {
                min = bal;
                minWeek = ws;
            }
            if (below == null && bal.compareTo(safetyBalance) < 0) {
                below = ws;
            }
            out.add(new Week(w + 1, ws, in, outAmt, bal, byCat));
        }
        return new Forecast(out, min, minWeek, below);
    }

    /** 预计回款日 = 到期日 + 该客户历史平均逾期天数（不早于今天） */
    public static LocalDate expectedDate(LocalDate due, double avgDelayDays, LocalDate today) {
        LocalDate d = due.plusDays(Math.round(avgDelayDays));
        return d.isBefore(today) ? today : d;
    }

    static long days(LocalDate a, LocalDate b) {
        return ChronoUnit.DAYS.between(a, b);
    }
}
