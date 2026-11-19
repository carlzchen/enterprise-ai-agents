package com.minierp.energy;

import com.minierp.common.BizException;
import com.minierp.energy.TouTariff.Band;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Date;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

/**
 * 能源管理：
 * 1. 电表日读数（尖 / 峰 / 平 / 谷分时段），产线电表对照 MES 良品产出得到单位产品电耗。
 * 2. 月度账单：
 *    - 电度电费按分时电价计算，加上按变压器容量收的基本电费，再加水费；
 *    - 同时算出碳排放和综合电价；
 *    - 付款走费用报销流程，超预算需要总经理审批。
 * 3. 看板：月度趋势、分时段结构、各电表构成、单位产品电耗、夜间谷段基线（用来发现空压机泄漏这类异常）。
 */
@Service
@RequiredArgsConstructor
public class EnergyService {
    /** 平段电价（元/kWh，含代理购电与输配电价，不含基本电费） */
    public static final BigDecimal FLAT_PRICE = new BigDecimal("0.6200");
    /** 基本电费：2500 kVA 变压器 × 23.3 元/kVA·月 */
    public static final BigDecimal CAPACITY_CHARGE = new BigDecimal("58250.00");
    public static final BigDecimal WATER_PRICE = new BigDecimal("4.10");
    private final JdbcTemplate jdbc;

    @Transactional
    public void record(String meterCode, LocalDate date, Map<Band, Double> kwh) {
        Long meter = jdbc.queryForList("SELECT id FROM en_meter WHERE code = ?", Long.class, meterCode).stream().findFirst()
                .orElseThrow(() -> new BizException("电表不存在：" + meterCode));
        double total = kwh.values().stream().mapToDouble(Double::doubleValue).sum();
        jdbc.update("""
                INSERT INTO en_reading (meter_id, read_date, sharp_kwh, peak_kwh, flat_kwh, valley_kwh, total_kwh) VALUES (?,?,?,?,?,?,?)
                ON DUPLICATE KEY UPDATE sharp_kwh = VALUES(sharp_kwh), peak_kwh = VALUES(peak_kwh), flat_kwh = VALUES(flat_kwh),
                  valley_kwh = VALUES(valley_kwh), total_kwh = VALUES(total_kwh)""", meter, Date.valueOf(date),
                r2(kwh.getOrDefault(Band.SHARP, 0.0)), r2(kwh.getOrDefault(Band.PEAK, 0.0)), r2(kwh.getOrDefault(Band.FLAT, 0.0)),
                r2(kwh.getOrDefault(Band.VALLEY, 0.0)), r2(total));
    }

