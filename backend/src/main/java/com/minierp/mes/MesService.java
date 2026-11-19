package com.minierp.mes;

import com.minierp.common.BizException;
import com.minierp.eam.OeeCalculator;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

/**
 * MES 现场层：
 * - 测试台数据接入：整机序列号 + 出厂测试值（伺服），出货时按先进先出绑定到发货单，实现单台追溯；
 * - 过程检验（首检 / 巡检），不合格自动拉质量安灯；
 * - 安灯：缺料 / 设备 / 质量异常的呼叫、响应、解决；
 * - 班次结算：按产线计算 OEE（设备停机 × 损失比例 → 时间开动率）与劳动效率（标准工时 / 出勤工时）。
 */
@Service
@RequiredArgsConstructor
public class MesService {
    private final JdbcTemplate jdbc;

    public record TestReq(String sn, BigDecimal backEmf, BigDecimal noLoadCurrent, BigDecimal encoderOffset, BigDecimal insulationMohm,
                          BigDecimal vibration, String result, String failItem) {
    }

    // ---------- 测试台数据 / 序列号 ----------

    /** 测试台批量上传：登记整机序列号与测试值（同一 SN 重复上传拒绝） */
    @Transactional
    public int ingestTests(String woNo, long productId, String lineCode, LocalDate date, List<TestReq> tests) {
        if (tests.isEmpty()) {
            return 0;
        }
        jdbc.batchUpdate("INSERT INTO mes_serial (sn, product_id, wo_no, produced_date, result) VALUES (?,?,?,?,?)",
                tests.stream().map(t -> new Object[]{t.sn(), productId, woNo, Date.valueOf(date), t.result()}).toList());
        jdbc.batchUpdate("""
                INSERT INTO mes_test_record (sn, product_id, line_code, test_date, back_emf, no_load_current, encoder_offset, insulation_mohm, vibration, result, fail_item)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", tests.stream().map(t -> new Object[]{t.sn(), productId, lineCode, Date.valueOf(date), t.backEmf(),
                t.noLoadCurrent(), t.encoderOffset(), t.insulationMohm(), t.vibration(), t.result(), t.failItem()}).toList());
        return tests.size();
    }

    /** 出货绑定序列号：该产品未出货的合格整机按生产先后绑定到发货单 */
    @Transactional
    public int assignSerials(long deliveryId, long productId, BigDecimal qty) {
        return jdbc.update("UPDATE mes_serial SET delivery_id = ? WHERE product_id = ? AND delivery_id IS NULL AND result = 'PASS' ORDER BY id LIMIT ?",
                deliveryId, productId, qty.intValue());
    }

    public Map<String, Object> serial(String sn) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM v_serial WHERE sn = ?", sn);
        if (r.isEmpty()) {
            throw new com.minierp.common.NotFoundException("序列号", sn);
        }
        Map<String, Object> s = new LinkedHashMap<>(r.get(0));
        s.put("ipqc", jdbc.queryForList("SELECT * FROM mes_ipqc WHERE wo_no = ? ORDER BY inspect_time", s.get("wo_no")));
        return s;
    }

    // ---------- 过程检验 ----------

    @Transactional
    public void recordIpqc(String woNo, String lineCode, String type, LocalDateTime at, String inspector, String result, String remark) {
        jdbc.update("INSERT INTO mes_ipqc (wo_no, line_code, type, inspect_time, inspector, result, remark) VALUES (?,?,?,?,?,?,?)",
                woNo, lineCode, type, Timestamp.valueOf(at), inspector, result, remark);
        if ("FAIL".equals(result)) {
            raiseAndon(lineCode, "QUALITY", ("FIRST".equals(type) ? "首检" : "巡检") + "不合格：" + (remark == null ? "" : remark), woNo, at, inspector);
        }
    }

    // ---------- 安灯 ----------

    @Transactional
    public long raiseAndon(String lineCode, String type, String desc, String refNo, LocalDateTime at, String by) {
        jdbc.update("INSERT INTO mes_andon (line_code, type, description, ref_no, raised_at, status, raised_by) VALUES (?,?,?,?,?,'OPEN',?)",
                lineCode, type, desc.length() > 200 ? desc.substring(0, 200) : desc, refNo, Timestamp.valueOf(at), by);
        return jdbc.queryForObject("SELECT MAX(id) FROM mes_andon WHERE line_code = ?", Long.class, lineCode);
    }

    @Transactional
    public void respondAndon(String refNo, LocalDateTime at) {
        jdbc.update("UPDATE mes_andon SET responded_at = ? WHERE ref_no = ? AND status = 'OPEN' AND responded_at IS NULL", Timestamp.valueOf(at), refNo);
    }

    @Transactional
    public void resolveAndon(String refNo, LocalDateTime at) {
        jdbc.update("UPDATE mes_andon SET responded_at = COALESCE(responded_at, ?), resolved_at = ?, status = 'RESOLVED' WHERE ref_no = ? AND status = 'OPEN'",
                Timestamp.valueOf(at), Timestamp.valueOf(at), refNo);
    }

    @Transactional
    public void resolveAndonById(long id, LocalDateTime at) {
        int n = jdbc.update("UPDATE mes_andon SET responded_at = COALESCE(responded_at, ?), resolved_at = ?, status = 'RESOLVED' WHERE id = ? AND status = 'OPEN'",
                Timestamp.valueOf(at), Timestamp.valueOf(at), id);
        if (n == 0) {
            throw new BizException("安灯不存在或已关闭");
        }
    }

    // ---------- 班次结算 ----------

    /** 结算某天各产线：OEE 与劳动效率，写入 mes_line_day（重复结算覆盖） */
    @Transactional
    public void closeShift(LocalDate date) {
        Map<String, double[]> out = new HashMap<>(); // line -> {total, good, stdHours}
        jdbc.query("""
                SELECT w.line_code, SUM(r.good_qty + r.scrap_qty), SUM(r.good_qty),
                       SUM((r.good_qty + r.scrap_qty) * COALESCE((SELECT SUM(ro.std_seconds) FROM mes_routing ro WHERE ro.line_code = w.line_code
                            AND ro.item_key = CASE WHEN w.item_type = 'PRODUCT' THEN CONCAT('SERIES:', (SELECT p.series FROM md_product p WHERE p.id = w.product_id))
                                                   ELSE CONCAT('SEMI:', LEFT((SELECT m.code FROM md_material m WHERE m.id = w.material_id), 5)) END), 0)) / 3600
                FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id WHERE r.report_date = ? GROUP BY w.line_code""", rs -> {
            out.put(rs.getString(1), new double[]{rs.getDouble(2), rs.getDouble(3), rs.getDouble(4)});
        }, Date.valueOf(date));
        Map<String, Double> hours = new HashMap<>();
        jdbc.query("""
                SELECT e.line_code, SUM(a.work_hours + a.overtime_hours) FROM hr_attendance a JOIN hr_employee e ON e.id = a.employee_id
                WHERE a.work_date = ? AND e.line_code IS NOT NULL GROUP BY e.line_code""", rs -> {
            hours.put(rs.getString(1), rs.getDouble(2));
        }, Date.valueOf(date));
        Map<String, Double> loss = new HashMap<>();
        jdbc.query("""
                SELECT q.line_code, SUM(LEAST(w.downtime_min, 480) * q.loss_factor * IF(w.type = 'PM', 0.5, 1))
                FROM eam_work_order w JOIN eam_equipment q ON q.id = w.equipment_id
                WHERE DATE(w.reported_at) = ? AND w.status = 'DONE' AND q.line_code IS NOT NULL GROUP BY q.line_code""", rs -> {
            loss.put(rs.getString(1), rs.getDouble(2));
        }, Date.valueOf(date));
        jdbc.update("DELETE FROM mes_line_day WHERE work_date = ?", Date.valueOf(date));
        List<Object[]> rows = new ArrayList<>();
        jdbc.query("SELECT line_code, daily_capacity, shifts FROM mes_line", rs -> {
            String line = rs.getString(1);
            int cap = rs.getInt(2) * rs.getInt(3);
            int planned = 480 * rs.getInt(3);
            double[] o = out.getOrDefault(line, new double[3]);
            int lossMin = (int) Math.round(Math.min(planned, loss.getOrDefault(line, 0.0)));
            OeeCalculator.Oee oee = OeeCalculator.compute(planned, lossMin, o[0], o[1], cap);
            rows.add(new Object[]{line, Date.valueOf(date), planned, lossMin, cap, o[0], o[1], Math.round(o[2] * 100) / 100.0,
                    Math.round(hours.getOrDefault(line, 0.0) * 100) / 100.0, oee.availability(), oee.performance(), oee.quality(), oee.oee()});
        });
        jdbc.batchUpdate("""
                INSERT INTO mes_line_day (line_code, work_date, planned_min, loss_min, capacity, total_qty, good_qty, std_hours, actual_hours,
                  availability, performance, quality, oee) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows);
    }

