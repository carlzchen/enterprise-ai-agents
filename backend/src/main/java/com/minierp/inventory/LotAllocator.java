package com.minierp.inventory;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;

/** 批次先进先出分配（纯函数）：lots 已按入库日期升序 */
public final class LotAllocator {
    private LotAllocator() {
    }

    public record Lot(long id, BigDecimal remaining) {
    }

    public record Take(long lotId, BigDecimal qty) {
    }

    /** 批次余量不足时，不足部分返回 lotId = -1（历史期初数据无批次时的兜底，调用方记录为"无批次"） */
    public static List<Take> fifo(List<Lot> lots, BigDecimal need) {
        List<Take> takes = new ArrayList<>();
        BigDecimal left = need;
        for (Lot l : lots) {
            if (left.signum() <= 0) {
                break;
            }
            if (l.remaining().signum() <= 0) {
                continue;
            }
            BigDecimal t = l.remaining().min(left);
            takes.add(new Take(l.id(), t));
            left = left.subtract(t);
        }
        if (left.signum() > 0) {
            takes.add(new Take(-1, left));
        }
        return takes;
    }
}
