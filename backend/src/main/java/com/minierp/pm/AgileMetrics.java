package com.minierp.pm;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

/**
 * 敏捷度量（纯函数）：燃尽图、速率与预测、周期时间分位数、累积流图。
 */
public final class AgileMetrics {
    private AgileMetrics() {
    }

    public record Completion(LocalDate date, int points) {
    }

    public record BurnPoint(LocalDate date, int remaining, double ideal) {
    }

    /** 燃尽：按工作日列出剩余故事点与理想线；scopeAdded 为迭代中途加入的故事点（按加入日计入） */
    public static List<BurnPoint> burndown(List<LocalDate> workdays, int committed, List<Completion> done, List<Completion> scopeAdded) {
        List<BurnPoint> out = new ArrayList<>();
        int n = workdays.size();
        for (int i = 0; i < n; i++) {
            LocalDate d = workdays.get(i);
            int added = scopeAdded.stream().filter(c -> !c.date().isAfter(d)).mapToInt(Completion::points).sum();
            int burned = done.stream().filter(c -> !c.date().isAfter(d)).mapToInt(Completion::points).sum();
            double ideal = n == 1 ? 0 : committed * (1 - i / (double) (n - 1));
            out.add(new BurnPoint(d, committed + added - burned, Math.round(ideal * 10) / 10.0));
        }
        return out;
    }

    public record Velocity(double average, double stdDev, int sprints, double forecastLow, double forecastHigh) {
    }

    /** 速率：最近 N 个迭代的完成点数均值，预测区间取均值 ± 1 个标准差 */
    public static Velocity velocity(List<Integer> completed, int lastN) {
        List<Integer> xs = completed.subList(Math.max(0, completed.size() - lastN), completed.size());
        if (xs.isEmpty()) {
            return new Velocity(0, 0, 0, 0, 0);
        }
        double avg = xs.stream().mapToInt(Integer::intValue).average().orElse(0);
        double sd = xs.size() < 2 ? 0 : Math.sqrt(xs.stream().mapToDouble(x -> (x - avg) * (x - avg)).sum() / (xs.size() - 1));
        return new Velocity(r1(avg), r1(sd), xs.size(), r1(Math.max(0, avg - sd)), r1(avg + sd));
    }

    /** 周期时间分位数（最近邻秩法）：p50 / p85 / p95，单位与输入一致 */
    public static Map<String, Double> percentiles(List<Double> values) {
        Map<String, Double> m = new LinkedHashMap<>();
        if (values.isEmpty()) {
            return m;
        }
        List<Double> s = new ArrayList<>(values);
        Collections.sort(s);
        for (int p : new int[]{50, 85, 95}) {
            int rank = (int) Math.ceil(p / 100.0 * s.size());
            m.put("p" + p, s.get(Math.max(0, rank - 1)));
        }
        return m;
    }

    public record Transition(long issueId, LocalDateTime at, String toStatus) {
    }

    /** 累积流：每天结束时各状态的问题数（问题创建即视为进入 TODO） */
    public static Map<LocalDate, Map<String, Integer>> cumulativeFlow(List<Transition> transitions, LocalDate from, LocalDate to) {
        List<Transition> sorted = new ArrayList<>(transitions);
        sorted.sort(Comparator.comparing(Transition::at).thenComparing(Transition::issueId));
        Map<Long, String> state = new HashMap<>();
        Map<LocalDate, Map<String, Integer>> out = new LinkedHashMap<>();
        int i = 0;
        for (LocalDate d = from; !d.isAfter(to); d = d.plusDays(1)) {
            while (i < sorted.size() && !sorted.get(i).at().toLocalDate().isAfter(d)) {
                state.put(sorted.get(i).issueId(), sorted.get(i).toStatus());
                i++;
            }
            Map<String, Integer> counts = new LinkedHashMap<>();
            for (String s : List.of("TODO", "IN_PROGRESS", "IN_REVIEW", "DONE")) {
                counts.put(s, 0);
            }
            state.values().forEach(s -> counts.merge(s, 1, Integer::sum));
            out.put(d, counts);
        }
        return out;
    }

    private static double r1(double v) {
        return Math.round(v * 10) / 10.0;
    }
}