    /** 车间看板：某天各产线产出、OEE、效率、未关闭安灯、IPQC 不合格；近 30 天 OEE 趋势 */
    public Map<String, Object> board(LocalDate date) {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("date", date);
        r.put("lines", jdbc.queryForList("""
                SELECT d.*, l.name, ROUND(d.std_hours / NULLIF(d.actual_hours, 0), 4) AS labor_efficiency,
                  (SELECT COUNT(*) FROM mes_andon a WHERE a.line_code = d.line_code AND DATE(a.raised_at) = d.work_date) AS andons,
                  (SELECT COUNT(*) FROM mes_ipqc i WHERE i.line_code = d.line_code AND DATE(i.inspect_time) = d.work_date AND i.result = 'FAIL') AS ipqc_fail
                FROM mes_line_day d JOIN mes_line l ON l.line_code = d.line_code WHERE d.work_date = ? ORDER BY d.line_code""", Date.valueOf(date)));
        r.put("trend", jdbc.queryForList("""
                SELECT work_date, line_code, oee, availability FROM mes_line_day WHERE work_date > ? AND work_date <= ? ORDER BY work_date""",
                Date.valueOf(date.minusDays(45)), Date.valueOf(date)));
        r.put("openAndons", jdbc.queryForList("SELECT * FROM v_andon WHERE status = 'OPEN' ORDER BY raised_at DESC LIMIT 20"));
        r.put("monthlyOee", jdbc.queryForList("""
                SELECT DATE_FORMAT(work_date, '%Y-%m') AS month, line_code, ROUND(AVG(oee), 4) AS oee, ROUND(AVG(availability), 4) AS availability,
                       ROUND(SUM(std_hours) / NULLIF(SUM(actual_hours), 0), 4) AS labor_efficiency
                FROM mes_line_day WHERE work_date > ? AND work_date <= ? AND total_qty > 0 GROUP BY month, line_code ORDER BY month""",
                Date.valueOf(date.minusMonths(12)), Date.valueOf(date)));
        return r;
    }
}
