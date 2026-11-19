package com.minierp.sales;

import java.math.BigDecimal;

/** 信用额度规则（纯函数）：已占用 + 本单 > 额度 即超限；额度为 0 视为不允许赊销 */
public final class CreditPolicy {
    private CreditPolicy() {
    }

    public static boolean exceeds(BigDecimal limit, BigDecimal exposure, BigDecimal orderAmount) {
        return exposure.add(orderAmount).compareTo(limit) > 0;
    }
}
