package com.minierp.aftersales;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;

/**
 * 质保规则（纯函数）。
 * 保修期：伺服整机 18 个月、其余产品 12 个月，自发货日起算。
 * 售后工单 SLA：P1（客户产线停机）7×24 响应 2 小时 / 解决 24 小时；P2 工作时间 4 / 24 小时；P3 工作时间 8 / 40 小时。
 * 质量保证金（预计负债）：
 *   - 月度计提 = 当月主营业务收入 × 计提比例；
 *   - 年末按"近 12 个月实际索赔 ÷ 12 × 在保平均剩余月数"测算应有余额，差额补提或冲回；
 *   - 次年计提比例按近 12 个月索赔率 × 1.1（留余量），限定在 0.05%~3% 之间。
 */
public final class WarrantyPolicy {
    private WarrantyPolicy() {
    }

    public static int months(String series) {
        return "SERVO".equals(series) ? 18 : 12;
    }

    public static LocalDate end(String series, LocalDate shipDate) {
        return shipDate.plusMonths(months(series));
    }

    public static boolean inWarranty(String series, LocalDate shipDate, LocalDate date) {
        return shipDate != null && !date.isAfter(end(series, shipDate));
    }

    public record TicketSla(double responseHours, double resolveHours, boolean businessHours) {
    }

    public static TicketSla sla(String priority) {
        return switch (priority) {
            case "P1" -> new TicketSla(2, 24, false);
            case "P2" -> new TicketSla(4, 24, true);
            case "P3" -> new TicketSla(8, 40, true);
            default -> throw new IllegalArgumentException("未知优先级 " + priority);
        };
    }

    public static BigDecimal accrual(BigDecimal revenue, BigDecimal rate) {
        return revenue.multiply(rate).setScale(2, RoundingMode.HALF_UP);
    }

    /** 应有余额：月均索赔 × 在保平均剩余月数（保修期一半） */
    public static BigDecimal requiredBalance(BigDecimal claims12m, double avgWarrantyMonths) {
        return claims12m.divide(BigDecimal.valueOf(12), 6, RoundingMode.HALF_UP)
                .multiply(BigDecimal.valueOf(avgWarrantyMonths / 2)).setScale(2, RoundingMode.HALF_UP);
    }

    /** 年末调整额：正数补提，负数冲回 */
    public static BigDecimal trueUp(BigDecimal balance, BigDecimal claims12m, double avgWarrantyMonths) {
        return requiredBalance(claims12m, avgWarrantyMonths).subtract(balance);
    }

    /** 下年计提比例：近 12 个月索赔率 × 1.1，限定在 0.05%~3% 之间 */
    public static BigDecimal nextRate(BigDecimal claims12m, BigDecimal revenue12m) {
        if (revenue12m.signum() <= 0) {
            return new BigDecimal("0.0010");
        }
        BigDecimal r = claims12m.multiply(new BigDecimal("1.1")).divide(revenue12m, 5, RoundingMode.HALF_UP);
        return r.max(new BigDecimal("0.0005")).min(new BigDecimal("0.0300"));
    }
}
