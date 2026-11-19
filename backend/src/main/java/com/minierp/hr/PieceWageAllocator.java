package com.minierp.hr;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.*;

/**
 * 班组计件分摊（纯函数）：班组当月计件总额按组员出勤工时（含加班）占比分摊，
 * 保留两位小数，尾差按最大余数法分配，保证分摊合计 = 班组总额。
 */
public final class PieceWageAllocator {
    private PieceWageAllocator() {
    }

    public static Map<Long, BigDecimal> allocate(BigDecimal teamTotal, Map<Long, BigDecimal> hours) {
        Map<Long, BigDecimal> out = new LinkedHashMap<>();
        BigDecimal totalHours = hours.values().stream().reduce(BigDecimal.ZERO, BigDecimal::add);
        if (totalHours.signum() <= 0 || teamTotal.signum() == 0) {
            hours.keySet().forEach(k -> out.put(k, BigDecimal.ZERO));
            return out;
        }
        long cents = teamTotal.movePointRight(2).setScale(0, RoundingMode.HALF_UP).longValueExact();
        record Part(long id, long floor, BigDecimal rem) {
        }
        List<Part> parts = new ArrayList<>();
        long assigned = 0;
        for (var e : new TreeMap<>(hours).entrySet()) {
            BigDecimal exact = BigDecimal.valueOf(cents).multiply(e.getValue()).divide(totalHours, 10, RoundingMode.HALF_UP);
            long floor = exact.setScale(0, RoundingMode.FLOOR).longValue();
            parts.add(new Part(e.getKey(), floor, exact.subtract(BigDecimal.valueOf(floor))));
            assigned += floor;
        }
        long left = cents - assigned;
        parts.sort(Comparator.comparing(Part::rem).reversed().thenComparing(Part::id));
        Map<Long, Long> result = new HashMap<>();
        for (Part p : parts) {
            result.put(p.id(), p.floor() + (left-- > 0 ? 1 : 0));
        }
        new TreeMap<>(hours).keySet().forEach(k -> out.put(k, BigDecimal.valueOf(result.get(k)).movePointLeft(2)));
        return out;
    }
}
