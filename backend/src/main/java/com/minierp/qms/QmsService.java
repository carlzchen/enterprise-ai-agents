package com.minierp.qms;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Date;
import java.time.LocalDate;
import java.util.*;

/**
 * 质量管理深化：
 * - 8D：中、高严重度客诉进入分析即开 8D（D1~D3 自动带出），客诉关闭时补齐 D4~D8 并结案；
 * - SPC：从出厂测试数据按天抽子组，出均值-极差图、Cpk / Ppk 与判异；
 * - 量具校准：到期校准，不合格锁定停用；
 * - OQC：按 GB/T 2828.1 自动给出抽样方案并判定。
 */
@Service
@RequiredArgsConstructor
public class QmsService {
    private static final Set<String> CHARACTERISTICS = Set.of("back_emf", "no_load_current", "encoder_offset", "insulation_mohm", "vibration");
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;

    // ---------- 8D ----------

    @Transactional
    public void open8d(long complaintId, LocalDate date, String owner) {
        Map<String, Object> c = jdbc.queryForMap("SELECT * FROM v_complaint WHERE id = ?", complaintId);
        if (!List.of("HIGH", "MEDIUM").contains(c.get("severity"))) {
            return;
        }
        Integer exists = jdbc.queryForObject("SELECT COUNT(*) FROM qms_8d WHERE complaint_id = ?", Integer.class, complaintId);
        if (exists != null && exists > 0) {
            return;
        }
        String no = docNo.next("8D", date);
        String problem = String.format("客户 %s 反馈 %s %s，涉及 %s 台（%s）", c.get("customer_name"), c.get("product_code"), defectName((String) c.get("defect_type")),
                ((Number) c.get("qty_affected")).intValue(), c.get("complaint_no"));
        jdbc.update("""
                INSERT INTO qms_8d (report_no, complaint_id, current_step, status, d1_team, d2_problem, d3_containment, opened_date, due_date, owner)
                VALUES (?,?,4,'OPEN',?,?,?,?,?,?)""", no, complaintId, "质量（组长 " + owner + "）、生产、研发、采购", problem,
                "库存与在途同批次产品隔离复检；客户端不良品换货；同批次发货清单排查（批次正向追溯）", Date.valueOf(date),
                Date.valueOf(date.plusDays("HIGH".equals(c.get("severity")) ? 20 : 30)), owner);
    }

    @Transactional
    public void close8d(long complaintId, String rootCause, String action, LocalDate date) {
        jdbc.update("""
                UPDATE qms_8d SET d4_root_cause = ?, d5_actions = ?, d6_verification = ?, d7_prevention = ?, d8_closure = ?,
                  current_step = 8, status = 'CLOSED', closed_date = ? WHERE complaint_id = ? AND status = 'OPEN'""",
                rootCause, action, "措施实施后连续 3 批出厂测试与 OQC 无同类不良，客户确认", "更新 PFMEA 与控制计划，相关作业指导书修订并培训",
                "8D 结案，经验教训纳入知识库", Date.valueOf(date), complaintId);
    }

    @Transactional
    public void update8dStep(long id, int step, String content, LocalDate date) {
        if (step < 1 || step > 8) {
            throw new BizException("8D 步骤为 1~8");
        }
        String col = new String[]{"d1_team", "d2_problem", "d3_containment", "d4_root_cause", "d5_actions", "d6_verification", "d7_prevention", "d8_closure"}[step - 1];
        int n = jdbc.update("UPDATE qms_8d SET " + col + " = ?, current_step = GREATEST(current_step, ?) WHERE id = ? AND status = 'OPEN'", content, step, id);
        if (n == 0) {
            throw new BizException("8D 不存在或已结案");
        }
        if (step == 8) {
            jdbc.update("UPDATE qms_8d SET status = 'CLOSED', closed_date = ? WHERE id = ?", Date.valueOf(date), id);
        }
    }

    // ---------- SPC ----------

