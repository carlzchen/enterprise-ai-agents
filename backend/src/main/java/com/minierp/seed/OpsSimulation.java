package com.minierp.seed;

import com.minierp.common.BizException;
import com.minierp.common.WorkCalendar;
import com.minierp.eam.EamService;
import com.minierp.inventory.InventoryService;
import com.minierp.mes.MesService;
import com.minierp.qms.QmsService;
import com.minierp.sales.Delivery;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Date;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * 现场运营模拟：设备故障与保养（EAM）、伺服整机测试数据与序列号、首检巡检、安灯、班次 OEE（MES）、量具校准与出货检验（QMS）。
 * 埋入的故事：
 * 1. L8 高速冲床 2025-06 中旬 ~ 09 模具与送料机构磨损，故障频发拖累组件线；2025-10 保养周期 30 天缩短为 14 天后故障回落；
 * 2. L5 编码器零位校准台 2025-11 ~ 2026-01 故障增多，与编码器装配良率问题同期，零位偏差 Cpk 明显下降；
 * 3. 2026-04 中旬 ~ 08 S05 磁钢一致性问题：伺服反电势波动变大、均值下移，振动上升，反电势 Cpk 下降、异响客诉增加；
 * 4. 2026 夏季高温，空压机故障增多。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class OpsSimulation {
    private static final DateTimeFormatter YMD = DateTimeFormatter.ofPattern("yyMMdd");
    private final JdbcTemplate jdbc;
    private final EamService eam;
    private final MesService mes;
    private final QmsService qms;
    private final InventoryService inventory;

    private Random rnd;
    private record Eq(long id, String code, String name, String category, String line, double lossFactor, int mtbfDays, String criticality, boolean old) {
    }

    private final List<Eq> equipment = new ArrayList<>();
    private final Map<String, Integer> shifts = new HashMap<>();
    private final Map<String, Long> spareIds = new HashMap<>();
    private final Map<Long, double[]> servoSpec = new HashMap<>(); // productId -> {ke, i0}
    private final Map<LocalDate, Integer> snSeq = new HashMap<>();
    private final Set<String> shortWos = new HashSet<>();
    private boolean l8PmTightened;

    // ================= 初始化 =================

    public void init(LocalDate start, Random random) {
        rnd = random;
        jdbc.query("SELECT line_code, shifts FROM mes_line", rs -> {
            shifts.put(rs.getString(1), rs.getInt(2));
        });
        jdbc.query("SELECT code, id FROM md_material WHERE category = 'SPARE'", rs -> {
            spareIds.put(rs.getString(1), rs.getLong(2));
        });
        createEquipment(start);
        createRoutings();
        createTestSpecs();
        createGauges(start);
        createFmea(start);
        log.info("Ops seeded: {} equipment", equipment.size());
    }

    private void createEquipment(LocalDate start) {
        Object[][] defs = { // line, category, name, count, loss, mtbf, criticality, vendor, cost
                {"L1", "WINDING", "全自动罩极绕线机", 6, 0.12, 60, "B", "田中精机", 180_000}, {"L1", "PRESS", "定子冲片冲床", 1, 0.5, 90, "A", "扬力集团", 650_000},
                {"L1", "ASSEMBLY", "罩极电机装配线", 2, 0.5, 120, "A", "自制", 900_000}, {"L1", "TEST", "罩极性能测试台", 2, 0.3, 150, "B", "艾诺仪器", 120_000},
                {"L2", "WINDING", "贯流电机绕线机", 3, 0.25, 70, "B", "田中精机", 160_000}, {"L2", "BALANCE", "风叶动平衡机", 1, 0.6, 100, "A", "申克", 380_000},
                {"L2", "ASSEMBLY", "贯流风机装配线", 1, 1.0, 120, "A", "自制", 700_000}, {"L2", "TEST", "贯流性能测试台", 1, 0.5, 150, "B", "艾诺仪器", 110_000},
                {"L3", "WINDING", "无刷电机绕线机", 4, 0.2, 70, "B", "日特机械", 260_000}, {"L3", "MAGNETIZE", "无刷转子充磁机", 1, 1.0, 110, "A", "湖南永逸", 420_000},
                {"L3", "ASSEMBLY", "无刷电机装配线", 1, 1.0, 130, "A", "自制", 1_100_000}, {"L3", "TEST", "驱动板功能测试台", 1, 0.5, 150, "B", "致茂电子", 350_000},
                {"L4", "WINDING", "步进电机绕线机", 3, 0.25, 70, "B", "日特机械", 220_000}, {"L4", "ASSEMBLY", "步进电机装配线", 1, 1.0, 130, "A", "自制", 800_000},
                {"L4", "TEST", "步进电机测试台", 1, 0.5, 150, "B", "致茂电子", 180_000},
                {"L5", "TEST", "编码器零位校准台", 1, 1.0, 120, "A", "多摩川", 680_000}, {"L5", "ASSEMBLY", "伺服电机总装线", 1, 1.0, 140, "A", "自制", 2_600_000},
                {"L5", "TEST", "伺服出厂测试台", 2, 0.5, 160, "A", "致茂电子", 520_000},
                {"L6", "ASSEMBLY", "齿轮减速电机装配线", 1, 1.0, 120, "A", "自制", 850_000}, {"L6", "WINDING", "减速电机绕线机", 3, 0.25, 70, "B", "田中精机", 150_000},
                {"L6", "TEST", "减速电机测试台", 1, 0.5, 150, "B", "艾诺仪器", 110_000},
                {"L7", "WINDING", "伺服定子嵌线机", 2, 0.5, 70, "A", "日特机械", 950_000}, {"L7", "VARNISH", "真空浸漆炉", 1, 1.0, 180, "A", "苏州宝联", 1_200_000},
                {"L7", "MAGNETIZE", "伺服转子充磁机", 1, 1.0, 120, "A", "湖南永逸", 560_000}, {"L7", "BALANCE", "转子动平衡机", 1, 0.5, 120, "B", "申克", 420_000},
                {"L8", "PRESS", "高速精密冲床", 1, 1.0, 45, "A", "会田", 3_200_000}, {"L8", "ASSEMBLY", "铁芯自动叠片机", 1, 0.5, 90, "B", "自制", 600_000},
                {null, "UTILITY", "螺杆空压机", 2, 0.0, 80, "A", "阿特拉斯", 450_000}};
        Map<String, Integer> seq = new HashMap<>();
        for (Object[] d : defs) {
            for (int i = 0; i < (Integer) d[3]; i++) {
                String prefix = (d[0] == null ? "PUB" : d[0]) + "-" + ((String) d[1]).substring(0, 3);
                int n = seq.merge(prefix, 1, Integer::sum);
                String code = String.format("EQ-%s-%02d", prefix, n);
                LocalDate bought = LocalDate.of(2013 + rnd.nextInt(11), 1 + rnd.nextInt(12), 1 + rnd.nextInt(28));
                if ("L5".equals(d[0]) || "L7".equals(d[0]) || "L8".equals(d[0])) {
                    bought = LocalDate.of(2020 + rnd.nextInt(4), 1 + rnd.nextInt(12), 1 + rnd.nextInt(28)); // 伺服相关产线较新
                }
                jdbc.update("""
                        INSERT INTO eam_equipment (code, name, category, line_code, model, vendor, purchase_date, purchase_cost, criticality, loss_factor, mtbf_days)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", code, d[2] + ((Integer) d[3] > 1 ? " #" + (i + 1) : ""), d[1], d[0],
                        ((String) d[1]).substring(0, 2) + "-" + (100 + rnd.nextInt(900)), d[7], Date.valueOf(bought), d[8], d[6], d[4], d[5]);
                long id = jdbc.queryForObject("SELECT id FROM eam_equipment WHERE code = ?", Long.class, code);
                equipment.add(new Eq(id, code, (String) d[2], (String) d[1], (String) d[0], (Double) d[4], (Integer) d[5], (String) d[6],
                        bought.getYear() < 2016));
                int interval = "A".equals(d[6]) ? 30 : 60;
                jdbc.update("INSERT INTO eam_pm_plan (equipment_id, name, interval_days, est_minutes, checklist, next_due) VALUES (?,?,?,?,?,?)",
                        id, "A".equals(d[6]) ? "月度保养" : "双月保养", interval, "A".equals(d[6]) ? 120 : 60, checklist((String) d[1]),
                        Date.valueOf(workday(start.plusDays(rnd.nextInt(interval)))));
                if ("A".equals(d[6])) {
                    jdbc.update("INSERT INTO eam_pm_plan (equipment_id, name, interval_days, est_minutes, checklist, next_due) VALUES (?,?,?,?,?,?)",
                            id, "年度大修", 365, 480, "全面拆检、精度校验、易损件更换", Date.valueOf(workday(start.plusDays(30 + rnd.nextInt(300)))));
                }
            }
        }
    }

    private void createRoutings() {
        Object[][] r = { // item_key, line, ops {code, name, sec, key, inspect}
                {"SERIES:SHADED_POLE", "L1", new Object[][]{{"OP10", "定子冲片", 12, 0, null}, {"OP20", "绕线", 34, 1, "FIRST"}, {"OP30", "罩极装配", 36, 0, "PATROL"}, {"OP40", "性能测试", 18, 1, null}, {"OP50", "包装", 6, 0, null}}},
                {"SERIES:CROSSFLOW", "L2", new Object[][]{{"OP10", "绕线", 90, 1, "FIRST"}, {"OP20", "风叶动平衡", 60, 1, null}, {"OP30", "装配", 100, 0, "PATROL"}, {"OP40", "测试", 36, 1, null}, {"OP50", "包装", 10, 0, null}}},
                {"SERIES:BLDC", "L3", new Object[][]{{"OP10", "定子绕线", 110, 1, "FIRST"}, {"OP20", "转子充磁", 45, 1, null}, {"OP30", "装配", 130, 0, "PATROL"}, {"OP40", "驱动板测试", 60, 1, null}, {"OP50", "包装", 12, 0, null}}},
                {"SERIES:STEPPER", "L4", new Object[][]{{"OP10", "绕线", 150, 1, "FIRST"}, {"OP20", "装配", 190, 0, "PATROL"}, {"OP30", "测试", 80, 1, null}, {"OP40", "包装", 20, 0, null}}},
                {"SERIES:SERVO", "L5", new Object[][]{{"OP10", "定转子合装", 700, 1, "FIRST"}, {"OP20", "编码器安装", 520, 1, null}, {"OP30", "零位校准", 480, 1, "PATROL"}, {"OP40", "出厂测试", 650, 1, null}, {"OP50", "振动老化", 300, 0, null}, {"OP60", "包装", 120, 0, null}}},
                {"SERIES:GEAR", "L6", new Object[][]{{"OP10", "绕线", 70, 1, "FIRST"}, {"OP20", "齿轮箱装配", 100, 0, "PATROL"}, {"OP30", "测试", 40, 1, null}, {"OP40", "包装", 12, 0, null}}},
                {"SEMI:SA-ST", "L7", new Object[][]{{"OP10", "嵌线", 620, 1, "FIRST"}, {"OP20", "整形绑扎", 250, 0, null}, {"OP30", "真空浸漆", 240, 1, "PATROL"}}},
                {"SEMI:SA-RT", "L7", new Object[][]{{"OP10", "磁钢粘接", 420, 1, "FIRST"}, {"OP20", "充磁", 180, 1, null}, {"OP30", "动平衡", 300, 1, "PATROL"}}},
                {"SEMI:SA-RC", "L8", new Object[][]{{"OP10", "高速冲压", 380, 1, "FIRST"}, {"OP20", "自动叠片", 320, 0, "PATROL"}}}};
        for (Object[] x : r) {
            int seq = 10;
            for (Object[] op : (Object[][]) x[2]) {
                jdbc.update("INSERT INTO mes_routing (item_key, op_seq, op_code, op_name, line_code, std_seconds, is_key, inspect_type) VALUES (?,?,?,?,?,?,?,?)",
                        x[0], seq, op[0], op[1], x[1], op[2], op[3], op[4]);
                seq += 10;
            }
        }
    }

    private void createTestSpecs() {
        jdbc.query("SELECT id, power_w FROM md_product WHERE series = 'SERVO'", rs -> {
            long id = rs.getLong(1);
            double w = rs.getDouble(2);
            double ke = 10 + w / 100, i0 = 0.25 + w / 2000;
            servoSpec.put(id, new double[]{ke, i0});
            Object[][] specs = {{"back_emf", "反电势常数", "V/krpm", ke, ke * 0.95, ke * 1.05}, {"no_load_current", "空载电流", "A", i0, null, i0 * 1.15},
                    {"encoder_offset", "编码器零位偏差", "°", 0, -0.5, 0.5}, {"insulation_mohm", "绝缘电阻", "MΩ", 800, 100, null},
                    {"vibration", "振动速度", "mm/s", 0.9, null, 1.8}};
            for (Object[] s : specs) {
                jdbc.update("INSERT INTO mes_test_spec (product_id, characteristic, name, unit, nominal, lsl, usl) VALUES (?,?,?,?,?,?,?)",
                        id, s[0], s[1], s[2], s[3], s[4], s[5]);
            }
        });
    }

    private void createGauges(LocalDate start) {
        Object[][] g = {{"数显卡尺", "卡尺", 180, 10, "0-150mm"}, {"外径千分尺", "千分尺", 180, 6, "0-25mm"}, {"扭力扳手", "扭力扳手", 180, 6, "2-25N·m"},
                {"耐压测试仪", "电气安规", 365, 6, "0-5kV"}, {"绝缘电阻测试仪", "电气安规", 365, 4, "0-2000MΩ"}, {"功率分析仪", "电参数", 365, 3, "0-600V/20A"},
                {"编码器测试仪", "编码器", 180, 2, "17bit"}, {"振动测试仪", "振动", 365, 2, "0-50mm/s"}, {"三坐标测量机", "几何量", 365, 1, "700×1000×600"}};
        String[] lines = {"L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"};
        int n = 0;
        for (Object[] x : g) {
            for (int i = 0; i < (Integer) x[3]; i++) {
                int interval = (Integer) x[2];
                LocalDate last = start.minusDays(rnd.nextInt(interval));
                jdbc.update("INSERT INTO qms_gauge (code, name, type, line_code, range_spec, cal_interval_days, last_cal_date, next_due, status) VALUES (?,?,?,?,?,?,?,?,'OK')",
                        String.format("GA-%03d", ++n), x[0] + (i > 0 ? " #" + (i + 1) : ""), x[1],
                        "三坐标测量机".equals(x[0]) ? null : "编码器测试仪".equals(x[0]) ? "L5" : lines[(n + i) % lines.length],
                        x[4], interval, Date.valueOf(last), Date.valueOf(workday(last.plusDays(interval))));
            }
        }
    }

    private void createFmea(LocalDate start) {
        Object[][] f = { // series, step, mode, effect, cause, control, S, O, D, action, owner, revO, revD, revDate
                {"SERVO", "编码器安装", "编码器端子接触不良", "运行中编码器报警、停机", "焊接端子虚焊", "目检 + 出厂测试", 9, 6, 5, "改用压接端子（ECN），增加端子拉力抽检", "林志远", 3, 4, "2026-02-02"},
                {"SERVO", "零位校准", "零位偏差超差", "定位精度差、电流偏大", "校准台夹具磨损", "出厂测试 100% 检测", 7, 4, 3, "校准台夹具纳入月度保养与点检", "冯立新", 2, 3, "2026-02-10"},
                {"SERVO", "转子磁钢粘接", "磁钢磁通一致性差", "反电势偏低、振动异响", "磁钢来料一致性波动", "IQC 抽检磁通", 8, 5, 5, "磁钢切换加严规格（ECN），供应商 S05 整改 8D", "吴志刚", 3, 4, "2026-09-07"},
                {"SERVO", "定子嵌线", "绝缘损伤", "耐压击穿、烧机", "嵌线工具划伤漆包线", "耐压 100% 测试", 10, 2, 2, null, null, null, null, null},
                {"SERVO", "动平衡", "不平衡量超差", "振动、噪声", "平衡机传感器漂移", "动平衡机自动判定", 6, 3, 3, null, null, null, null, null},
                {"SERVO", "出厂测试", "漏检", "不良流出到客户", "测试程序参数错误", "首件确认", 8, 2, 4, "测试程序版本受控，变更走 ECN", "陈家豪", 2, 2, "2025-06-01"},
                {"BLDC", "转子充磁", "充磁不足", "扭矩不足、异响", "充磁电容老化", "充磁后磁通抽检", 7, 4, 4, "充磁机电容纳入年度更换", "冯立新", 2, 4, "2025-12-01"},
                {"BLDC", "驱动板装配", "霍尔板装反", "电机不启动", "防错缺失", "功能测试", 8, 3, 2, "增加霍尔板防呆工装", "罗家明", 1, 2, "2025-04-15"},
                {"BLDC", "定子绕线", "匝数不足", "电流偏大、过热", "绕线机计数故障", "电阻测试", 7, 3, 3, null, null, null, null, null},
                {"SHADED_POLE", "绕线", "断线", "不启动", "张力异常", "电阻测试 100%", 7, 4, 2, "张力器定期更换", "冯立新", 3, 2, "2025-09-01"},
                {"SHADED_POLE", "罩极装配", "罩极环错位", "启动力矩不足", "装配治具磨损", "性能测试", 6, 3, 3, null, null, null, null, null},
                {"SHADED_POLE", "包装", "混料", "发错型号", "人工装箱", "条码扫描", 5, 2, 3, null, null, null, null, null},
                {"CROSSFLOW", "风叶动平衡", "不平衡", "噪声大", "平衡机校准漂移", "平衡机自动判定", 6, 3, 3, null, null, null, null, null},
                {"STEPPER", "装配", "轴承压装偏斜", "异响、寿命短", "压装力未监控", "噪声抽检", 6, 3, 5, "压机增加压力-位移监控", "邓建华", 2, 3, "2025-08-01"},
                {"GEAR", "齿轮箱装配", "齿轮润滑不足", "磨损、噪声", "注脂量不稳定", "噪声抽检", 6, 4, 4, "定量注脂机替代人工", "罗家明", 2, 3, "2026-03-01"}};
        for (Object[] x : f) {
            jdbc.update("""
                    INSERT INTO qms_fmea (series, process_step, failure_mode, effect, cause, current_control, severity, occurrence, detection, action, action_owner,
                      revised_occurrence, revised_detection, updated_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7], x[8], x[9], x[10], x[11], x[12], x[13] == null ? Date.valueOf(start) : Date.valueOf((String) x[13]));
        }
    }

    // ================= 每日：生产前（设备故障与保养 → 当天各产线产能损失） =================

    /** @return 产线 → 当天因设备停机损失的产能比例（0~0.9） */
    public Map<String, Double> beforeProduction(LocalDate d) {
        if (!l8PmTightened && !d.isBefore(LocalDate.of(2025, 10, 8))) {
            // 冲床故障复盘：月度保养改为两周一次
            jdbc.queryForList("SELECT p.id FROM eam_pm_plan p JOIN eam_equipment q ON q.id = p.equipment_id WHERE q.code LIKE 'EQ-L8-PRE%' AND p.interval_days = 30", Long.class)
                    .forEach(id -> eam.changePmInterval(id, 14));
            l8PmTightened = true;
        }
        List<Long> techs = jdbc.queryForList("SELECT e.id FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id WHERE d.code = 'EQP' AND e.status <> 'LEFT'", Long.class);
        Map<String, Double> lossMin = new HashMap<>();
        for (Eq e : equipment) {
            double p = 1.0 / e.mtbfDays() * (e.old() ? 1.5 : 1.0) * storyFactor(e, d);
            if (rnd.nextDouble() >= p) {
                continue;
            }
            LocalDateTime at = d.atTime(8 + rnd.nextInt(8), rnd.nextInt(60));
            String[] fault = fault(e.category());
            long wo;
            try {
                wo = eam.reportBreakdown(e.id(), fault[0], fault[1], "班组长", at);
            } catch (BizException alreadyOpen) {
                continue;
            }
            long tech = techs.isEmpty() ? 0 : techs.get(rnd.nextInt(techs.size()));
            eam.start(wo, tech, at.plusMinutes(10 + rnd.nextInt(30)));
            int downtime = (int) Math.min(900, Math.max(25, Math.exp(Math.log("A".equals(e.criticality()) ? 170 : 110) + rnd.nextGaussian() * 0.6)));
            List<EamService.SpareReq> spares = new ArrayList<>();
            String spare = spareFor(e.category());
            if (spare != null && rnd.nextDouble() < 0.6 && inventory.available(InventoryService.MATERIAL, spareIds.get(spare)).compareTo(BigDecimal.ONE) >= 0) {
                spares.add(new EamService.SpareReq(spareIds.get(spare), BigDecimal.ONE));
            }
            eam.complete(wo, fault[2], fault[3], spares, null, at.plusMinutes(downtime));
            if (e.line() != null) {
                lossMin.merge(e.line(), Math.min(480.0, downtime) * e.lossFactor(), Double::sum);
            }
        }
        // 到期保养（90% 当天完成，其余顺延）
        for (Map<String, Object> pm : jdbc.queryForList("""
                SELECT p.id, p.est_minutes, q.line_code, q.loss_factor, q.category FROM eam_pm_plan p JOIN eam_equipment q ON q.id = p.equipment_id
                WHERE p.next_due <= ? AND q.status <> 'SCRAPPED'""", Date.valueOf(d))) {
            if (rnd.nextDouble() > 0.9 || techs.isEmpty()) {
                continue;
            }
            List<EamService.SpareReq> spares = new ArrayList<>();
            String spare = spareFor((String) pm.get("category"));
            if (spare != null && rnd.nextDouble() < 0.3 && inventory.available(InventoryService.MATERIAL, spareIds.get(spare)).compareTo(BigDecimal.ONE) >= 0) {
                spares.add(new EamService.SpareReq(spareIds.get(spare), BigDecimal.ONE));
            }
            eam.doPm(((Number) pm.get("id")).longValue(), techs.get(rnd.nextInt(techs.size())), d.atTime(12, 0), spares);
            if (pm.get("line_code") != null) {
                lossMin.merge((String) pm.get("line_code"), ((Number) pm.get("est_minutes")).doubleValue()
                        * ((BigDecimal) pm.get("loss_factor")).doubleValue() * 0.5, Double::sum);
            }
        }
        Map<String, Double> frac = new HashMap<>();
        lossMin.forEach((line, m) -> frac.put(line, Math.min(0.9, m / (480.0 * shifts.getOrDefault(line, 1)))));
        return frac;
    }

    private double storyFactor(Eq e, LocalDate d) {
        if (e.code().startsWith("EQ-L8-PRE")) {
            if (between(d, "2025-06-15", "2025-09-30")) {
                return 5;
            }
            return d.isAfter(LocalDate.of(2025, 10, 7)) ? 0.7 : 1;
        }
        if (e.name().startsWith("编码器零位校准台") && between(d, "2025-11-01", "2026-01-31")) {
            return 3;
        }
        if ("UTILITY".equals(e.category()) && between(d, "2026-07-01", "2026-08-31")) {
            return 3;
        }
        return 1;
    }

    // ================= 每日：生产后（测试数据、首检巡检、缺料安灯） =================

    public void recordShortage(String line, String woNo, LocalDate d) {
        if (shortWos.add(woNo)) {
            mes.raiseAndon(line, "MATERIAL", "工单 " + woNo + " 缺料停线等待到货", woNo, d.atTime(8, 30), "班组长");
            mes.respondAndon(woNo, d.atTime(8, 45).plusMinutes(rnd.nextInt(30)));
        }
    }

    public void afterProduction(LocalDate d) {
        // 缺料工单今天恢复报工 → 关闭缺料安灯
        if (!shortWos.isEmpty()) {
            List<String> resumed = jdbc.queryForList("""
                    SELECT DISTINCT w.wo_no FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id WHERE r.report_date = ?""", String.class, Date.valueOf(d));
            for (String wo : resumed) {
                if (shortWos.remove(wo)) {
                    mes.resolveAndon(wo, d.atTime(9 + rnd.nextInt(3), rnd.nextInt(60)));
                }
            }
        }
        servoTests(d);
        ipqc(d);
    }

    /** 伺服出厂测试：良品测试值在规格内，报废品按不良代码对应测试项超差；按故事调整分布 */
    private void servoTests(LocalDate d) {
        List<Map<String, Object>> reports = jdbc.queryForList("""
                SELECT r.good_qty, r.scrap_qty, r.defect_code, w.wo_no, w.product_id, w.line_code FROM mes_wo_report r
                JOIN mes_work_order w ON w.id = r.wo_id JOIN md_product p ON p.id = w.product_id
                WHERE r.report_date = ? AND p.series = 'SERVO'""", Date.valueOf(d));
        boolean encoderIssue = between(d, "2025-11-01", "2026-01-31");
        boolean magnetIssue = between(d, "2026-04-15", "2026-08-31");
        for (Map<String, Object> r : reports) {
            long pid = ((Number) r.get("product_id")).longValue();
            double[] spec = servoSpec.get(pid);
            int good = ((BigDecimal) r.get("good_qty")).intValue(), scrap = ((BigDecimal) r.get("scrap_qty")).intValue();
            List<MesService.TestReq> tests = new ArrayList<>();
            List<Boolean> order = new ArrayList<>();
            for (int i = 0; i < good; i++) {
                order.add(true);
            }
            for (int i = 0; i < scrap; i++) {
                order.add(false);
            }
            Collections.shuffle(order, rnd);
            for (boolean pass : order) {
                double ke, i0, enc, ins, vib;
                int guard = 0;
                do {
                    ke = spec[0] * (1 + (magnetIssue ? -0.008 : 0) + rnd.nextGaussian() * (magnetIssue ? 0.018 : 0.0095));
                    i0 = spec[1] * (1 + rnd.nextGaussian() * 0.03);
                    enc = (encoderIssue ? 0.1 : 0) + rnd.nextGaussian() * (encoderIssue ? 0.19 : 0.085);
                    ins = Math.exp(Math.log(800) + rnd.nextGaussian() * 0.25);
                    vib = (magnetIssue ? 1.05 : 0.9) + rnd.nextGaussian() * (magnetIssue ? 0.2 : 0.14);
                } while (pass && failItem(spec, ke, i0, enc, ins, vib) != null && ++guard < 50);
                String fail = null;
                if (!pass) {
                    switch (String.valueOf(r.get("defect_code"))) {
                        case "ENCODER" -> enc = (rnd.nextBoolean() ? 1 : -1) * (0.52 + rnd.nextDouble() * 0.4);
                        case "NOISE" -> vib = 1.85 + rnd.nextDouble() * 0.8;
                        case "CURRENT" -> i0 = spec[1] * (1.16 + rnd.nextDouble() * 0.15);
                        default -> ke = spec[0] * (rnd.nextBoolean() ? 1.06 : 0.94);
                    }
                    fail = failItem(spec, ke, i0, enc, ins, vib);
                }
                int seq = snSeq.merge(d, 1, Integer::sum);
                tests.add(new MesService.TestReq(String.format("SV%s%05d", d.format(YMD), seq), bd(ke, 3), bd(i0, 4), bd(enc, 4), bd(ins, 1), bd(vib, 3),
                        pass ? "PASS" : "FAIL", fail));
            }
            mes.ingestTests((String) r.get("wo_no"), pid, (String) r.get("line_code"), d, tests);
        }
    }

    private static String failItem(double[] spec, double ke, double i0, double enc, double ins, double vib) {
        if (ke < spec[0] * 0.95 || ke > spec[0] * 1.05) {
            return "BACK_EMF";
        }
        if (Math.abs(enc) > 0.5) {
            return "ENCODER";
        }
        if (i0 > spec[1] * 1.15) {
            return "NO_LOAD_CURRENT";
        }
        if (ins < 100) {
            return "INSULATION";
        }
        return vib > 1.8 ? "VIBRATION" : null;
    }

    private void ipqc(LocalDate d) {
        List<String> inspectors = jdbc.queryForList("""
                SELECT e.name FROM hr_employee e JOIN org_dept dp ON dp.id = e.dept_id JOIN org_position p ON p.id = e.position_id
                WHERE dp.code = 'QA' AND p.code = 'INSP' AND e.status <> 'LEFT'""", String.class);
        if (inspectors.isEmpty()) {
            return;
        }
        // 首检：当天首次报工的工单
        for (Map<String, Object> w : jdbc.queryForList("SELECT wo_no, line_code FROM mes_work_order WHERE actual_start = ?", Date.valueOf(d))) {
            String line = (String) w.get("line_code");
            double failP = "L5".equals(line) && between(d, "2025-11-01", "2026-01-31") ? 0.06 : 0.015;
            LocalDateTime at = d.atTime(8, 20 + rnd.nextInt(30));
            String who = inspectors.get(rnd.nextInt(inspectors.size()));
            if (rnd.nextDouble() < failP) {
                mes.recordIpqc((String) w.get("wo_no"), line, "FIRST", at, who, "FAIL", "首件关键尺寸 / 参数超差，调机");
                mes.recordIpqc((String) w.get("wo_no"), line, "FIRST", at.plusMinutes(50), who, "PASS", "调整后复检合格");
                mes.resolveAndon((String) w.get("wo_no"), at.plusMinutes(50));
            } else {
                mes.recordIpqc((String) w.get("wo_no"), line, "FIRST", at, who, "PASS", null);
            }
        }
        // 巡检：有产出的产线上下午各一次
        for (Map<String, Object> w : jdbc.queryForList("""
                SELECT w.line_code, MIN(w.wo_no) AS wo_no FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id WHERE r.report_date = ? GROUP BY w.line_code""",
                Date.valueOf(d))) {
            for (int h : new int[]{10, 15}) {
                boolean fail = rnd.nextDouble() < 0.008;
                LocalDateTime at = d.atTime(h, rnd.nextInt(60));
                mes.recordIpqc((String) w.get("wo_no"), (String) w.get("line_code"), "PATROL", at, inspectors.get(rnd.nextInt(inspectors.size())),
                        fail ? "FAIL" : "PASS", fail ? "工艺参数偏离，已纠正" : null);
                if (fail) {
                    mes.resolveAndon((String) w.get("wo_no"), at.plusMinutes(40));
                }
            }
        }
    }

    // ================= 每日：日终（班次结算、量具校准） =================

    public void endOfDay(LocalDate d) {
        mes.closeShift(d);
        for (Map<String, Object> g : jdbc.queryForList("SELECT id FROM qms_gauge WHERE next_due <= ? AND status = 'OK'", Date.valueOf(d))) {
            if (rnd.nextDouble() > 0.8) {
                continue; // 部分量具逾期几天才送检
            }
            double r = rnd.nextDouble();
            String result = r < 0.95 ? "PASS" : r < 0.995 ? "ADJUSTED" : "FAIL";
            qms.calibrate(((Number) g.get("id")).longValue(), d, result, "PASS".equals(result) ? null : "示值误差超出允差，已调整", "计量室");
            if ("FAIL".equals(result)) {
                // 校准不合格：停用后购置新量具替换
                jdbc.update("UPDATE qms_gauge SET status = 'SCRAPPED' WHERE id = ?", g.get("id"));
            }
        }
    }

    /** 出货检验：伺服 / 无刷 AQL 0.65，其余 1.0；故事期间不良率上升 */
    public void oqc(Delivery dn, LocalDate d) {
        for (Map<String, Object> l : jdbc.queryForList("""
                SELECT dl.product_id, dl.qty, p.series FROM sal_delivery_line dl JOIN md_product p ON p.id = dl.product_id WHERE dl.delivery_id = ?""", dn.getId())) {
            String series = (String) l.get("series");
            int lot = Math.max(2, ((BigDecimal) l.get("qty")).intValue());
            double aql = "SERVO".equals(series) || "BLDC".equals(series) ? 0.65 : 1.0;
            double p = 0.0015;
            if ("SERVO".equals(series) && between(d, "2025-11-01", "2026-02-28")) {
                p = 0.008;
            } else if (("SERVO".equals(series) || "BLDC".equals(series)) && between(d, "2026-05-01", "2026-09-30")) {
                p = 0.005;
            }
            int n = com.minierp.qms.AqlSampling.plan(lot, aql).sampleSize();
            int defects = 0;
            for (int i = 0; i < n; i++) {
                if (rnd.nextDouble() < p) {
                    defects++;
                }
            }
            qms.oqc(dn.getId(), ((Number) l.get("product_id")).longValue(), lot, aql, defects, d, "郑凯");
        }
    }

    // ================= 工具 =================

    private String[] fault(String category) {
        String[][] f = switch (category) {
            case "WINDING" -> new String[][]{{"W01", "绕线断线频繁", "绕线嘴磨损", "更换绕线嘴并校正排线"}, {"W02", "张力异常", "张力器弹簧疲劳", "更换张力器"},
                    {"W03", "排线不良", "导轨润滑不足", "清洁润滑导轨"}};
            case "PRESS" -> new String[][]{{"P01", "冲片毛刺超标", "模具刃口磨损", "更换刃口镶件并修模"}, {"P02", "送料不到位", "送料机构同步带松弛", "更换同步带、调整送料"},
                    {"P03", "离合器打滑", "离合器片磨损", "更换离合器片"}};
            case "ASSEMBLY" -> new String[][]{{"A01", "压装工位气缸不动作", "气缸密封圈老化", "更换气缸"}, {"A02", "工位检测误报", "光电传感器脏污损坏", "更换传感器"},
                    {"A03", "PLC 通讯中断", "I/O 模块故障", "更换 I/O 模块"}};
            case "TEST" -> new String[][]{{"T01", "测试通讯故障", "线缆接触不良", "重新压接线缆"}, {"T02", "测试结果漂移", "夹具探针磨损", "更换探针组并校准"}};
            case "MAGNETIZE" -> new String[][]{{"M01", "充磁不足报警", "储能电容容量衰减", "更换储能电容"}, {"M02", "线圈过热", "冷却水流量不足", "清洗冷却水路"}};
            case "BALANCE" -> new String[][]{{"B01", "平衡量读数跳动", "传感器松动", "紧固并重新标定"}};
            case "VARNISH" -> new String[][]{{"V01", "真空度不达标", "真空泵磨损", "更换真空泵修理包"}, {"V02", "温控失灵", "热电偶损坏", "更换热电偶"}};
            default -> new String[][]{{"U01", "排气温度过高停机", "滤芯堵塞、环境温度高", "更换滤芯并清洁冷却器"}, {"U02", "漏气压力不足", "管路接头松动", "紧固管路"}};
        };
        return f[rnd.nextInt(f.length)];
    }

    private static String spareFor(String category) {
        return switch (category) {
            case "WINDING" -> "M-SP-NOZ";
            case "PRESS" -> "M-SP-DIE";
            case "ASSEMBLY" -> "M-SP-CYL";
            case "TEST" -> "M-SP-FIX";
            case "MAGNETIZE" -> "M-SP-CAP";
            case "VARNISH" -> "M-SP-PUMP";
            case "BALANCE" -> "M-SP-SENSOR";
            case "UTILITY" -> "M-SP-FILTER";
            default -> null;
        };
    }

    private static String checklist(String category) {
        return switch (category) {
            case "WINDING" -> "清洁导轨、检查绕线嘴与张力、校正计数";
            case "PRESS" -> "检查模具刃口、润滑、离合器与刹车、送料精度";
            case "TEST" -> "检查夹具探针、标准件比对、通讯线缆";
            case "UTILITY" -> "更换滤芯、检查油位与排污";
            default -> "清洁、润滑、紧固、检查易损件与安全装置";
        };
    }

    private static LocalDate workday(LocalDate d) {
        while (!WorkCalendar.isWorkday(d)) {
            d = d.plusDays(1);
        }
        return d;
    }

    private static boolean between(LocalDate d, String from, String to) {
        return !d.isBefore(LocalDate.parse(from)) && !d.isAfter(LocalDate.parse(to));
    }

    private static BigDecimal bd(double v, int scale) {
        return BigDecimal.valueOf(v).setScale(scale, RoundingMode.HALF_UP);
    }
}