    /** 生成月度账单（重复生成会覆盖尚未付款的账单） */
    @Transactional
    public Map<String, Object> bill(YearMonth ym, BigDecimal waterM3) {
        Date f = Date.valueOf(ym.atDay(1)), t = Date.valueOf(ym.atEndOfMonth());
        Map<String, Object> s = jdbc.queryForMap("""
                SELECT COALESCE(SUM(sharp_kwh), 0) AS sharp, COALESCE(SUM(peak_kwh), 0) AS peak, COALESCE(SUM(flat_kwh), 0) AS flat,
                       COALESCE(SUM(valley_kwh), 0) AS valley, COALESCE(SUM(total_kwh), 0) AS total FROM en_reading WHERE read_date BETWEEN ? AND ?""", f, t);
        BigDecimal total = (BigDecimal) s.get("total");
        if (total.signum() <= 0) {
            throw new BizException(ym + " 没有电表读数");
        }
        Map<Band, Double> kwh = new EnumMap<>(Band.class);
        kwh.put(Band.SHARP, ((BigDecimal) s.get("sharp")).doubleValue());
        kwh.put(Band.PEAK, ((BigDecimal) s.get("peak")).doubleValue());
        kwh.put(Band.FLAT, ((BigDecimal) s.get("flat")).doubleValue());
        kwh.put(Band.VALLEY, ((BigDecimal) s.get("valley")).doubleValue());
        BigDecimal energy = TouTariff.energyCharge(kwh, FLAT_PRICE);
        BigDecimal water = waterM3.multiply(WATER_PRICE).setScale(2, RoundingMode.HALF_UP);
        BigDecimal amount = energy.add(CAPACITY_CHARGE).add(water);
        BigDecimal good = jdbc.queryForObject("""
                SELECT COALESCE(SUM(d.good_qty), 0) FROM mes_line_day d JOIN mes_line l ON l.line_code = d.line_code
                WHERE l.series <> 'SEMI' AND d.work_date BETWEEN ? AND ?""", BigDecimal.class, f, t);
        BigDecimal perUnit = good.signum() > 0 ? total.divide(good, 4, RoundingMode.HALF_UP) : BigDecimal.ZERO;
        jdbc.update("DELETE FROM en_bill WHERE period = ? AND expense_id IS NULL", ym.toString());
        jdbc.update("""
                INSERT INTO en_bill (period, total_kwh, sharp_kwh, peak_kwh, flat_kwh, valley_kwh, flat_price, energy_charge, capacity_charge, water_m3, water_charge,
                  total_amount, avg_price, carbon_t, good_qty, kwh_per_unit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ym.toString(), total, s.get("sharp"), s.get("peak"), s.get("flat"), s.get("valley"), FLAT_PRICE, energy, CAPACITY_CHARGE, waterM3, water,
                amount, energy.add(CAPACITY_CHARGE).divide(total, 4, RoundingMode.HALF_UP), TouTariff.carbonTons(total.doubleValue()), good, perUnit);
        return jdbc.queryForMap("SELECT * FROM en_bill WHERE period = ?", ym.toString());
    }

    public Map<String, Object> dashboard(YearMonth ym) {
        Date f = Date.valueOf(ym.atDay(1)), t = Date.valueOf(ym.atEndOfMonth());
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("bill", jdbc.queryForList("SELECT * FROM en_bill WHERE period = ?", ym.toString()).stream().findFirst().orElse(null));
        out.put("bills", jdbc.queryForList("SELECT * FROM en_bill WHERE period <= ? ORDER BY period DESC LIMIT 24", ym.toString()).reversed());
        out.put("meters", jdbc.queryForList("""
                SELECT code, name, type, line_code, total_kwh, sharp_kwh + peak_kwh AS peak_kwh, flat_kwh, valley_kwh FROM v_energy_meter_month
                WHERE period = ? ORDER BY total_kwh DESC""", ym.toString()));
        out.put("lines", jdbc.queryForList("""
                SELECT line_code, SUM(total_kwh) AS kwh, SUM(good_qty) AS good_qty, ROUND(SUM(total_kwh) / NULLIF(SUM(good_qty), 0), 4) AS kwh_per_unit
                FROM v_energy_line_day WHERE read_date BETWEEN ? AND ? GROUP BY line_code ORDER BY line_code""", f, t));
        out.put("lineTrend", jdbc.queryForList("""
                SELECT line_code, DATE_FORMAT(read_date, '%Y-%m') AS period, ROUND(SUM(total_kwh) / NULLIF(SUM(good_qty), 0), 4) AS kwh_per_unit
                FROM v_energy_line_day WHERE read_date > DATE_SUB(?, INTERVAL 24 MONTH) AND read_date <= ? GROUP BY line_code, period ORDER BY period, line_code""", t, t));
        out.put("daily", jdbc.queryForList("""
                SELECT r.read_date, SUM(r.total_kwh) AS total_kwh, SUM(r.sharp_kwh + r.peak_kwh) AS peak_kwh, SUM(r.valley_kwh) AS valley_kwh,
                       SUM(CASE WHEN m.code = 'AIR' THEN r.valley_kwh END) AS air_valley_kwh
                FROM en_reading r JOIN en_meter m ON m.id = r.meter_id WHERE r.read_date BETWEEN ? AND ? GROUP BY r.read_date ORDER BY r.read_date""", f, t));
        // 空压站夜间谷段基线：非生产日（周末 / 节假日）的空压机电量应接近 0，抬升说明管网泄漏
        out.put("airBaseline", jdbc.queryForList("""
                SELECT DATE_FORMAT(r.read_date, '%Y-%m') AS period, ROUND(AVG(r.total_kwh), 1) AS idle_day_kwh FROM en_reading r JOIN en_meter m ON m.id = r.meter_id
                WHERE m.code = 'AIR' AND r.read_date > DATE_SUB(?, INTERVAL 24 MONTH) AND r.read_date <= ?
                  AND NOT EXISTS (SELECT 1 FROM mes_line_day d WHERE d.work_date = r.read_date) GROUP BY period ORDER BY period""", t, t));
        return out;
    }

    private static BigDecimal r2(double v) {
        return BigDecimal.valueOf(v).setScale(2, RoundingMode.HALF_UP);
    }
}
