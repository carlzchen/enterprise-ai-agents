package com.minierp.aps;

import java.math.BigDecimal;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.*;

/**
 * 有限产能排产（纯函数）：每条产线逐个工作日装载日产能。
 * 每天优先继续昨天未完成的工单，其次在"物料已齐套（earliestStart ≤ 当天）"的工单中选交期最早的（EDD）；
 * 缺料工单自动让位给后面已齐套的工单。输出每个工单的开工/完工日期、延期天数与每日负荷。
 */
public final class ApsScheduler {
    private ApsScheduler() {
    }

    private static final int MAX_DAYS = 400;

    /** @param earliestStart 物料最早齐套日，null 表示已齐套 */
    public record Order(long woId, String line, BigDecimal qty, LocalDate due, LocalDate earliestStart) {
        public Order(long woId, String line, BigDecimal qty, LocalDate due) {
            this(woId, line, qty, due, null);
        }
    }

    public record Slot(long woId, String line, int seq, BigDecimal qty, LocalDate start, LocalDate end, LocalDate due, int lateDays) {
    }

    public record Result(List<Slot> slots, Map<String, SortedMap<LocalDate, BigDecimal>> load) {
    }

    public static Result schedule(Map<String, Integer> capacity, List<Order> orders, LocalDate from, Set<LocalDate> holidays) {
        List<Slot> slots = new ArrayList<>();
        Map<String, SortedMap<LocalDate, BigDecimal>> load = new TreeMap<>();
        Map<String, List<Order>> byLine = new TreeMap<>();
        for (Order o : orders) {
            byLine.computeIfAbsent(o.line(), k -> new ArrayList<>()).add(o);
        }
        for (var e : byLine.entrySet()) {
            String line = e.getKey();
            int cap = capacity.getOrDefault(line, 0);
            if (cap <= 0) {
                throw new IllegalArgumentException("产线没有产能配置：" + line);
            }
            List<Order> pool = new ArrayList<>(e.getValue());
            pool.sort(Comparator.comparing(Order::due).thenComparing(Order::woId));
            Map<Long, BigDecimal> left = new HashMap<>();
            Map<Long, LocalDate> start = new HashMap<>(), end = new HashMap<>();
            pool.forEach(o -> left.put(o.woId(), o.qty()));
            SortedMap<LocalDate, BigDecimal> lineLoad = load.computeIfAbsent(line, k -> new TreeMap<>());
            List<Long> startOrder = new ArrayList<>();
            Order active = null;
            LocalDate day = nextWorkday(from, holidays);
            for (int n = 0; n < MAX_DAYS && left.values().stream().anyMatch(q -> q.signum() > 0); n++) {
                BigDecimal free = BigDecimal.valueOf(cap);
                while (free.signum() > 0) {
                    final LocalDate today = day;
                    if (active == null || left.get(active.woId()).signum() <= 0) {
                        active = pool.stream().filter(o -> left.get(o.woId()).signum() > 0)
                                .filter(o -> o.earliestStart() == null || !o.earliestStart().isAfter(today)).findFirst().orElse(null);
                    }
                    if (active == null) {
                        break; // 今天没有齐套的工单可做
                    }
                    BigDecimal take = left.get(active.woId()).min(free);
                    if (!start.containsKey(active.woId())) {
                        start.put(active.woId(), day);
                        startOrder.add(active.woId());
                    }
                    end.put(active.woId(), day);
                    left.merge(active.woId(), take.negate(), BigDecimal::add);
                    lineLoad.merge(day, take, BigDecimal::add);
                    free = free.subtract(take);
                }
                day = nextWorkday(day.plusDays(1), holidays);
            }
            Map<Long, Order> byId = new HashMap<>();
            pool.forEach(o -> byId.put(o.woId(), o));
            int seq = 1;
            for (Long id : startOrder) {
                Order o = byId.get(id);
                LocalDate s = start.get(id), f = end.get(id);
                slots.add(new Slot(id, line, seq++, o.qty(), s, f, o.due(), (int) Math.max(0, ChronoUnit.DAYS.between(o.due(), f))));
            }
        }
        return new Result(slots, load);
    }

    static LocalDate nextWorkday(LocalDate d, Set<LocalDate> holidays) {
        while (d.getDayOfWeek() == DayOfWeek.SATURDAY || d.getDayOfWeek() == DayOfWeek.SUNDAY || holidays.contains(d)) {
            d = d.plusDays(1);
        }
        return d;
    }
}
