package com.minierp.wms;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** 库位分配策略（纯函数，便于单元测试） */
public final class BinAllocator {
    private BinAllocator() {
    }

    /** @param itemQty 该库位上本物料数量；@param totalQty 该库位所有物料总量 */
    public record Bin(long locationId, BigDecimal itemQty, BigDecimal totalQty) {
    }

    public record Allocation(long locationId, BigDecimal qty) {
    }

    /** 上架：优先放到已存放该物料且数量最多的库位（同物料集中存放），否则放到最空的库位 */
    public static long putaway(List<Bin> zoneBins) {
        if (zoneBins.isEmpty()) {
            throw new IllegalArgumentException("库区没有可用库位");
        }
        return zoneBins.stream().filter(b -> b.itemQty().signum() > 0)
                .max(Comparator.comparing(Bin::itemQty))
                .or(() -> zoneBins.stream().min(Comparator.comparing(Bin::totalQty).thenComparing(Bin::locationId)))
                .orElseThrow().locationId();
    }

    /** 拣货：先拣数量少的库位（尽快清空零散库位），不足时抛出异常 */
    public static List<Allocation> pick(List<Bin> binsWithItem, BigDecimal need) {
        List<Bin> sorted = binsWithItem.stream().filter(b -> b.itemQty().signum() > 0)
                .sorted(Comparator.comparing(Bin::itemQty).thenComparing(Bin::locationId)).toList();
        BigDecimal available = sorted.stream().map(Bin::itemQty).reduce(BigDecimal.ZERO, BigDecimal::add);
        if (available.compareTo(need) < 0) {
            throw new IllegalArgumentException("库位库存不足：需要 " + need + "，可拣 " + available);
        }
        List<Allocation> result = new ArrayList<>();
        BigDecimal remaining = need;
        for (Bin b : sorted) {
            if (remaining.signum() <= 0) {
                break;
            }
            BigDecimal take = b.itemQty().min(remaining);
            result.add(new Allocation(b.locationId(), take));
            remaining = remaining.subtract(take);
        }
        return result;
    }
}
