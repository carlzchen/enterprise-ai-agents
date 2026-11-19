package com.minierp.seed;

import com.minierp.common.WorkCalendar;
import com.minierp.ehs.EhsService;
import com.minierp.energy.EnergyService;
import com.minierp.energy.TouTariff;
import com.minierp.energy.TouTariff.Band;
import com.minierp.finance.Expense;
import com.minierp.finance.FinanceService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Date;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 安环与能源模拟。
 * 能源：
 *   - 各电表日读数按产线产出、班次时段、季节（空调）生成，分到尖 / 峰 / 平 / 谷；
 *   - 次月第一个工作日出账，账单金额走"生产部 水电费"报销流程（替代原来的随机水电费）。
 * 故事：
 *   - 夏季空调负荷让电费超预算；
 *   - 2025-10 下旬空压管网泄漏，周末空载电量抬升，2025-11-24 能源分析发现、12-08 修复；
 *   - 2026-03 起伺服老化房移到谷段运行，峰段占比下降。
 * 安环：
 *   - 每周二安环部巡检，员工随手拍，隐患整改后复查；
 *   - 未遂事件、急救事件随机发生；
 *   - 2025-06 冲压线压手损工事故，引发冲床安全专项检查；
 *   - 2025-12 仓库叉车撞伤损工事故，整改为人车分流；
 *   - 设备维修动火、高处、受限空间作业走作业票审批；
 *   - 危废按月产生、按季转移；2026 年上半年 HW12 接收单位换证暂停接收，8 月贮存接近一年期限，更换处置单位转移。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class EhsSimulation {
    private final JdbcTemplate jdbc;
    private final EhsService ehs;
    private final EnergyService energy;
    private final FinanceService finance;
    private final HrSimulation hr;

    private Random rnd;
    private LocalDate lastRead;
    private final Map<String, Long> depts = new HashMap<>();
    private final TreeMap<LocalDate, List<Runnable>> tasks = new TreeMap<>();

    /** 产线：待机功率 kW、单位产品电耗 kWh */
    private static final Object[][] LINES = {{"L1", 50, 0.12}, {"L2", 25, 0.35}, {"L3", 35, 0.50}, {"L4", 25, 0.40},
            {"L5", 40, 2.40}, {"L6", 35, 0.45}, {"L7", 30, 1.00}, {"L8", 60, 3.50}};
    private static final double[] HVAC_SEASON = {0, 0.5, 0.5, 0.7, 1.0, 1.4, 1.9, 2.2, 2.2, 1.8, 1.2, 0.7, 0.5};
    private static final String DISPOSER = "深圳市绿源危废处置有限公司", DISPOSER2 = "惠州市清源环境科技有限公司";
    private static final Object[][] WASTES = {{"HW12", "浸漆废物（废绝缘漆、漆渣）", "WS1", 0.9, 1.4, 3800}, {"HW08", "废矿物油（设备润滑、液压油）", "EQP", 0.25, 0.5, 2500},
            {"HW49", "含油抹布手套、废包装桶", "WS2", 0.4, 0.7, 4200}};

    // ================= 初始化 =================

    public void init(LocalDate start, Random random) {
        rnd = random;
        lastRead = start.minusDays(1);
        jdbc.query("SELECT code, id FROM org_dept", rs -> {
            depts.put(rs.getString(1), rs.getLong(2));
        });
        for (Object[] l : LINES) {
            String line = (String) l[0];
            String ws = List.of("L1", "L2", "L6").contains(line) ? "WS1" : "WS2";
            jdbc.update("INSERT INTO en_meter (code, name, type, line_code, dept_id) VALUES (?,?,'LINE',?,?)", line,
                    jdbc.queryForObject("SELECT name FROM mes_line WHERE line_code = ?", String.class, line) + " 电表", line, depts.get(ws));
        }
        Object[][] meters = {{"AIR", "空压站", "UTILITY", "EQP"}, {"HVAC", "中央空调与净化", "UTILITY", "EQP"}, {"AGING", "伺服老化房", "UTILITY", "WS2"},
                {"OFFICE", "办公研发楼", "OFFICE", "ADM"}, {"DORM", "员工宿舍", "DORM", "ADM"}};
        for (Object[] m : meters) {
            jdbc.update("INSERT INTO en_meter (code, name, type, dept_id) VALUES (?,?,?,?)", m[0], m[1], m[2], depts.get(m[3]));
        }
        // 期初危废库存（上线前已产生、尚未转移）
        ehs.generateWaste("HW12", (String) WASTES[0][1], new BigDecimal("1.200"), depts.get("WS1"), start.minusDays(40));
        ehs.generateWaste("HW08", (String) WASTES[1][1], new BigDecimal("0.350"), depts.get("EQP"), start.minusDays(50));
        ehs.generateWaste("HW49", (String) WASTES[2][1], new BigDecimal("0.500"), depts.get("WS2"), start.minusDays(25));
        scheduleStories();
    }

    // ================= 每日 =================

    public void onDay(LocalDate d) {
        for (LocalDate x = lastRead.plusDays(1); !x.isAfter(d); x = x.plusDays(1)) {
            readMeters(x);
        }
        lastRead = d;
        YearMonth ym = YearMonth.from(d);
        if (jdbc.queryForObject("SELECT COUNT(*) FROM en_bill WHERE period = ?", Integer.class, ym.minusMonths(1).toString()) == 0
                && jdbc.queryForObject("SELECT COUNT(*) FROM en_reading WHERE read_date BETWEEN ? AND ?", Integer.class,
                Date.valueOf(ym.minusMonths(1).atDay(1)), Date.valueOf(ym.minusMonths(1).atEndOfMonth())) > 0) {
            payBill(ym.minusMonths(1), d);
        }
        if (d.getDayOfWeek() == DayOfWeek.TUESDAY) {
            inspection(d);
        }
        employeeReports(d);
        randomIncidents(d);
        permits(d);
        if (d.equals(WorkCalendar.lastWorkday(ym))) {
            generateWaste(d);
        }
        if (List.of(2, 5, 8, 11).contains(d.getMonthValue()) && d.getDayOfMonth() >= 15 && d.getDayOfMonth() <= 21 && d.getDayOfWeek() == DayOfWeek.WEDNESDAY) {
            transferWaste(d);
        }
        // 任务里可能再排当天或更早的后续任务（如收货后检验），循环到当天无待办为止，保证在月结前入账
        for (List<List<Runnable>> due = drain(d); !due.isEmpty(); due = drain(d)) {
            due.forEach(rs -> rs.forEach(Runnable::run));
        }
    }

    // ================= 能源 =================

    private double leakKw(LocalDate x) {
        LocalDate s = LocalDate.of(2025, 10, 20), fixed = LocalDate.of(2025, 12, 8);
        if (x.isBefore(s) || !x.isBefore(fixed)) {
            return 8;
        }
        return 8 + Math.min(37, 37.0 * (x.toEpochDay() - s.toEpochDay()) / 21);
    }

    private void readMeters(LocalDate x) {
        int month = x.getMonthValue();
        Map<String, Map<String, Object>> day = new HashMap<>();
        jdbc.queryForList("SELECT d.line_code, d.total_qty, d.good_qty, l.shifts FROM mes_line_day d JOIN mes_line l ON l.line_code = d.line_code WHERE d.work_date = ?",
                Date.valueOf(x)).forEach(r -> day.put((String) r.get("line_code"), r));
        boolean production = !day.isEmpty();
        double lineTotal = 0;
        for (Object[] l : LINES) {
            Map<String, Object> r = day.get((String) l[0]);
            Map<Band, Double> kwh;
            if (r == null) {
                kwh = TouTariff.split(((Integer) l[1]) * 24 * 0.12 * noise(), 0, 0, month); // 非生产日：照明、保温、控制柜待机
            } else {
                int shifts = ((Number) r.get("shifts")).intValue();
                int[] window = shifts >= 3 ? new int[]{0, 0} : shifts == 2 ? new int[]{8, 0} : new int[]{8, 17};
                int hours = shifts >= 3 ? 24 : shifts == 2 ? 16 : 9;
                double v = ((Integer) l[1]) * hours + (Double) l[2] * ((BigDecimal) r.get("total_qty")).doubleValue();
                kwh = TouTariff.split(v * noise(), window[0], window[1], month);
            }
            lineTotal += kwh.values().stream().mapToDouble(Double::doubleValue).sum();
            energy.record((String) l[0], x, kwh);
        }
        double leak = leakKw(x);
        energy.record("AIR", x, production ? TouTariff.split((0.26 * lineTotal + leak * 24) * noise(), 0, 0, month)
                : TouTariff.split(100 + leak * 24 * 0.6, 0, 0, month));
        double hvac = 2500 * HVAC_SEASON[month] * (production ? 1 : 0.25) * noise();
        energy.record("HVAC", x, TouTariff.split(hvac, 8, 20, month));
        Map<String, Object> l5 = day.get("L5");
        double aging = l5 == null ? 0 : 3.0 * ((BigDecimal) l5.get("good_qty")).doubleValue() * noise();
        Map<Band, Double> ag;
        if (!x.isBefore(LocalDate.of(2026, 3, 2))) { // 老化房移到谷段：70% 在 0–8 点
            ag = TouTariff.split(aging * 0.7, 0, 8, month);
            TouTariff.split(aging * 0.3, 8, 0, month).forEach((b, v) -> ag.merge(b, v, Double::sum));
        } else {
            ag = TouTariff.split(aging, 8, 0, month);
        }
        energy.record("AGING", x, ag);
        energy.record("OFFICE", x, production ? TouTariff.split(2200 * noise(), 8, 20, month) : TouTariff.split(700 * noise(), 0, 0, month));
        energy.record("DORM", x, TouTariff.split(1400 * (month >= 6 && month <= 9 ? 1.4 : 1.0) * noise(), 18, 8, month));
    }

    private double noise() {
        return 0.95 + rnd.nextDouble() * 0.1;
    }

    /** 月度账单：出账后按"生产部 水电费"走报销流程付款（超预算需总经理审批） */
    private void payBill(YearMonth ym, LocalDate d) {
        int headcount = jdbc.queryForObject("SELECT COUNT(*) FROM hr_employee WHERE status <> 'LEFT' AND leave_date IS NULL", Integer.class);
        double water = headcount * (ym.getMonthValue() >= 6 && ym.getMonthValue() <= 9 ? 22 : 17) * noise();
        Map<String, Object> bill = energy.bill(ym, BigDecimal.valueOf(Math.round(water)));
        BigDecimal amount = (BigDecimal) bill.get("total_amount");
        BigDecimal kwh = (BigDecimal) bill.get("total_kwh");
        BigDecimal peakShare = ((BigDecimal) bill.get("peak_kwh")).add((BigDecimal) bill.get("sharp_kwh")).divide(kwh, 3, RoundingMode.HALF_UP);
        Long emp = hr.pickEmployee("设备动力部");
        Expense e = new Expense();
        e.setExpenseDate(d);
        e.setDept("生产部");
        e.setCategory("水电费");
        e.setAmount(amount);
        e.setEmployee(emp == null ? "设备动力部" : hr.nameOf(emp));
        e.setDescription(ym + " 电费 " + kwh.divide(BigDecimal.valueOf(10000), 1, RoundingMode.HALF_UP) + " 万kWh（峰段 " + peakShare.movePointRight(2).stripTrailingZeros().toPlainString()
                + "%）+ 水费 " + bill.get("water_m3") + " 吨");
        Expense saved = finance.submit(e, emp, d.atTime(9, 30));
        jdbc.update("UPDATE en_bill SET expense_id = ? WHERE period = ?", saved.getId(), ym.toString());
    }

    // ================= 隐患 =================

    private static final String[][] HAZARDS = { // dept, category, location, description
            {"WS1", "电气", "绕线车间", "配电柜门未关闭，柜前堆放物料"}, {"WS1", "化学品", "浸漆房", "浸漆房防爆灯罩破损"}, {"WS1", "消防", "装配区", "灭火器压力不足"},
            {"WS1", "机械", "绕线机", "绕线机防护罩拆除未恢复"}, {"WS1", "作业环境", "浸漆房", "排风量不足，有机废气浓度偏高"},
            {"WS2", "机械", "冲压线", "冲床光栅遮挡测试不合格"}, {"WS2", "电气", "伺服总装线", "临时接线未走线槽"}, {"WS2", "机械", "嵌线工位", "治具锐边未倒角"},
            {"WS2", "消防", "老化房", "老化房烟感探测器被遮挡"}, {"WS2", "职业健康", "冲压线", "员工未佩戴耳塞（噪声 88dB）"},
            {"LOG", "叉车", "成品仓", "叉车倒车蜂鸣器失效"}, {"LOG", "消防", "成品仓", "消防通道被托盘占用"}, {"LOG", "机械", "原材料仓", "货架横梁变形未更换"},
            {"LOG", "叉车", "装卸平台", "装卸平台未设置车轮止挡"}, {"EQP", "电气", "空压站", "空压机接地线松动"}, {"EQP", "化学品", "油品库", "废油桶未加盖、无防渗漏托盘"},
            {"EQP", "消防", "配电房", "配电房挡鼠板缺失"}, {"RND", "电气", "EMC 实验室", "测试台漏电保护器未定期测试"}, {"ADM", "消防", "员工宿舍", "宿舍私拉电线给电动车充电"},
            {"ADM", "作业环境", "食堂", "食堂地面湿滑无警示"}, {"QA", "化学品", "实验室", "化学试剂未上锁管理"}};

    private void inspection(LocalDate d) {
        int n = 2 + rnd.nextInt(4);
        for (int i = 0; i < n; i++) {
            String[] h = pickHazard(d);
            hazard("INSPECTION", h, rnd.nextDouble() < 0.04 ? "MAJOR" : "GENERAL", d, ehsStaff());
        }
    }

    private void employeeReports(LocalDate d) {
        if (rnd.nextDouble() < 0.35) {
            String[] h = pickHazard(d);
            Long who = hr.pickEmployee(deptName(h[0]));
            hazard("REPORT", h, "GENERAL", d, who);
        }
    }

    private String[] pickHazard(LocalDate d) {
        int m = d.getMonthValue();
        if (m >= 6 && m <= 9 && rnd.nextDouble() < 0.2) {
            return new String[]{rnd.nextBoolean() ? "WS1" : "WS2", "职业健康", "车间", "高温时段未落实防暑降温措施（车间温度 35℃）"};
        }
        if ((m >= 10 || m == 1) && rnd.nextDouble() < 0.15) {
            return new String[]{"LOG", "叉车", "成品仓", "旺季出货叉车超速、人车混行"};
        }
        return HAZARDS[rnd.nextInt(HAZARDS.length)];
    }

    private void hazard(String source, String[] h, String level, LocalDate d, Long foundBy) {
        long owner = head(h[0]);
        long id = ehs.reportHazard(new EhsService.HazardReq(source, depts.get(h[0]), h[2], h[1], h[3], level, d, foundBy, owner));
        int days = "MAJOR".equals(level) ? 10 + rnd.nextInt(28) : 1 + rnd.nextInt(rnd.nextDouble() < 0.85 ? 7 : 12);
        LocalDate fix = d.plusDays(days);
        later(fix, () -> {
            ehs.rectify(id, "已整改：" + h[3].replaceAll("[，,].*", "") + " 问题已消除", fix);
            LocalDate check = fix.plusDays(1);
            while (check.getDayOfWeek() != DayOfWeek.TUESDAY) {
                check = check.plusDays(1);
            }
            LocalDate c = check;
            later(c, () -> {
                boolean pass = rnd.nextDouble() > 0.05;
                ehs.verify(id, pass, c);
                if (!pass) {
                    later(c.plusDays(3), () -> {
                        ehs.rectify(id, "复查后重新整改完成", c.plusDays(3));
                        later(c.plusDays(7), () -> ehs.verify(id, true, c.plusDays(7)));
                    });
                }
            });
        });
    }

    // ================= 事故 =================

    private void randomIncidents(LocalDate d) {
        boolean peak = d.getMonthValue() >= 10 || d.getMonthValue() == 1;
        if (rnd.nextDouble() < (peak ? 0.16 : 0.1)) {
            String[][] nm = {{"LOG", "成品仓", "叉车转弯与行人险些相撞"}, {"WS2", "冲压线", "送料时手部接近模具区，光栅触发停机"}, {"WS1", "装配区", "货架顶层纸箱滑落"},
                    {"EQP", "配电房", "检修时未挂牌上锁，险些误合闸"}, {"WS1", "浸漆房", "绝缘漆桶倾倒，少量泄漏"}, {"LOG", "装卸平台", "货车未熄火司机离车"}};
            String[] x = peak && rnd.nextDouble() < 0.5 ? nm[0] : nm[rnd.nextInt(nm.length)];
            incident("NEAR_MISS", x[0], x[1], x[2], null, 0, d.atTime(8 + rnd.nextInt(12), rnd.nextInt(60)), "作业人员安全意识不足 / 现场管理疏漏", "班前会通报，纳入周检重点");
        }
        if (rnd.nextDouble() < 0.035) {
            String[][] fa = {{"WS1", "绕线机", "漆包线划伤手指", "手指划伤"}, {"WS2", "嵌线工位", "治具夹伤手指", "手指轻微挤伤"}, {"LOG", "原材料仓", "搬运纸箱扭伤手腕", "手腕扭伤"}};
            String[] x = fa[rnd.nextInt(fa.length)];
            incident("FIRST_AID", x[0], x[1], x[2], x[3], 0, d.atTime(9 + rnd.nextInt(9), rnd.nextInt(60)), "未按要求佩戴防护手套", "补发防割手套，班组长现场检查");
        }
    }

    private void scheduleStories() {
        later(LocalDate.of(2025, 3, 12), () -> incident("RECORDABLE", "WS1", "浸漆房", "取出浸漆工件时接触烘箱内壁，手背烫伤", "手背二度烫伤（医院处理）", 0,
                LocalDateTime.of(2025, 3, 12, 14, 20), "烘箱未冷却即开门取件，隔热手套破损", "烘箱加装温度联锁（≤60℃ 方可开门），隔热手套按月更换"));
        later(LocalDate.of(2025, 6, 17), () -> {
            incident("LOST_TIME", "WS2", "冲压线 L8", "冲床送料时右手食指被上模压伤", "右手食指骨折", 21, LocalDateTime.of(2025, 6, 17, 10, 45),
                    "操作工为赶产量短接光栅保护，单手送料", "拆除短接并加装光栅短接检测；改双手按钮启动；冲压工重新培训考核");
            for (int i = 0; i < 6; i++) { // 冲床安全专项检查
                LocalDate x = LocalDate.of(2025, 6, 18).plusDays(i);
                String[] h = {"WS2", "机械", "冲压线", new String[]{"冲床光栅与控制回路未联锁", "双手按钮间距不足，可单手操作", "模具区防护网缺失",
                        "冲床急停按钮失效", "送料工具（镊子、吸盘）未配置", "冲床每日点检记录缺失"}[i]};
                hazard("AUDIT", h, i < 2 ? "MAJOR" : "GENERAL", x, ehsStaff());
            }
        });
        later(LocalDate.of(2025, 12, 9), () -> {
            incident("LOST_TIME", "LOG", "成品仓", "叉车倒车时撞到后方拣货员，左脚被压伤", "左脚跖骨骨折", 35, LocalDateTime.of(2025, 12, 9, 16, 10),
                    "旺季出货人车混行，叉车倒车无警示、视线盲区", "划定人车分流黄线与行人通道；叉车加装蓝光警示灯与限速 8km/h；旺季增设指挥员");
            for (int i = 0; i < 4; i++) {
                String[] h = {"LOG", "叉车", "成品仓", new String[]{"人车通道未分离", "叉车无倒车蓝光警示灯", "叉车未限速", "拣货区与叉车作业区交叉"}[i]};
                hazard("AUDIT", h, i == 0 ? "MAJOR" : "GENERAL", LocalDate.of(2025, 12, 10).plusDays(i), ehsStaff());
            }
        });
        later(LocalDate.of(2026, 4, 22), () -> incident("RECORDABLE", "EQP", "伺服总装线", "维修送料机构时被钣金锐边划伤手臂", "前臂划伤缝合 5 针", 0,
                LocalDateTime.of(2026, 4, 22, 11, 5), "维修前未拆除锐边部件防护，未戴防割袖套", "维修作业清单增加锐边防护检查；配发防割袖套"));
        later(LocalDate.of(2025, 11, 24), () -> hazard("AUDIT", new String[]{"EQP", "能源", "空压站",
                "能源分析：空压站非生产日空载电量较 9 月上升约 3 倍，管网存在泄漏（超声波检漏发现 23 处漏点）"}, "GENERAL", LocalDate.of(2025, 11, 24), ehsStaff()));
        later(LocalDate.of(2026, 8, 3), () -> hazard("AUDIT", new String[]{"WS1", "化学品", "危废仓库",
                "HW12 浸漆废物最早一批贮存已近一年（原处置单位换证暂停接收），逾期将违反固废法贮存期限"}, "MAJOR", LocalDate.of(2026, 8, 3), ehsStaff()));
    }

    private void incident(String type, String dept, String location, String desc, String injury, int lostDays, LocalDateTime at, String rootCause, String action) {
        Long emp = "WS2".equals(dept) && location.contains("冲压") ? jdbc.queryForList(
                "SELECT id FROM hr_employee WHERE line_code = 'L8' AND status <> 'LEFT' AND leave_date IS NULL ORDER BY id LIMIT 1", Long.class).stream().findFirst().orElse(null)
                : hr.pickEmployee(deptName(dept));
        long id = ehs.reportIncident(new EhsService.IncidentReq(at, depts.get(dept), location, type, emp, desc, injury, lostDays));
        LocalDate close = at.toLocalDate().plusDays("NEAR_MISS".equals(type) ? 2 + rnd.nextInt(5) : 5 + rnd.nextInt(10));
        later(close, () -> ehs.closeIncident(id, rootCause, action, close));
    }

    // ================= 作业票 =================

    private void permits(LocalDate d) {
        List<Object[]> todo = new ArrayList<>();
        if (rnd.nextDouble() < 0.3) {
            boolean lvl1 = rnd.nextDouble() < 0.1;
            todo.add(new Object[]{"HOT_WORK", lvl1 ? "1" : "2", lvl1 ? "浸漆房附近" : new String[]{"冲压线", "设备维修间", "绕线车间"}[rnd.nextInt(3)],
                    lvl1 ? "排风管道焊接修补" : new String[]{"设备支架焊接", "模具架补焊", "输送线护栏焊接"}[rnd.nextInt(3)]});
        }
        if (d.getDayOfMonth() <= 7 && d.getDayOfWeek() == DayOfWeek.THURSDAY) {
            todo.add(new Object[]{"HEIGHT", "2", "厂房屋面", "中央空调冷却塔检修"});
            todo.add(new Object[]{"ELECTRICAL", "2", "伺服总装线", "新增工位临时用电"});
        }
        if (d.getDayOfMonth() >= 15 && d.getDayOfMonth() <= 21 && d.getDayOfWeek() == DayOfWeek.FRIDAY && List.of(3, 6, 9, 12).contains(d.getMonthValue())) {
            todo.add(new Object[]{"CONFINED", "2", "污水处理站", "调节池清淤"});
        }
        for (Object[] p : todo) {
            Long applicant = hr.pickEmployee("设备动力部"), guardian = hr.pickEmployee("设备动力部");
            if (applicant == null) {
                continue;
            }
            LocalDate work = d.plusDays(1);
            LocalDateTime s = work.atTime(9, 0), e = work.atTime(16, 0);
            long id = ehs.applyPermit(new EhsService.PermitReq((String) p[0], (String) p[1], depts.get("EQP"), (String) p[2], (String) p[3], guardian, s, e),
                    applicant, d.atTime(14, rnd.nextInt(60)));
            closeWhenDone(id, work.plusDays(1), e, 0);
        }
    }

    /** 作业结束后关闭作业票；审批拖延的，按实际批准后的作业时间顺延关闭 */
    private void closeWhenDone(long id, LocalDate day, LocalDateTime end, int tries) {
        later(day, () -> {
            String st = jdbc.queryForObject("SELECT status FROM ehs_permit WHERE id = ?", String.class, id);
            if ("APPROVED".equals(st)) {
                LocalDateTime at = end.toLocalDate().isBefore(day) ? day.atTime(16, 30) : end.plusMinutes(30);
                ehs.closePermit(id, at);
            } else if ("APPROVING".equals(st) && tries < 6) {
                closeWhenDone(id, day.plusDays(1), end, tries + 1);
            }
        });
    }

    // ================= 危废 =================

    private void generateWaste(LocalDate d) {
        double index = jdbc.queryForObject("SELECT COALESCE(SUM(total_qty), 0) FROM mes_line_day WHERE work_date BETWEEN ? AND ?", Double.class,
                Date.valueOf(d.withDayOfMonth(1)), Date.valueOf(d)) / 800_000.0;
        for (Object[] w : WASTES) {
            double qty = ((Double) w[3] + rnd.nextDouble() * ((Double) w[4] - (Double) w[3])) * ("HW12".equals(w[0]) ? Math.max(0.6, Math.min(1.4, index)) : 1);
            ehs.generateWaste((String) w[0], (String) w[1], BigDecimal.valueOf(qty).setScale(3, RoundingMode.HALF_UP), depts.get(w[2]), d);
        }
    }

    private void transferWaste(LocalDate d) {
        for (Object[] w : WASTES) {
            boolean paused = "HW12".equals(w[0]) && d.isAfter(LocalDate.of(2025, 11, 1)) && d.isBefore(LocalDate.of(2026, 7, 1));
            if (paused) {
                continue; // 原处置单位 HW12 经营许可证换证，暂停接收
            }
            transfer(w, d, d.getYear() == 2026 && "HW12".equals(w[0]) ? DISPOSER2 : DISPOSER); // 2026 年 HW12 改由备选处置单位接收
        }
    }

    private void transfer(Object[] w, LocalDate d, String receiver) {
        BigDecimal qty = ehs.transferWaste((String) w[0], receiver, BigDecimal.valueOf((Integer) w[5]), d);
        if (qty.signum() <= 0) {
            return;
        }
        BigDecimal cost = qty.multiply(BigDecimal.valueOf((Integer) w[5])).setScale(2, RoundingMode.HALF_UP);
        Long emp = hr.pickEmployee("安全环保部");
        Expense e = new Expense();
        e.setExpenseDate(d);
        e.setDept("安全环保部");
        e.setCategory("危废处置费");
        e.setAmount(cost.max(BigDecimal.valueOf(1500)));
        e.setEmployee(emp == null ? "安全环保部" : hr.nameOf(emp));
        e.setDescription(w[0] + " " + w[1] + " " + qty.stripTrailingZeros().toPlainString() + " 吨，接收单位：" + receiver);
        finance.submit(e, emp, d.atTime(10, 0));
    }

    // ================= 工具 =================

    private Long ehsStaff() {
        return hr.pickEmployee("安全环保部");
    }

    private long head(String code) {
        return jdbc.queryForObject("SELECT COALESCE(manager_emp_id, (SELECT manager_emp_id FROM org_dept WHERE code = 'GM')) FROM org_dept WHERE code = ?", Long.class, code);
    }

    private String deptName(String code) {
        return jdbc.queryForObject("SELECT name FROM org_dept WHERE code = ?", String.class, code);
    }

    private void later(LocalDate d, Runnable r) {
        tasks.computeIfAbsent(d, k -> new ArrayList<>()).add(r);
    }

    private List<List<Runnable>> drain(LocalDate d) {
        List<List<Runnable>> out = new ArrayList<>();
        while (!tasks.isEmpty() && !tasks.firstKey().isAfter(d)) {
            out.add(tasks.pollFirstEntry().getValue());
        }
        return out;
    }
}