    /** 按天取前 n 台测试值为一个子组（模拟每天定时抽检），分析均值-极差控制图与过程能力 */
    public Map<String, Object> spc(String productCode, String characteristic, LocalDate from, LocalDate to, int subgroupSize) {
        if (!CHARACTERISTICS.contains(characteristic)) {
            throw new BizException("不支持的特性：" + characteristic);
        }
        int n = Math.max(2, Math.min(10, subgroupSize));
        Long pid = jdbc.queryForList("SELECT id FROM md_product WHERE code = ?", Long.class, productCode).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("产品", productCode));
        Map<String, Object> spec = jdbc.queryForList("SELECT * FROM mes_test_spec WHERE product_id = ? AND characteristic = ?", pid, characteristic)
                .stream().findFirst().orElseThrow(() -> new BizException("该产品没有此项测试规格"));
        TreeMap<LocalDate, List<Double>> byDay = new TreeMap<>();
        jdbc.query("""
                SELECT test_date, %s FROM (SELECT test_date, %s, ROW_NUMBER() OVER (PARTITION BY test_date ORDER BY id) AS rn
                  FROM mes_test_record WHERE product_id = ? AND test_date BETWEEN ? AND ?) x WHERE rn <= ? ORDER BY test_date""".formatted(characteristic, characteristic),
                rs -> {
                    byDay.computeIfAbsent(rs.getDate(1).toLocalDate(), k -> new ArrayList<>()).add(rs.getDouble(2));
                }, pid, Date.valueOf(from), Date.valueOf(to), n);
        List<LocalDate> days = byDay.entrySet().stream().filter(e -> e.getValue().size() >= 2).map(Map.Entry::getKey).toList();
        if (days.isEmpty()) {
            throw new BizException("所选期间没有测试数据");
        }
        double lsl = spec.get("lsl") == null ? Double.NEGATIVE_INFINITY : ((Number) spec.get("lsl")).doubleValue();
        double usl = spec.get("usl") == null ? Double.POSITIVE_INFINITY : ((Number) spec.get("usl")).doubleValue();
        SpcCalculator.Result res = SpcCalculator.analyze(days.stream().map(byDay::get).toList(), lsl, usl);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("product", productCode);
        out.put("characteristic", characteristic);
        out.put("spec", spec);
        out.put("subgroupSize", n);
        out.put("days", days);
        out.put("xbar", res.xbar());
        out.put("range", res.range());
        out.put("center", res.center());
        out.put("ucl", res.ucl());
        out.put("lcl", res.lcl());
        out.put("rBar", res.rBar());
        out.put("rUcl", res.rUcl());
        out.put("sigma", res.sigmaWithin());
        out.put("cp", Double.isFinite(res.cp()) ? res.cp() : null);
        out.put("cpk", Double.isFinite(res.cpk()) ? res.cpk() : null);
        out.put("ppk", Double.isFinite(res.ppk()) ? res.ppk() : null);
        out.put("violations", res.violations().stream().map(v -> Map.of("date", days.get(v.index()), "rule", v.rule())).toList());
        out.put("failRate", jdbc.queryForObject("SELECT ROUND(AVG(result = 'FAIL'), 4) FROM mes_test_record WHERE product_id = ? AND test_date BETWEEN ? AND ?",
                Double.class, pid, Date.valueOf(from), Date.valueOf(to)));
        return out;
    }

    /** 按月的 Cpk 走势（每月单独计算），用于看过程能力变化 */
    public List<Map<String, Object>> cpkTrend(String productCode, String characteristic, LocalDate from, LocalDate to) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (LocalDate m = from.withDayOfMonth(1); !m.isAfter(to); m = m.plusMonths(1)) {
            try {
                Map<String, Object> r = spc(productCode, characteristic, m, m.plusMonths(1).minusDays(1), 5);
                out.add(Map.of("month", m.toString().substring(0, 7), "cpk", r.get("cpk") == null ? 0 : r.get("cpk"),
                        "failRate", r.get("failRate") == null ? 0 : r.get("failRate")));
            } catch (BizException ignored) {
                // 当月无数据
            }
        }
        return out;
    }

    // ---------- 量具 ----------

    @Transactional
    public void calibrate(long gaugeId, LocalDate date, String result, String deviation, String by) {
        Map<String, Object> g = jdbc.queryForList("SELECT * FROM qms_gauge WHERE id = ?", gaugeId).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("量具", gaugeId));
        if (!List.of("PASS", "ADJUSTED", "FAIL").contains(result)) {
            throw new BizException("校准结果为 PASS / ADJUSTED / FAIL");
        }
        jdbc.update("INSERT INTO qms_calibration (gauge_id, cal_date, due_date, result, deviation, calibrated_by, certificate_no) VALUES (?,?,?,?,?,?,?)",
                gaugeId, Date.valueOf(date), g.get("next_due"), result, deviation, by, "CAL-" + date.toString().replace("-", "") + "-" + gaugeId);
        int interval = ((Number) g.get("cal_interval_days")).intValue();
        LocalDate next = date.plusDays(interval);
        while (!com.minierp.common.WorkCalendar.isWorkday(next)) {
            next = next.plusDays(1); // 到期日落在节假日顺延到工作日，避免送检"逾期"失真
        }
        jdbc.update("UPDATE qms_gauge SET last_cal_date = ?, next_due = ?, status = ? WHERE id = ?", Date.valueOf(date),
                Date.valueOf(next), "FAIL".equals(result) ? "LOCKED" : "OK", gaugeId);
    }

    // ---------- OQC ----------

    @Transactional
    public AqlSampling.Plan oqc(long deliveryId, long productId, int lotSize, double aql, int defects, LocalDate date, String inspector) {
        AqlSampling.Plan plan = AqlSampling.plan(Math.max(2, lotSize), aql);
        if (defects < 0 || defects > plan.sampleSize()) {
            throw new BizException("不良数无效");
        }
        jdbc.update("""
                INSERT INTO qms_oqc (delivery_id, product_id, inspect_date, lot_size, aql, code_letter, sample_size, ac, re, defects, result, inspector)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", deliveryId, productId, Date.valueOf(date), lotSize, aql, String.valueOf(plan.codeLetter()),
                plan.sampleSize(), plan.ac(), plan.re(), defects, defects <= plan.ac() ? "PASS" : "REWORK", inspector);
        return plan;
    }

    private static String defectName(String code) {
        return switch (code) {
            case "NOISE" -> "异响";
            case "NO_START" -> "不启动";
            case "OVERHEAT" -> "过热";
            case "SPEED" -> "转速异常";
            case "ENCODER" -> "编码器故障";
            case "APPEARANCE" -> "外观不良";
            default -> code;
        };
    }
}
