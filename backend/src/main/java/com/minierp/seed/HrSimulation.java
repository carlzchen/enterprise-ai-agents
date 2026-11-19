package com.minierp.seed;

import com.minierp.common.BizException;
import com.minierp.common.WorkCalendar;
import com.minierp.hr.LeaveService;
import com.minierp.hr.PayrollService;
import com.minierp.org.OrgService;
import com.minierp.workflow.WorkflowService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.Time;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 组织、OA 与 HR 的逐日模拟（由 BusinessSimulator 调用，全部走业务 Service）。
 * 埋入的故事：
 * 1. 春节后一线工人约 12% 未返岗（2025-02、2026-02），当月流失率跳升，随后按产量补招；
 * 2. 伺服线 L5 / 组件线 L7 产量增长 → 持续招工，计件工资随产量上升；
 * 3. 2026-03 计件单价普调 5%（年度调薪），一线人均工资上升；
 * 4. 旺季产线负荷高时加班增加（按当日产线产量 / 产能自动计算加班工时）。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class HrSimulation {
    private static final String[] SURNAMES = {"王", "李", "张", "刘", "陈", "杨", "黄", "赵", "周", "吴", "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
            "梁", "宋", "郑", "谢", "韩", "唐", "冯", "于", "董", "萧", "程", "曹", "袁", "邓", "许", "傅", "沈", "曾", "彭", "吕"};
    private static final String[] GIVEN = {"伟", "芳", "娜", "敏", "静", "丽", "强", "磊", "军", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "秀英", "华", "平",
            "刚", "桂英", "建华", "文", "辉", "红", "鑫", "海燕", "晓东", "志强", "春梅", "玉兰", "国庆", "丹", "亮", "婷", "雪", "宇", "浩", "倩"};

    /** 产线：编码、车间、班组数、初始人数、计件单价（按 2025 年月均产量折算，使人均计件约 5200~6200 元） */
    private static final Object[][] LINES = {
            {"L1", "WS1", 3, 72, "0.88"}, {"L2", "WS1", 3, 36, "2.93"}, {"L6", "WS1", 3, 30, "2.10"},
            {"L3", "WS2", 3, 42, "3.60"}, {"L4", "WS2", 3, 27, "3.80"}, {"L5", "WS2", 3, 30, "28.50"},
            {"L7", "WS2", 2, 24, "9.75"}, {"L8", "WS2", 2, 10, "7.35"}};
    /** 2025 年各产线日均良品（招工目标的基准） */
    private static final Map<String, Double> BASE_DAILY = Map.of("L1", 22_650.0, "L2", 3_400.0, "L6", 3_950.0, "L3", 3_420.0,
            "L4", 2_080.0, "L5", 348.0, "L7", 720.0, "L8", 362.0);
    private static final Map<String, Integer> CAPACITY = Map.of("L1", 9000 * 3, "L2", 2500 * 2, "L3", 1600 * 3, "L4", 1500 * 2,
            "L5", 260 * 2, "L6", 1600 * 3, "L7", 700 * 2, "L8", 1500);

    private final JdbcTemplate jdbc;
    private final OrgService orgs;
    private final WorkflowService workflow;
    private final LeaveService leaves;
    private final PayrollService payroll;

    private Random rnd;
    private final Map<String, Long> deptIds = new HashMap<>();
    private final Map<String, Long> positionIds = new HashMap<>();
    private final Map<String, Long> teamLeaders = new HashMap<>();
    private final Map<String, Long> lineWorkshop = new HashMap<>();
    private final Set<Long> protectedEmps = new HashSet<>();
    private final TreeMap<LocalDate, List<String>> officeReplacements = new TreeMap<>();
    private final Set<LocalDate> springFestivalReturn = Set.of(LocalDate.of(2025, 2, 5), LocalDate.of(2026, 2, 24));
    private Long hrHead;

    // ================= 初始化 =================

    public void init(LocalDate start, Random random) {
        rnd = random;
        createDepts();
        createPositions();
        createOffice(start);
        createWorkers(start);
        createPieceRates(start);
        createNotices(start);
        // 初始员工：入职满 3 个月的直接为正式员工（不产生转正记录）
        jdbc.update("UPDATE hr_employee SET status = 'ACTIVE' WHERE status = 'PROBATION' AND hire_date <= ?", Date.valueOf(start.minusMonths(3)));
        log.info("HR seeded: {} employees", jdbc.queryForObject("SELECT COUNT(*) FROM hr_employee", Integer.class));
    }

    private void createDepts() {
        Object[][] defs = { // code, name, parent, cost_type
                {"GM", "总经办", null, "ADMIN"}, {"FIN", "财务部", "GM", "ADMIN"}, {"HR", "人力资源部", "GM", "ADMIN"},
                {"ADM", "行政部", "GM", "ADMIN"}, {"IT", "信息部", "GM", "ADMIN"}, {"PUR", "采购部", "GM", "ADMIN"},
                {"SAL", "销售部", "GM", "SALES"}, {"EXP", "外贸部", "SAL", "SALES"}, {"RND", "研发中心", "GM", "RND"},
                {"QA", "质量部", "GM", "PRODUCTION"}, {"PRD", "生产部", "GM", "PRODUCTION"},
                {"WS1", "家电电机车间", "PRD", "PRODUCTION"}, {"WS2", "智能驱控车间", "PRD", "PRODUCTION"}, {"LOG", "仓储物流部", "PRD", "PRODUCTION"},
                {"EQP", "设备动力部", "PRD", "PRODUCTION"}, {"SVC", "客户服务部", "SAL", "SALES"}, {"EHS", "安全环保部", "GM", "ADMIN"}};
        int sort = 0;
        for (Object[] d : defs) {
            jdbc.update("INSERT INTO org_dept (code, name, parent_id, cost_type, sort_no) VALUES (?,?,?,?,?)",
                    d[0], d[1], d[2] == null ? null : deptIds.get(d[2]), d[3], sort++);
            deptIds.put((String) d[0], jdbc.queryForObject("SELECT id FROM org_dept WHERE code = ?", Long.class, d[0]));
        }
    }

    private void createPositions() {
        Object[][] defs = {
                {"GM", "总经理", "M4", 1}, {"DIR", "部门总监", "M3", 1}, {"MGR", "部门经理", "M2", 1}, {"SUP", "车间主任", "M2", 1},
                {"ENG_SR", "高级工程师", "P5", 0}, {"ENG", "工程师", "P3", 0}, {"SPEC", "专员", "P2", 0}, {"CLERK", "文员", "P1", 0},
                {"SALES", "销售经理", "P3", 0}, {"ACC", "会计", "P3", 0}, {"QE", "质量工程师", "P3", 0}, {"INSP", "检验员", "O3", 0},
                {"PLAN", "计划员", "P2", 0}, {"WH", "仓管员", "O2", 0}, {"LEAD", "班组长", "O3", 0}, {"OP", "操作工", "O1", 0},
                {"TECH", "设备技术员", "P2", 0}};
        for (Object[] p : defs) {
            jdbc.update("INSERT INTO org_position (code, name, grade, is_manager) VALUES (?,?,?,?)", p[0], p[1], p[2], p[3]);
            positionIds.put((String) p[0], jdbc.queryForObject("SELECT id FROM org_position WHERE code = ?", Long.class, p[0]));
        }
    }

    /** 职员：部门负责人与演示账号绑定（统一身份），其余随机生成；与采购员、销售员、检验员等业务数据里的姓名一致 */
    private void createOffice(LocalDate start) {
        Object[][] heads = { // dept, name, position, salary, username
                {"GM", "林建国", "GM", 48000, "gm"}, {"FIN", "陈思敏", "DIR", 32000, "cfo"}, {"HR", "何雅琴", "MGR", 18000, "hr01"},
                {"ADM", "唐晓雯", "MGR", 13000, null}, {"IT", "梁启航", "MGR", 16000, "admin"}, {"PUR", "周文斌", "MGR", 16000, "buyer01"},
                {"SAL", "王浩然", "DIR", 26000, "sales01"}, {"EXP", "许诗雨", "MGR", 17000, null}, {"RND", "林志远", "DIR", 30000, "eng01"},
                {"QA", "吴志刚", "MGR", 17000, "qa01"}, {"PRD", "孙宏伟", "DIR", 24000, "prod01"}, {"WS1", "罗家明", "SUP", 14000, null},
                {"WS2", "邓建华", "SUP", 15000, null}, {"LOG", "赵国强", "MGR", 12000, "wh01"}, {"EQP", "冯立新", "MGR", 15000, null},
                {"SVC", "马晓峰", "MGR", 15000, null}, {"EHS", "钱永康", "MGR", 14000, null}};
        for (Object[] h : heads) {
            long id = hire(h[1].toString(), (String) h[0], (String) h[2], null, null, null, "MONTHLY", ((Integer) h[3]), randomPast(start, 2012, 2021));
            jdbc.update("UPDATE org_dept SET manager_emp_id = ? WHERE id = ?", id, deptIds.get(h[0]));
            if (h[4] != null) {
                orgs.linkAccount(id, (String) h[4]);
            }
            protectedEmps.add(id);
            if ("HR".equals(h[0])) {
                hrHead = id;
            }
        }
        Object[][] members = { // dept, position, salary, count, fixed names...
                {"FIN", "ACC", 11000, 4, new String[]{"刘婷"}}, {"HR", "SPEC", 9000, 3, new String[]{}}, {"ADM", "CLERK", 6500, 4, new String[]{}},
                {"IT", "ENG", 14000, 2, new String[]{}}, {"PUR", "SPEC", 10000, 4, new String[]{"周敏", "黄涛"}},
                {"SAL", "SALES", 10000, 8, new String[]{"王磊", "李娜", "张伟", "刘洋", "陈静", "杨帆"}}, {"EXP", "SALES", 11000, 3, new String[]{}},
                {"RND", "ENG_SR", 22000, 4, new String[]{"陈家豪"}}, {"RND", "ENG", 15000, 10, new String[]{}},
                {"QA", "QE", 12000, 3, new String[]{}}, {"QA", "INSP", 6800, 6, new String[]{"郑凯"}},
                {"PRD", "PLAN", 9500, 3, new String[]{}}, {"LOG", "WH", 6200, 8, new String[]{}}, {"EQP", "TECH", 9000, 8, new String[]{}}, {"GM", "SPEC", 12000, 2, new String[]{"赵强", "孙丽"}},
                {"SVC", "ENG", 11000, 6, new String[]{}}, {"EHS", "SPEC", 9000, 2, new String[]{}}};
        for (Object[] m : members) {
            String[] fixed = (String[]) m[4];
            for (int i = 0; i < (Integer) m[3]; i++) {
                String name = i < fixed.length ? fixed[i] : randomName();
                long id = hire(name, (String) m[0], (String) m[1], null, null, null, "MONTHLY",
                        (int) Math.round((Integer) m[2] * (0.9 + rnd.nextDouble() * 0.25)), randomPast(start, 2015, 2024));
                if (i < fixed.length) {
                    protectedEmps.add(id);
                }
            }
        }
        orgs.linkAccount(jdbc.queryForObject("SELECT id FROM hr_employee WHERE name = '刘婷'", Long.class), "fin01");
        orgs.linkAccount(jdbc.queryForObject("SELECT id FROM hr_employee WHERE name = '陈家豪'", Long.class), "eng02");
    }

    private void createWorkers(LocalDate start) {
        for (Object[] l : LINES) {
            String line = (String) l[0];
            lineWorkshop.put(line, deptIds.get(l[1]));
            int teams = (Integer) l[2], count = (Integer) l[3];
            for (int t = 0; t < teams; t++) {
                String team = String.valueOf((char) ('A' + t));
                long leader = hire(randomName(), (String) l[1], "LEAD", null, line, team, "PIECE", 3500, randomPast(start, 2014, 2021));
                teamLeaders.put(line + team, leader);
                protectedEmps.add(leader);
            }
            for (int i = 0; i < count - teams; i++) {
                String team = String.valueOf((char) ('A' + i % teams));
                hire(randomName(), (String) l[1], "OP", teamLeaders.get(line + team), line, team, "PIECE", 2360, randomPast(start, 2019, 2024));
            }
        }
    }

    private void createPieceRates(LocalDate start) {
        for (Object[] l : LINES) {
            BigDecimal rate = new BigDecimal((String) l[4]);
            jdbc.update("INSERT INTO hr_piece_rate (line_code, rate, effective_from, remark) VALUES (?,?,?,?)", l[0], rate, Date.valueOf(start.minusMonths(1)), "初始单价");
            jdbc.update("INSERT INTO hr_piece_rate (line_code, rate, effective_from, remark) VALUES (?,?,?,?)", l[0],
                    rate.multiply(new BigDecimal("1.05")).setScale(4, java.math.RoundingMode.HALF_UP), Date.valueOf(LocalDate.of(2026, 3, 1)), "2026 年度调价 +5%");
        }
    }

    private void createNotices(LocalDate start) {
        Object[][] n = {
                {"2024-09-24", "国庆节放假安排", "公告", "10 月 1 日至 7 日放假，10 月 8 日正常上班。各车间做好节前设备点检与 5S。"},
                {"2024-10-15", "《员工考勤管理制度》修订", "制度", "迟到 30 分钟以上按旷工半天处理；请假需提前在 OA 发起，病假事后 2 个工作日内补交证明。"},
                {"2024-12-20", "年度 IATF 16949 监督审核安排", "通知", "外审定于 2025 年 1 月 8~10 日，质量部牵头，各部门准备过程文件与记录。"},
                {"2025-01-20", "春节放假及返岗通知", "公告", "1 月 28 日至 2 月 4 日放假，2 月 5 日返岗。按时返岗的一线员工发放返岗红包 500 元。"},
                {"2025-03-01", "计件工资核算规则说明", "制度", "计件只计良品，班组计件总额按组员当月出勤工时分摊；计件不足保底工资的按出勤补足。"},
                {"2025-06-01", "安全生产月活动", "通知", "6 月为安全生产月，冲压、绕线工序开展隐患排查，发现问题在 OA 通用审批中上报。"},
                {"2025-09-22", "国庆中秋放假安排", "公告", "10 月 1 日至 8 日放假，10 月 9 日正常上班。"},
                {"2026-01-26", "春节放假及返岗通知", "公告", "2 月 15 日至 23 日放假，2 月 24 日返岗。返岗红包 800 元，介绍新员工入职满 3 个月奖励 1000 元。"},
                {"2026-02-27", "2026 年度调薪通知", "通知", "自 3 月 1 日起各产线计件单价上调 5%；职员调薪按绩效结果执行。"},
                {"2026-04-10", "伺服产线扩产招聘", "通知", "伺服线与组件线订单增长，持续招聘操作工，内部推荐有奖。"},
                {"2026-09-01", "质量月活动", "通知", "9 月为质量月，围绕磁钢来料与编码器装配开展专项改善。"},
                {"2026-09-20", "国庆节放假安排", "公告", "10 月 1 日至 8 日放假，10 月 9 日正常上班。"}};
        for (Object[] x : n) {
            jdbc.update("INSERT INTO oa_notice (title, content, category, publisher, publish_date, pinned) VALUES (?,?,?,?,?,?)",
                    x[1], x[3], x[2], "何雅琴", Date.valueOf((String) x[0]), 0);
        }
    }

    // ================= 每日 =================

    /** 在生产报工之后调用（加班按当日产线负荷计算） */
    public void onDay(LocalDate d) {
        if (springFestivalReturn.contains(d)) {
            springFestivalAttrition(d);
        }
        orgs.processDepartures(d);
        if (d.getDayOfWeek() == java.time.DayOfWeek.MONDAY) {
            orgs.confirmProbation(d);
            hireForLines(d);
        }
        for (List<String> depts : drain(officeReplacements, d)) {
            depts.forEach(dept -> hire(randomName(), dept, officePosition(dept), null, null, null, "MONTHLY", 9000 + rnd.nextInt(5000), d));
        }
        attendance(d);
        requestLeaves(d);
        resignations(d);
        otherRequests(d);
        processApprovals(d);
        YearMonth ym = YearMonth.from(d);
        if (d.equals(WorkCalendar.lastWorkday(ym))) {
            runPayroll(ym, d);
        }
        LocalDate payDay = firstWorkdayFrom(ym.atDay(10));
        if (d.equals(payDay)) {
            jdbc.queryForList("SELECT id FROM hr_payroll WHERE period = ? AND status = 'APPROVED'", Long.class, ym.minusMonths(1).toString())
                    .forEach(id -> payroll.pay(id, d));
        }
    }

    /** 当月报销的报销人：该部门在职职员 */
    public Long pickEmployee(String deptName) {
        List<Long> ids = jdbc.queryForList("""
                SELECT e.id FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id
                WHERE (d.name = ? OR d.parent_id = (SELECT id FROM org_dept WHERE name = ?)) AND e.status <> 'LEFT' AND e.wage_type = 'MONTHLY'""",
                Long.class, deptName, deptName);
        return ids.isEmpty() ? null : ids.get(rnd.nextInt(ids.size()));
    }

    public String nameOf(long empId) {
        return jdbc.queryForObject("SELECT name FROM hr_employee WHERE id = ?", String.class, empId);
    }

    private void attendance(LocalDate d) {
        Map<String, Double> util = new HashMap<>();
        jdbc.query("""
                SELECT w.line_code, SUM(r.good_qty + r.scrap_qty) FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id
                WHERE r.report_date = ? GROUP BY w.line_code""", rs -> {
            util.put(rs.getString(1), rs.getDouble(2) / CAPACITY.getOrDefault(rs.getString(1), 1));
        }, Date.valueOf(d));
        Map<Long, String> onLeave = new HashMap<>();
        jdbc.query("SELECT employee_id, leave_type FROM hr_leave WHERE status = 'APPROVED' AND ? BETWEEN start_date AND end_date", rs -> {
            onLeave.put(rs.getLong(1), rs.getString(2));
        }, Date.valueOf(d));
        List<Object[]> rows = new ArrayList<>();
        jdbc.query("""
                SELECT id, line_code, wage_type FROM hr_employee WHERE hire_date <= ? AND status <> 'LEFT' AND (leave_date IS NULL OR leave_date >= ?)""", rs -> {
            long id = rs.getLong(1);
            String line = rs.getString(2);
            boolean worker = "PIECE".equals(rs.getString(3));
            if (onLeave.containsKey(id)) {
                rows.add(new Object[]{id, Date.valueOf(d), "LEAVE", null, null, 0, 0, onLeave.get(id)});
                return;
            }
            double r = rnd.nextDouble();
            if (r < (worker ? 0.005 : 0.002)) {
                rows.add(new Object[]{id, Date.valueOf(d), "ABSENT", null, null, 0, 0, null});
                return;
            }
            boolean late = r < 0.025;
            double ot = 0;
            if (worker && line != null) {
                double u = util.getOrDefault(line, 0.0);
                ot = u >= 0.9 ? 3 : u >= 0.75 ? 2 : u >= 0.55 ? 1 : 0;
                ot = Math.max(0, ot + (rnd.nextDouble() < 0.2 ? -1 : 0));
            } else if (rnd.nextDouble() < 0.06) {
                ot = 1 + rnd.nextInt(2);
            }
            LocalTime in = late ? LocalTime.of(8, 10 + rnd.nextInt(30)) : LocalTime.of(7, 45 + rnd.nextInt(14));
            LocalTime out = LocalTime.of(17, 30 + rnd.nextInt(15)).plusMinutes((long) (ot * 60) + (ot > 0 ? 30 : 0));
            rows.add(new Object[]{id, Date.valueOf(d), late ? "LATE" : "NORMAL", Time.valueOf(in), Time.valueOf(out), late ? 7.5 : 8, ot, null});
        }, Date.valueOf(d), Date.valueOf(d));
        jdbc.batchUpdate("""
                INSERT INTO hr_attendance (employee_id, work_date, status, clock_in, clock_out, work_hours, overtime_hours, leave_type)
                VALUES (?,?,?,?,?,?,?,?)""", rows);
    }

    private void requestLeaves(LocalDate d) {
        List<Long> active = jdbc.queryForList("SELECT id FROM hr_employee WHERE status <> 'LEFT' AND leave_date IS NULL", Long.class);
        for (Long id : active) {
            if (rnd.nextDouble() >= 0.0035) {
                continue;
            }
            double r = rnd.nextDouble();
            String type = r < 0.4 ? "ANNUAL" : r < 0.65 ? "SICK" : "PERSONAL";
            LocalDate s = "SICK".equals(type) ? d.plusDays(1) : d.plusDays(2 + rnd.nextInt(9));
            while (!WorkCalendar.isWorkday(s)) {
                s = s.plusDays(1);
            }
            LocalDate e = s.plusDays("ANNUAL".equals(type) ? rnd.nextInt(5) : rnd.nextInt(3));
            String reason = switch (type) {
                case "ANNUAL" -> "年假休息";
                case "SICK" -> "身体不适就医";
                default -> new String[]{"家中有事", "办理个人证件", "孩子入学"}[rnd.nextInt(3)];
            };
            try {
                leaves.applyLeave(id, type, s, e, reason, d.atTime(9, rnd.nextInt(60)));
            } catch (BizException ignored) {
                // 与已有请假重叠
            }
        }
    }

    private void resignations(LocalDate d) {
        List<Map<String, Object>> active = jdbc.queryForList("SELECT id, wage_type, dept_id FROM hr_employee WHERE status <> 'LEFT' AND leave_date IS NULL");
        for (Map<String, Object> e : active) {
            long id = ((Number) e.get("id")).longValue();
            if (protectedEmps.contains(id)) {
                continue;
            }
            boolean worker = "PIECE".equals(e.get("wage_type"));
            if (rnd.nextDouble() >= (worker ? 0.0011 : 0.0004)) {
                continue;
            }
            if (worker && rnd.nextDouble() < 0.5) {
                orgs.leaveNow(id, d, "自离（未办理离职手续）");
                continue;
            }
            LocalDate last = d.plusDays(worker ? 7 : 30);
            String reason = worker ? new String[]{"回老家发展", "工资期望不符", "家庭原因"}[rnd.nextInt(3)]
                    : new String[]{"个人职业发展", "薪资原因", "家庭原因", "异地发展"}[rnd.nextInt(4)];
            try {
                leaves.applyResign(id, last, reason, d.atTime(10, rnd.nextInt(60)));
                if (!worker) {
                    String dept = jdbc.queryForObject("SELECT code FROM org_dept WHERE id = ?", String.class, e.get("dept_id"));
                    officeReplacements.computeIfAbsent(last.plusDays(10 + rnd.nextInt(30)), k -> new ArrayList<>()).add(dept);
                }
            } catch (BizException ignored) {
                // 已有审批中的离职申请
            }
        }
    }

    /** 出差、用印、通用审批：职员日常流程 */
    private void otherRequests(LocalDate d) {
        Object[][] kinds = {{"TRAVEL", "SAL", 0.35}, {"TRAVEL", "PUR", 0.08}, {"TRAVEL", "QA", 0.08}, {"SEAL", "SAL", 0.2}, {"SEAL", "PUR", 0.15},
                {"GENERAL", "PRD", 0.12}, {"GENERAL", "RND", 0.1}, {"GENERAL", "QA", 0.08}};
        for (Object[] k : kinds) {
            if (rnd.nextDouble() >= (Double) k[2]) {
                continue;
            }
            Long emp = pickEmployee(jdbc.queryForObject("SELECT name FROM org_dept WHERE code = ?", String.class, k[1]));
            if (emp == null) {
                continue;
            }
            String name = nameOf(emp);
            Map<String, Object> form;
            String title;
            BigDecimal amount = null, days = null;
            switch ((String) k[0]) {
                case "TRAVEL" -> {
                    String city = new String[]{"苏州", "宁波", "佛山", "东莞", "武汉", "天津", "首尔", "斯图加特"}[rnd.nextInt(8)];
                    int n = 1 + rnd.nextInt(5);
                    LocalDate s = d.plusDays(2 + rnd.nextInt(7));
                    amount = BigDecimal.valueOf(1500L * n + rnd.nextInt(3000));
                    days = BigDecimal.valueOf(n);
                    form = Map.of("destination", city, "startDate", s.toString(), "endDate", s.plusDays(n - 1).toString(), "budget", amount, "reason", "客户拜访 / 供应商审核");
                    title = name + " 出差 " + city + " " + n + " 天";
                }
                case "SEAL" -> {
                    String type = rnd.nextDouble() < 0.6 ? "CONTRACT" : "OFFICIAL";
                    String doc = "CONTRACT".equals(type) ? "年度供货框架合同" : "资质证明文件";
                    form = Map.of("sealType", type, "document", doc, "copies", 2, "reason", "客户 / 供应商要求盖章");
                    title = name + " 用印：" + doc;
                }
                default -> {
                    String subject = new String[]{"工装夹具采购", "车间安全隐患整改", "样品寄送", "培训费用", "软件许可续费"}[rnd.nextInt(5)];
                    form = Map.of("subject", subject, "detail", subject + "，详见附件");
                    title = name + " 的通用审批：" + subject;
                }
            }
            workflow.start(new WorkflowService.StartReq((String) k[0], title, form, null, null, amount, days), emp, d.atTime(9, rnd.nextInt(60)));
        }
    }

    /** 审批人办理待办：前一天及更早的待办 80% 当天处理，当天的 30%；少量驳回 */
    public void processApprovals(LocalDate d) {
        List<Map<String, Object>> tasks = jdbc.queryForList("""
                SELECT t.id, t.assignee_emp_id, t.created_at, i.def_code, i.amount FROM wf_task t JOIN wf_instance i ON i.id = t.instance_id
                WHERE t.status = 'PENDING' AND i.def_code <> 'PAYROLL' AND t.created_at < ? ORDER BY t.id""", Date.valueOf(d.plusDays(1)));
        for (Map<String, Object> t : tasks) {
            Object c = t.get("created_at");
            LocalDateTime created = c instanceof java.sql.Timestamp ts ? ts.toLocalDateTime() : (LocalDateTime) c;
            boolean today = created.toLocalDate().equals(d);
            if (rnd.nextDouble() >= (today ? 0.3 : 0.8)) {
                continue;
            }
            LocalDateTime at = today ? d.atTime(15, rnd.nextInt(60)) : d.atTime(10, rnd.nextInt(60));
            if (at.isBefore(created)) {
                at = created.plusMinutes(30);
            }
            long taskId = ((Number) t.get("id")).longValue(), who = ((Number) t.get("assignee_emp_id")).longValue();
            double rejectP = switch ((String) t.get("def_code")) {
                case "EXPENSE" -> 0.025;
                case "LEAVE" -> 0.03;
                case "RESIGN" -> 0.0;
                default -> 0.05;
            };
            if (rnd.nextDouble() < rejectP) {
                workflow.reject(taskId, who, new String[]{"请补充说明或附件", "预算不足，下月再议", "时间与生产安排冲突"}[rnd.nextInt(3)], at);
            } else {
                workflow.approve(taskId, who, rnd.nextDouble() < 0.3 ? "同意" : null, at);
            }
        }
    }

    private void runPayroll(YearMonth ym, LocalDate d) {
        long id = payroll.calculate(ym, d);
        long wf = payroll.submit(id, hrHead, d.atTime(15, 0));
        LocalDateTime at = d.atTime(15, 30);
        for (int i = 0; i < 5; i++) {
            List<Map<String, Object>> pending = jdbc.queryForList("SELECT id, assignee_emp_id FROM wf_task WHERE instance_id = ? AND status = 'PENDING'", wf);
            if (pending.isEmpty()) {
                break;
            }
            at = at.plusMinutes(20);
            for (Map<String, Object> t : pending) {
                workflow.approve(((Number) t.get("id")).longValue(), ((Number) t.get("assignee_emp_id")).longValue(), "同意发放", at);
            }
        }
    }

    // ================= 招聘与流失 =================

    private void springFestivalAttrition(LocalDate d) {
        List<Long> workers = jdbc.queryForList("SELECT id FROM hr_employee WHERE status <> 'LEFT' AND leave_date IS NULL AND wage_type = 'PIECE'", Long.class);
        int n = 0;
        for (Long id : workers) {
            if (!protectedEmps.contains(id) && rnd.nextDouble() < 0.12) {
                orgs.leaveNow(id, d.minusDays(1), "春节后未返岗");
                n++;
            }
        }
        log.info("  {} 春节后未返岗 {} 人", d, n);
    }

    /**
     * 每周按近 56 天产量补招：目标人数 = 初始人数 × (近期日均产量 / 2025 年日均)，限制在 0.8~1.6 倍，每周每线最多招 5 人。
     * 用 56 天窗口平滑季节高峰，避免"旺季快速招人、淡季只能靠自然流失"导致的人员棘轮上升。
     */
    private void hireForLines(LocalDate d) {
        Map<String, Double> recent = new HashMap<>();
        jdbc.query("""
                SELECT w.line_code, SUM(r.good_qty) / ? FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id
                WHERE r.report_date > ? AND r.report_date <= ? GROUP BY w.line_code""", rs -> {
            recent.put(rs.getString(1), rs.getDouble(2));
        }, Math.max(1, WorkCalendar.workdays(d.minusDays(55), d)), Date.valueOf(d.minusDays(56)), Date.valueOf(d));
        for (Object[] l : LINES) {
            String line = (String) l[0];
            double ratio = Math.max(0.8, Math.min(1.6, recent.getOrDefault(line, BASE_DAILY.get(line)) / BASE_DAILY.get(line)));
            int target = (int) Math.round((Integer) l[3] * ratio);
            Map<String, Integer> teamSize = new TreeMap<>();
            for (int t = 0; t < (Integer) l[2]; t++) {
                teamSize.put(String.valueOf((char) ('A' + t)), 0);
            }
            jdbc.query("SELECT team, COUNT(*) FROM hr_employee WHERE line_code = ? AND status <> 'LEFT' AND leave_date IS NULL GROUP BY team", rs -> {
                teamSize.put(rs.getString(1), rs.getInt(2));
            }, line);
            int active = teamSize.values().stream().mapToInt(Integer::intValue).sum();
            int toHire = Math.min(5, target - active);
            for (int i = 0; i < toHire; i++) {
                String team = teamSize.entrySet().stream().min(Map.Entry.comparingByValue()).orElseThrow().getKey();
                teamSize.merge(team, 1, Integer::sum);
                Long leader = teamLeaders.get(line + team);
                hire(randomName(), (String) l[1], "OP", leader, line, team, "PIECE", 2360, d, rnd.nextDouble() < 0.35 ? "DISPATCH" : "FULL_TIME");
            }
        }
    }

    // ================= 工具 =================

    private long hire(String name, String deptCode, String pos, Long manager, String line, String team, String wage, int salary, LocalDate hireDate) {
        return hire(name, deptCode, pos, manager, line, team, wage, salary, hireDate, "FULL_TIME");
    }

    private long hire(String name, String deptCode, String pos, Long manager, String line, String team, String wage, int salary, LocalDate hireDate,
                      String type) {
        BigDecimal base = BigDecimal.valueOf(salary);
        // 一线员工社保按最低基数，职员按工资
        BigDecimal socialBase = "PIECE".equals(wage) ? BigDecimal.valueOf(2360) : base;
        return orgs.hire(new OrgService.HireReq(name, rnd.nextDouble() < ("PIECE".equals(wage) ? 0.45 : 0.4) ? "F" : "M",
                LocalDate.of(1970 + rnd.nextInt(35), 1 + rnd.nextInt(12), 1 + rnd.nextInt(28)), deptIds.get(deptCode), positionIds.get(pos), manager,
                line, team, type, wage, base, socialBase, hireDate));
    }

    private String officePosition(String dept) {
        return switch (dept) {
            case "SAL", "EXP" -> "SALES";
            case "RND" -> "ENG";
            case "QA" -> "QE";
            case "FIN" -> "ACC";
            case "LOG" -> "WH";
            case "PRD" -> "PLAN";
            case "EQP" -> "TECH";
            default -> "SPEC";
        };
    }

    private String randomName() {
        return SURNAMES[rnd.nextInt(SURNAMES.length)] + GIVEN[rnd.nextInt(GIVEN.length)];
    }

    private LocalDate randomPast(LocalDate start, int fromYear, int toYear) {
        LocalDate d = LocalDate.of(fromYear + rnd.nextInt(toYear - fromYear + 1), 1 + rnd.nextInt(12), 1 + rnd.nextInt(28));
        return d.isBefore(start) ? d : start.minusMonths(4);
    }

    private static LocalDate firstWorkdayFrom(LocalDate d) {
        while (!WorkCalendar.isWorkday(d)) {
            d = d.plusDays(1);
        }
        return d;
    }

    private static <T> List<T> drain(TreeMap<LocalDate, T> map, LocalDate d) {
        List<T> out = new ArrayList<>();
        while (!map.isEmpty() && !map.firstKey().isAfter(d)) {
            out.add(map.pollFirstEntry().getValue());
        }
        return out;
    }
}
