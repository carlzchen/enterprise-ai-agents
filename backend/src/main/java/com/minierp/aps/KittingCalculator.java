package com.minierp.aps;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.*;

/**
 * 物料齐套检查（纯函数）：工单按交期顺序依次占用 ① 主料现存量 → ② 替代料现存量 → ③ 主料在途采购 → ④ 替代料在途；
 * 仍不够即为缺料。返回每个工单的最早齐套日、状态和使用的替代料。
 * 多级 BOM 时用 {@link Pool} 逐层计算：下层组件工单排完产后，把其完工日作为上层的"在途供应"加入资源池。
 */
public final class KittingCalculator {
    private KittingCalculator() {
    }

    public record Demand(long woId, LocalDate due, Map<Long, BigDecimal> materials) {
    }

    public record Supply(long materialId, LocalDate date, BigDecimal qty) {
    }

    /** @param ratio 1 单位主料 = ratio 单位替代料 */
    public record Sub(long materialId, BigDecimal ratio) {
    }

    /** @param substitutions 替代料 id → 占用数量 */
    public record Kit(long woId, String status, LocalDate earliestStart, List<Long> shortMaterials, Map<Long, BigDecimal> substitutions) {
        public Kit(long woId, String status, LocalDate earliestStart, List<Long> shortMaterials) {
            this(woId, status, earliestStart, shortMaterials, Map.of());
        }
    }

    public static Map<Long, Kit> check(List<Demand> demands, Map<Long, BigDecimal> stock, List<Supply> supplies, LocalDate planDate,
                                       int shortageLeadDays) {
        return new Pool(stock, supplies, Map.of()).check(demands, planDate, shortageLeadDays);
    }

    /** 可跨多次 check 共享的资源池（现存量 + 在途），用于多级 BOM 逐层齐套 */
    public static final class Pool {
        private final Map<Long, BigDecimal> onHand;
        private final Map<Long, Deque<Supply>> incoming = new HashMap<>();
        private final Map<Long, List<Sub>> substitutes;

        public Pool(Map<Long, BigDecimal> stock, List<Supply> supplies, Map<Long, List<Sub>> substitutes) {
            this.onHand = new HashMap<>(stock);
            this.substitutes = substitutes;
            supplies.forEach(this::addSupply);
        }

        public void addSupply(Supply s) {
            Deque<Supply> q = incoming.computeIfAbsent(s.materialId(), k -> new ArrayDeque<>());
            List<Supply> all = new ArrayList<>(q);
            all.add(s);
            all.sort(Comparator.comparing(Supply::date));
            q.clear();
            q.addAll(all);
        }

        public Map<Long, Kit> check(List<Demand> demands, LocalDate planDate, int shortageLeadDays) {
            Map<Long, Kit> result = new LinkedHashMap<>();
            List<Demand> sorted = new ArrayList<>(demands);
            sorted.sort(Comparator.comparing(Demand::due).thenComparing(Demand::woId));
            for (Demand d : sorted) {
                LocalDate earliest = null;
                List<Long> shortMats = new ArrayList<>();
                Map<Long, BigDecimal> used = new LinkedHashMap<>();
                boolean waiting = false;
                for (var m : new TreeMap<>(d.materials()).entrySet()) {
                    long mid = m.getKey();
                    List<Sub> subs = substitutes.getOrDefault(mid, List.of());
                    BigDecimal missing = takeOnHand(mid, m.getValue(), BigDecimal.ONE, null);
                    for (Sub s : subs) {
                        missing = takeOnHand(s.materialId(), missing, s.ratio(), used);
                    }
                    Arrival a = takeIncoming(mid, missing, BigDecimal.ONE, null);
                    missing = a.missing();
                    LocalDate arrive = a.date();
                    for (Sub s : subs) {
                        a = takeIncoming(s.materialId(), missing, s.ratio(), used);
                        missing = a.missing();
                        arrive = later(arrive, a.date());
                    }
                    if (missing.signum() > 0) {
                        shortMats.add(mid);
                    } else if (arrive != null) {
                        waiting = true;
                        earliest = later(earliest, arrive);
                    }
                }
                if (!shortMats.isEmpty()) {
                    LocalDate s = planDate.plusDays(shortageLeadDays);
                    result.put(d.woId(), new Kit(d.woId(), "SHORTAGE", later(earliest, s), shortMats, used));
                } else if (waiting && earliest.isAfter(planDate)) {
                    result.put(d.woId(), new Kit(d.woId(), "WAIT_PO", earliest, List.of(), used));
                } else {
                    result.put(d.woId(), new Kit(d.woId(), "READY", null, List.of(), used));
                }
            }
            return result;
        }

        /** 从现存量占用：need 为主料口径，ratio 换算成该物料数量；返回仍缺的主料口径数量 */
        private BigDecimal takeOnHand(long mid, BigDecimal need, BigDecimal ratio, Map<Long, BigDecimal> used) {
            if (need.signum() <= 0) {
                return BigDecimal.ZERO;
            }
            BigDecimal have = onHand.getOrDefault(mid, BigDecimal.ZERO);
            BigDecimal cover = need.min(have.max(BigDecimal.ZERO).divide(ratio, 3, RoundingMode.DOWN));
            if (cover.signum() <= 0) {
                return need;
            }
            BigDecimal qty = cover.multiply(ratio);
            onHand.put(mid, have.subtract(qty));
            if (used != null) {
                used.merge(mid, qty, BigDecimal::add);
            }
            return need.subtract(cover);
        }

        private record Arrival(BigDecimal missing, LocalDate date) {
        }

        private Arrival takeIncoming(long mid, BigDecimal need, BigDecimal ratio, Map<Long, BigDecimal> used) {
            Deque<Supply> q = incoming.getOrDefault(mid, new ArrayDeque<>());
            LocalDate arrive = null;
            BigDecimal missing = need;
            while (missing.signum() > 0 && !q.isEmpty()) {
                Supply s = q.pollFirst();
                BigDecimal cover = missing.min(s.qty().divide(ratio, 3, RoundingMode.DOWN));
                if (cover.signum() <= 0) {
                    continue;
                }
                BigDecimal qty = cover.multiply(ratio);
                missing = missing.subtract(cover);
                arrive = s.date();
                if (used != null) {
                    used.merge(mid, qty, BigDecimal::add);
                }
                if (s.qty().compareTo(qty) > 0) {
                    q.addFirst(new Supply(mid, s.date(), s.qty().subtract(qty)));
                }
            }
            return new Arrival(missing, arrive);
        }
    }

    private static LocalDate later(LocalDate a, LocalDate b) {
        if (a == null) {
            return b;
        }
        return b == null || a.isAfter(b) ? a : b;
    }
}
