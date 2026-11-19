package com.minierp.transfer;

import java.math.BigDecimal;
import java.util.Map;

/** 发货仓选择（纯函数）：客户所在区域有前置仓且整单库存足够时就近发货，否则从总仓发货，避免拆单 */
public final class WarehouseRouter {
    private WarehouseRouter() {
    }

    public static long choose(Long regionalWarehouse, Map<Long, BigDecimal> regionalStock, Map<Long, BigDecimal> need, long mainWarehouse) {
        if (regionalWarehouse == null || need.isEmpty()) {
            return mainWarehouse;
        }
        boolean covered = need.entrySet().stream()
                .allMatch(e -> regionalStock.getOrDefault(e.getKey(), BigDecimal.ZERO).compareTo(e.getValue()) >= 0);
        return covered ? regionalWarehouse : mainWarehouse;
    }
}
