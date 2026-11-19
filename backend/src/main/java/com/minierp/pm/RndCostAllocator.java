package com.minierp.pm;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.*;

/**
 * 研发人工成本按项目归集（纯函数，研发费用加计扣除的项目辅助账依据）：
 * 每人当月人工成本 × (该人在项目上的工时 / 该人当月出勤工时)；未登记到项目的工时计入"非项目"。
 * 工时登记超过出勤工时时按登记工时占比分摊，不产生负的非项目部分；每人分摊合计 = 其人工成本（尾差给非项目或最后一个项目）。
 */
public final class RndCostAllocator {
    private RndCostAllocator() {
    }

    public static final String NON_PROJECT = "非项目";

    public record EmpMonth(long empId, BigDecimal laborCost, BigDecimal attendanceHours, Map<String, BigDecimal> projectHours) {
    }

    public static Map<String, BigDecimal> allocate(List<EmpMonth> emps) {
        Map<String, BigDecimal> out = new TreeMap<>();
        for (EmpMonth e : emps) {
            BigDecimal logged = e.projectHours().values().stream().reduce(BigDecimal.ZERO, BigDecimal::add);
            BigDecimal base = logged.max(e.attendanceHours());
            if (base.signum() <= 0) {
                out.merge(NON_PROJECT, e.laborCost(), BigDecimal::add);
                continue;
            }
            BigDecimal left = e.laborCost();
            List<Map.Entry<String, BigDecimal>> entries = new ArrayList<>(new TreeMap<>(e.projectHours()).entrySet());
            for (int i = 0; i < entries.size(); i++) {
                BigDecimal part = e.laborCost().multiply(entries.get(i).getValue()).divide(base, 2, RoundingMode.HALF_UP);
                boolean last = i == entries.size() - 1 && logged.compareTo(e.attendanceHours()) >= 0;
                if (last) {
                    part = left;
                }
                out.merge(entries.get(i).getKey(), part, BigDecimal::add);
                left = left.subtract(part);
            }
            if (left.signum() != 0) {
                out.merge(NON_PROJECT, left, BigDecimal::add);
            }
        }
        return out;
    }
}
