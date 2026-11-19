package com.minierp.energy;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.EnumMap;
import java.util.Map;

/**
 * 广东工商业分时电价（纯函数），参照粤发改价格〔2021〕331 号：
 *   - 峰段 10–12 点、14–19 点；谷段 0–8 点；其余为平段；
 *   - 峰平谷比价 1.7 : 1 : 0.38；
 *   - 7、8、9 月的 11–12 点、15–17 点执行尖峰电价（峰段 × 1.25）。
 * 碳排放按全国电网平均排放因子 0.5366 kgCO2/kWh（生态环境部公布的 2022 年度数值），可按最新公布值调整。
 */
public final class TouTariff {
    private TouTariff() {
    }

    public enum Band { SHARP, PEAK, FLAT, VALLEY }

    public static final BigDecimal GRID_FACTOR = new BigDecimal("0.5366");

    public static Band band(int hour, int month) {
        boolean summer = month >= 7 && month <= 9;
        if (hour < 8) {
            return Band.VALLEY;
        }
        if (summer && (hour == 11 || hour == 15 || hour == 16)) {
            return Band.SHARP;
        }
        if (hour >= 10 && hour < 12 || hour >= 14 && hour < 19) {
            return Band.PEAK;
        }
        return Band.FLAT;
    }

    public static double multiplier(Band b) {
        return switch (b) {
            case SHARP -> 1.7 * 1.25;
            case PEAK -> 1.7;
            case FLAT -> 1.0;
            case VALLEY -> 0.38;
        };
    }

    /**
     * 把电量均摊到运行时段的每个小时，再归到各时段。
     * @param fromHour 开始小时（含），toHour 结束小时（不含）；toHour ≤ fromHour 表示跨零点；from == to 表示全天 24 小时
     */
    public static Map<Band, Double> split(double kwh, int fromHour, int toHour, int month) {
        int hours = toHour > fromHour ? toHour - fromHour : 24 - fromHour + toHour;
        Map<Band, Double> out = new EnumMap<>(Band.class);
        for (Band b : Band.values()) {
            out.put(b, 0.0);
        }
        for (int i = 0; i < hours; i++) {
            Band b = band((fromHour + i) % 24, month);
            out.merge(b, kwh / hours, Double::sum);
        }
        return out;
    }

    /** 电度电费 = Σ 各时段电量 × 平段电价 × 比价 */
    public static BigDecimal energyCharge(Map<Band, ? extends Number> kwh, BigDecimal flatPrice) {
        double sum = 0;
        for (Map.Entry<Band, ? extends Number> e : kwh.entrySet()) {
            sum += e.getValue().doubleValue() * multiplier(e.getKey());
        }
        return flatPrice.multiply(BigDecimal.valueOf(sum)).setScale(2, RoundingMode.HALF_UP);
    }

    /** 碳排放（吨 CO2） */
    public static BigDecimal carbonTons(double kwh) {
        return BigDecimal.valueOf(kwh).multiply(GRID_FACTOR).divide(BigDecimal.valueOf(1000), 3, RoundingMode.HALF_UP);
    }
}
