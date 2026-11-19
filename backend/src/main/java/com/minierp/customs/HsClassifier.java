package com.minierp.customs;

import java.math.BigDecimal;

/** 商品归类（纯函数）：按产品系列与功率给出 HS 编码与出口退税率（电机、风机退税率均为 13%） */
public final class HsClassifier {
    private HsClassifier() {
    }

    public record Hs(String code, String description, BigDecimal refundRate) {
    }

    private static final BigDecimal R13 = new BigDecimal("0.13");

    public static Hs classify(String series, BigDecimal powerW) {
        double w = powerW == null ? 0 : powerW.doubleValue();
        return switch (series) {
            case "SHADED_POLE" -> new Hs("8501400000", "其他单相交流电动机", R13);
            case "CROSSFLOW" -> new Hs("8414590090", "其他风机、风扇", R13);
            case "BLDC" -> new Hs(w <= 750 ? "8501310000" : "8501320000", "直流电动机", R13);
            case "STEPPER" -> new Hs("8501310000", "直流电动机（输出功率 ≤ 750W）", R13);
            case "SERVO" -> new Hs(w <= 750 ? "8501510010" : "8501520000", "多相交流电动机", R13);
            case "GEAR" -> w <= 37.5 ? new Hs("8501109990", "输出功率 ≤ 37.5W 的电动机", R13) : new Hs("8501400000", "其他单相交流电动机", R13);
            default -> new Hs("8501400000", "其他电动机", R13);
        };
    }
}
