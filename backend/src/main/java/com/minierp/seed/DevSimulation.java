package com.minierp.seed;

import com.minierp.common.BizException;
import com.minierp.common.WorkCalendar;
import com.minierp.kb.KbService;
import com.minierp.pm.PmService;
import com.minierp.qms.AqlSampling;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 研发与知识库模拟：
 * - NPI 项目启动即建研发项目（SCRUM，两周迭代），按 NPI 当前阶段生成需求 / 任务 / 缺陷；NPI 暂停或结项后不再开迭代并归档；
 * - 伺服、无刷平台维护项目（KANBAN）接收客诉自动转入的缺陷，测试与工装项目承接零散任务；
 * - 研发工程师按考勤出勤登记工时，工时够了提交评审，评审 85% 一次通过；
 * - 知识库初始化制度 / 作业指导书 / 抽样规则 / 选型计算等页面，每月沉淀设备故障案例，工程师不定期修订作业指导书（产生版本历史）。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class DevSimulation {
    private static final Map<String, String[]> STAGE_WORK = Map.of(
            "CONCEPT", new String[]{"市场需求调研与客户访谈", "竞品拆解分析", "技术路线评估", "产品规格书初稿", "目标成本测算"},
            "DESIGN", new String[]{"电磁方案仿真", "结构设计与公差分析", "驱动电路原理图", "BOM 初版", "DFMEA", "热仿真", "编码器选型", "减速器匹配计算"},
            "PROTOTYPE", new String[]{"手板样机装配", "样机性能测试", "温升试验", "振动噪声测试", "可靠性寿命试验", "设计评审问题整改"},
            "PILOT", new String[]{"试产工艺文件编制", "工装夹具验证", "PFMEA 更新", "试产问题关闭", "客户送样与承认书", "首件检验标准"},
            "MP", new String[]{"量产移交", "成本优化方案", "量产爬坡问题跟踪"});
    private static final String[] BUGS = {"样机温升超标", "编码器信号受干扰", "齿隙超出设计值", "低速运行抖动", "驱动板过流保护误触发", "装配干涉", "噪声超出目标 3dB"};

    private final JdbcTemplate jdbc;
    private final PmService pm;
    private final KbService kb;

    private Random rnd;
    private final Map<Long, Long> pmOfPlm = new HashMap<>();
    private final Map<Long, Double> effort = new HashMap<>(); // issueId -> 需要的工时
    private final Map<Long, Long> teamOf = new HashMap<>();    // 工程师 -> 主项目
    private long sv, bl, te;
    private Long svLead;

    // ================= 初始化 =================

    public void init(LocalDate start, Random random) {
        rnd = random;
        LocalDateTime at = start.minusDays(3).atTime(10, 0);
        createSpaces();
        List<Long> engineers = engineers();
        svLead = jdbc.queryForList("SELECT u.employee_id FROM sys_user u WHERE u.username = 'eng02'", Long.class).stream().findFirst().orElse(engineers.get(0));
        sv = pm.createProject("SV", "伺服平台维护", null, svLead, "KANBAN", "SERVO", start);
        bl = pm.createProject("BL", "无刷平台维护", null, engineers.get(Math.min(2, engineers.size() - 1)), "KANBAN", "BLDC", start);
        te = pm.createProject("TE", "测试与工装", null, engineers.get(Math.min(3, engineers.size() - 1)), "KANBAN", null, start);
        seedPages(at);
        log.info("Dev seeded: {} engineers, {} KB pages", engineers.size(), jdbc.queryForObject("SELECT COUNT(*) FROM kb_page", Integer.class));
    }

    private void createSpaces() {
        Object[][] s = {{"POL", "制度流程", "公司制度、审批权限、考勤与薪酬规则", "何雅琴"}, {"PROC", "工艺技术", "作业指导书、工艺参数、工装", "罗家明"},
                {"QUAL", "质量经验", "检验规则、SPC、8D 经验教训", "吴志刚"}, {"EQP", "设备维修", "保养标准、故障案例", "冯立新"},
                {"RND", "研发设计", "设计规范、选型计算、设计变更", "林志远"}};
        for (Object[] x : s) {
            jdbc.update("INSERT INTO kb_space (space_key, name, description, owner) VALUES (?,?,?,?)", x);
        }
    }

    private void seedPages(LocalDateTime at) {
        kb.save(null, "POL", null, "考勤管理制度", """
                # 考勤管理制度

                - 标准工时：08:00~17:30，午休 1 小时；车间按班次排班。
                - 迟到 30 分钟以内记迟到；超过 30 分钟按旷工半天处理。
                - 请假须提前在 OA 发起：1~2 天直属上级审批；3 天及以上加部门负责人；5 天及以上或病假由人力资源备案。
                - 病假须在 2 个工作日内补交医院证明。
                - 加班由班组长根据产线负荷安排，计入考勤加班工时。
                """, "考勤,制度", true, null, "创建", "MANUAL", null, at);
        kb.save(null, "POL", null, "计件工资核算规则", """
                # 计件工资核算规则

                1. 计件只计良品，报废不计；单价按报工日期取产线有效单价（"组织人事 → 计件单价"）。
                2. 班组当月计件总额 = Σ 良品数 × 单价，按组员出勤工时（含加班）占比分摊。
                3. 计件工资低于保底工资（按出勤天数折算）的，按保底补足。
                4. 加班产量已计入计件，另按保底工资折算的小时工资支付 0.5 倍加班溢价。
                5. 个税按累计预扣法代扣；社保公积金按缴费基数上下限计算。
                """, "薪酬,计件", true, null, "创建", "MANUAL", null, at);
        StringBuilder wf = new StringBuilder("# 审批权限与流程\n\n本页由流程定义自动生成，流程定义修改后以系统为准。\n");
        jdbc.queryForList("SELECT code, name, definition FROM wf_definition WHERE version = 1 ORDER BY id").forEach(d ->
                wf.append("\n## ").append(d.get("name")).append("（").append(d.get("code")).append("）\n\n```json\n").append(d.get("definition")).append("\n```\n"));
        wf.append("\n规则：申请人本人自动跳过；同一审批人在前序节点已审批的，后续节点自动通过；驳回必须填写理由；无人同意前申请人可撤回。\n");
        kb.save(null, "POL", null, "审批权限与流程", wf.toString(), "OA,审批", true, null, "创建", "MANUAL", null, at);

        long sopFolder = kb.folder("PROC", "作业指导书（SOP）", at);
        Map<String, List<Map<String, Object>>> routes = new LinkedHashMap<>();
        jdbc.queryForList("SELECT * FROM mes_routing ORDER BY item_key, op_seq").forEach(r -> routes.computeIfAbsent((String) r.get("item_key"), k -> new ArrayList<>()).add(r));
        routes.forEach((key, ops) -> {
            StringBuilder b = new StringBuilder("# " + sopTitle(key) + " 作业指导书\n\n产线：" + ops.get(0).get("line_code") + "\n\n| 工序 | 名称 | 标准工时(秒) | 关键工序 | 检验 |\n|---|---|---|---|---|\n");
            double total = 0;
            for (Map<String, Object> o : ops) {
                b.append("| ").append(o.get("op_code")).append(" | ").append(o.get("op_name")).append(" | ").append(o.get("std_seconds")).append(" | ")
                        .append(Boolean.TRUE.equals(o.get("is_key")) || o.get("is_key") instanceof Number n && n.intValue() == 1 ? "是" : "").append(" | ")
                        .append(o.get("inspect_type") == null ? "" : "FIRST".equals(o.get("inspect_type")) ? "首检" : "巡检").append(" |\n");
                total += ((Number) o.get("std_seconds")).doubleValue();
            }
            b.append("\n合计标准工时 ").append(Math.round(total)).append(" 秒/台。\n\n## 注意事项\n- 关键工序须由持证员工操作，首件经 IPQC 确认后方可批量生产。\n- 设备异常立即拉设备安灯，禁止带病运行。\n");
            kb.save(null, "PROC", sopFolder, sopTitle(key) + " 作业指导书", b.toString(), "SOP," + ops.get(0).get("line_code"), true, null, "由工艺路线生成",
                    "ROUTING", key, at);
        });

        StringBuilder aql = new StringBuilder("""
                # 抽样检验规则（GB/T 2828.1）

                一般检验水平 II，正常检验一次抽样。伺服、无刷 AQL 0.65，其余 1.0。遇箭头时改用箭头指向的方案，样本量随之改变；样本量 ≥ 批量时全检。

                | 批量 | 字码 | AQL 0.65 样本 / Ac / Re | AQL 1.0 样本 / Ac / Re |
                |---|---|---|---|
                """);
        for (int lot : new int[]{50, 150, 500, 1200, 3200, 10000}) {
            AqlSampling.Plan a = AqlSampling.plan(lot, 0.65), b = AqlSampling.plan(lot, 1.0);
            aql.append("| ").append(lot).append(" | ").append(a.codeLetter()).append(" | ").append(a.sampleSize()).append(" / ").append(a.ac()).append(" / ").append(a.re())
                    .append(" | ").append(b.sampleSize()).append(" / ").append(b.ac()).append(" / ").append(b.re()).append(" |\n");
        }
        kb.save(null, "QUAL", null, "抽样检验规则（OQC / IQC）", aql.toString(), "AQL,检验", true, null, "创建", "MANUAL", null, at);
        kb.save(null, "QUAL", null, "SPC 判异规则与过程能力要求", """
                # SPC 判异规则与过程能力要求

                - 子组：每天抽前 5 台出厂测试值；控制限 X̄̄ ± A2·R̄。
                - 判异：① 1 点超出控制限；② 连续 9 点在中心线同侧；③ 连续 6 点持续上升或下降。出现判异须在当班分析原因。
                - 过程能力：关键特性 Cpk ≥ 1.33；1.0 ≤ Cpk < 1.33 需制定改善计划；Cpk < 1.0 须 100% 检验并启动 8D。
                - 伺服关键特性：反电势常数（±5%）、编码器零位偏差（±0.5°）、空载电流（≤ 标称 1.15 倍）、绝缘电阻（≥ 100MΩ）、振动（≤ 1.8mm/s）。
                """, "SPC,Cpk", true, null, "创建", "MANUAL", null, at);
        kb.folder("QUAL", "8D 经验教训", at);
        kb.save(null, "EQP", null, "设备保养标准", """
                # 设备保养标准

                | 设备类别 | 周期 | 内容 |
                |---|---|---|
                | 绕线机 | 双月 | 清洁导轨、检查绕线嘴与张力、校正计数 |
                | 冲床 | 月度（2025-10 起高速冲床改为两周） | 检查模具刃口、润滑、离合器与刹车、送料精度 |
                | 测试台 | 双月 | 检查夹具探针、标准件比对、通讯线缆 |
                | 充磁机 | 月度 | 检查储能电容、冷却水路 |
                | 空压机 | 月度 | 更换滤芯、检查油位与排污（夏季加密） |

                关键设备（A 类）另有年度大修：全面拆检、精度校验、易损件更换。
                """, "保养,设备", true, null, "创建", "MANUAL", null, at);
        kb.folder("EQP", "设备故障案例", at);
        kb.save(null, "RND", null, "伺服电机选型计算", """
                # 伺服电机选型计算

                1. 负载转矩：T_L = F·r / η（直线负载），或按工艺实测。
                2. 加速转矩：T_a = (J_M + J_L) · ω / t_a，ω = 2π·n / 60。
                3. 负载惯量比：J_L / J_M 一般 ≤ 10，高响应场合 ≤ 5；超过时考虑减速机，J_L' = J_L / i²。
                4. 有效转矩：T_rms = √(Σ T_i² t_i / Σ t_i) ≤ 额定转矩 × 0.8。
                5. 额定转矩 T_N = 9550 × P(kW) / n(rpm)，例：750W、3000rpm → 2.39 N·m。
                6. 编码器：定位精度要求高选 17 位绝对值编码器，其余可选 2500 线增量编码器。
                """, "伺服,选型", true, null, "创建", "MANUAL", null, at);
        kb.save(null, "RND", null, "研发项目管理规范", """
                # 研发项目管理规范

                - NPI 项目采用两周迭代（SCRUM），平台维护采用看板（KANBAN）。
                - 状态：待办 → 进行中 → 评审中 → 完成；需求和缺陷必须经过评审，任务可直接完成。
                - 完成定义（DoD）：设计文档更新、评审通过、相关 BOM / ECN 已发起、测试记录归档。
                - 工时每天登记到问题上，研发人工成本按项目工时归集，作为研发费用加计扣除的辅助账依据。
                - 质量客诉（中、高严重度）自动转入对应平台项目的缺陷，客诉 8D 结案时缺陷关闭。
                """, "研发,规范", true, null, "创建", "MANUAL", null, at);
        kb.folder("RND", "设计变更（ECN）", at);
    }

    private static String sopTitle(String key) {
        return switch (key) {
            case "SERIES:SHADED_POLE" -> "罩极电机";
            case "SERIES:CROSSFLOW" -> "贯流风机";
            case "SERIES:BLDC" -> "无刷直流电机";
            case "SERIES:STEPPER" -> "步进电机";
            case "SERIES:SERVO" -> "交流伺服电机";
            case "SERIES:GEAR" -> "齿轮减速电机";
            case "SEMI:SA-ST" -> "伺服定子组件";
            case "SEMI:SA-RT" -> "伺服转子组件";
            case "SEMI:SA-RC" -> "伺服转子铁芯";
            default -> key;
        };
    }

    // ================= 每日 =================

    /** 在考勤生成之后调用（按当天出勤安排工程师工作） */
    public void onDay(LocalDate d) {
        syncNpiProjects(d);
        if (d.getDayOfWeek() == java.time.DayOfWeek.MONDAY) {
            assignTeams(); // NPI 暂停 / 结项后重新分配工程师
        }
        runSprints(d);
        work(d);
        if (d.getDayOfWeek() == java.time.DayOfWeek.MONDAY && rnd.nextDouble() < 0.5) {
            maintenanceTask(d);
        }
        if (d.getDayOfWeek() == java.time.DayOfWeek.WEDNESDAY && rnd.nextDouble() < 0.25) {
            reviseSop(d);
        }
        if (d.getDayOfMonth() <= 7 && d.getDayOfWeek() == java.time.DayOfWeek.TUESDAY) {
            YearMonth prev = YearMonth.from(d).minusMonths(1);
            kb.publishEquipmentCases(prev.atDay(1), prev.atEndOfMonth(), d.atTime(9, 30));
        }
    }

    private void syncNpiProjects(LocalDate d) {
        for (Map<String, Object> p : jdbc.queryForList("SELECT id, name, stage, status FROM plm_project WHERE start_date <= ?", Date.valueOf(d))) {
            long plmId = ((Number) p.get("id")).longValue();
            if (!pmOfPlm.containsKey(plmId)) {
                String name = (String) p.get("name");
                String key = name.contains("关节") ? "JM" : name.contains("灵巧") ? "DH" : name.contains("步进") ? "BJ" : name.contains("变频") ? "VF"
                        : name.contains("铝线") ? "LX" : "NP" + plmId;
                List<Long> eng = engineers();
                long lead = eng.get(rnd.nextInt(Math.min(3, eng.size())));
                long id = pm.createProject(key, name, plmId, lead, "SCRUM", null, d);
                pmOfPlm.put(plmId, id);
                for (String epic : new String[]{"概念与规格", "方案设计", "样机验证", "试产导入", "量产移交"}) {
                    pm.createIssue(new PmService.IssueReq(id, "EPIC", epic, null, "HIGH", lead, null, null, null, null, null, null, null), lead, d.atTime(9, 0));
                }
                assignTeams();
            }
            String status = (String) p.get("status");
            if (List.of("CLOSED", "KILLED").contains(status)) {
                jdbc.update("UPDATE pm_project SET status = 'ARCHIVED' WHERE id = ? AND status = 'ACTIVE'", pmOfPlm.get(plmId));
            }
        }
    }

    /** 工程师分组：进行中的 NPI 项目平均分配，平台负责人留在平台 */
    private void assignTeams() {
        List<Long> active = jdbc.queryForList("""
                SELECT p.id FROM pm_project p JOIN plm_project np ON np.id = p.plm_project_id
                WHERE p.board_type = 'SCRUM' AND p.status = 'ACTIVE' AND np.status = 'ACTIVE' ORDER BY p.id""", Long.class);
        List<Long> eng = engineers();
        teamOf.clear();
        for (int i = 0; i < eng.size(); i++) {
            teamOf.put(eng.get(i), active.isEmpty() ? te : active.get(i % active.size()));
        }
    }

    private void runSprints(LocalDate d) {
        for (Map<String, Object> p : jdbc.queryForList("""
                SELECT p.id, p.lead_emp_id, np.stage, np.status AS npi_status FROM pm_project p JOIN plm_project np ON np.id = p.plm_project_id
                WHERE p.board_type = 'SCRUM' AND p.status = 'ACTIVE'""")) {
            long id = ((Number) p.get("id")).longValue();
            List<Map<String, Object>> active = jdbc.queryForList("SELECT id, end_date FROM pm_sprint WHERE project_id = ? AND status = 'ACTIVE'", id);
            boolean running = "ACTIVE".equals(p.get("npi_status"));
            if (!active.isEmpty()) {
                if (((Date) active.get(0).get("end_date")).toLocalDate().isAfter(d)) {
                    continue;
                }
                pm.closeSprint(((Number) active.get(0).get("id")).longValue(), null, d.atTime(17, 30));
            }
            if (!running) {
                continue;
            }
            startSprint(id, ((Number) p.get("lead_emp_id")).longValue(), (String) p.get("stage"), d);
        }
    }

    private void startSprint(long projectId, long lead, String stage, LocalDate d) {
        int n = jdbc.queryForObject("SELECT COUNT(*) FROM pm_sprint WHERE project_id = ?", Integer.class, projectId) + 1;
        long team = teamOf.values().stream().filter(v -> v == projectId).count();
        // 每人每迭代约 11 点（≈ 66 小时，占可用工时 80% 左右，其余为会议、支持）
        int capacity = (int) Math.round(Math.max(1, team) * 11 * (0.85 + rnd.nextDouble() * 0.25));
        long sprint = pm.createSprint(projectId, "Sprint " + n, stageGoal(stage), d, d.plusDays(13));
        LocalDateTime at = d.atTime(9, 30);
        int planned = 0;
        // 先放入待办池中的遗留问题
        for (Map<String, Object> i : jdbc.queryForList("SELECT id, story_points FROM pm_issue WHERE project_id = ? AND sprint_id IS NULL AND status <> 'DONE' AND type <> 'EPIC' ORDER BY id",
                projectId)) {
            if (planned >= capacity) {
                break;
            }
            pm.moveToSprint(((Number) i.get("id")).longValue(), sprint, lead, at);
            planned += i.get("story_points") == null ? 1 : ((Number) i.get("story_points")).intValue();
        }
        Long epic = jdbc.queryForList("SELECT id FROM pm_issue WHERE project_id = ? AND type = 'EPIC' AND title = ?", Long.class, projectId, epicOf(stage))
                .stream().findFirst().orElse(null);
        // 史诗跟随 NPI 阶段：当前阶段史诗进入进行中；之前阶段的史诗子问题全部完成后关闭
        if (epic != null && "TODO".equals(jdbc.queryForObject("SELECT status FROM pm_issue WHERE id = ?", String.class, epic))) {
            pm.transition(epic, "IN_PROGRESS", null, lead, at);
        }
        for (Long e : jdbc.queryForList("""
                SELECT e.id FROM pm_issue e WHERE e.project_id = ? AND e.type = 'EPIC' AND e.status = 'IN_PROGRESS' AND e.id <> ?
                  AND NOT EXISTS (SELECT 1 FROM pm_issue c WHERE c.epic_id = e.id AND c.status <> 'DONE')""", Long.class, projectId, epic == null ? -1 : epic)) {
            pm.transition(e, "DONE", "FIXED", lead, at);
        }
        String[] work = STAGE_WORK.getOrDefault(stage, STAGE_WORK.get("DESIGN"));
        while (planned < capacity) {
            boolean bug = List.of("PROTOTYPE", "PILOT").contains(stage) && rnd.nextDouble() < 0.25;
            String type = bug ? "BUG" : rnd.nextDouble() < 0.3 ? "TASK" : "STORY";
            int pts = new int[]{1, 2, 3, 3, 5, 5, 8}[rnd.nextInt(7)];
            String title = bug ? BUGS[rnd.nextInt(BUGS.length)] : work[rnd.nextInt(work.length)] + ("STORY".equals(type) ? "" : "（补充）");
            long issue = pm.createIssue(new PmService.IssueReq(projectId, type, title, null, bug ? "HIGH" : "MEDIUM", null, epic, pts,
                    BigDecimal.valueOf(pts * 6L), stage, null, null, null), lead, at);
            pm.moveToSprint(issue, sprint, lead, at);
            planned += pts;
        }
        pm.startSprint(sprint);
    }

    /** 工程师一天的工作：继续自己进行中的问题，没有就从所在项目领取；登记工时，工时够了提交评审；评审次日给结论 */
    private void work(LocalDate d) {
        LocalDateTime noon = d.atTime(12, 0), evening = d.atTime(17, 30);
        // 评审：昨天及之前提交评审的，85% 通过
        for (Map<String, Object> i : jdbc.queryForList("""
                SELECT i.id, i.assignee_emp_id, p.lead_emp_id FROM pm_issue i JOIN pm_project p ON p.id = i.project_id
                WHERE i.status = 'IN_REVIEW' AND i.complaint_id IS NULL AND (SELECT MAX(h.changed_at) FROM pm_issue_history h WHERE h.issue_id = i.id) < ?""", Date.valueOf(d))) {
            long id = ((Number) i.get("id")).longValue(), lead = ((Number) i.get("lead_emp_id")).longValue();
            if (rnd.nextDouble() < 0.85) {
                pm.transition(id, "DONE", "FIXED", lead, d.atTime(10, 0));
            } else {
                pm.transition(id, "IN_PROGRESS", null, lead, d.atTime(10, 0));
                pm.comment(id, lead, "评审意见：测试数据不完整，请补充后再提交", d.atTime(10, 0));
                effort.merge(id, 6.0, Double::sum);
            }
        }
        Set<Long> present = new HashSet<>(jdbc.queryForList("""
                SELECT a.employee_id FROM hr_attendance a JOIN hr_employee e ON e.id = a.employee_id JOIN org_dept dp ON dp.id = e.dept_id
                WHERE a.work_date = ? AND a.status IN ('NORMAL','LATE') AND dp.code = 'RND'""", Long.class, Date.valueOf(d)));
        for (Long eng : engineers()) {
            if (!present.contains(eng)) {
                continue;
            }
            double hours = 6.5 + rnd.nextInt(4) * 0.5;
            // 平台负责人每天 2 小时处理客诉缺陷
            if (eng.equals(svLead)) {
                List<Long> bugs = jdbc.queryForList("SELECT id FROM pm_issue WHERE project_id IN (?, ?) AND type = 'BUG' AND status IN ('TODO','IN_PROGRESS') ORDER BY FIELD(priority,'HIGHEST','HIGH','MEDIUM','LOW'), id LIMIT 1",
                        Long.class, sv, bl);
                if (!bugs.isEmpty()) {
                    long b = bugs.get(0);
                    if ("TODO".equals(jdbc.queryForObject("SELECT status FROM pm_issue WHERE id = ?", String.class, b))) {
                        pm.assign(b, eng, eng, noon);
                        pm.transition(b, "IN_PROGRESS", null, eng, noon);
                    }
                    pm.logWork(b, eng, d, BigDecimal.valueOf(2), "客诉根因分析");
                    hours -= 2;
                }
            }
            long project = teamOf.getOrDefault(eng, te);
            Long issue = pick(eng, project, noon);
            if (issue == null) {
                issue = pullFromBacklog(eng, project, noon); // 迭代内的活干完了，从待办池拉入（记为范围变更）
            }
            if (issue == null) {
                issue = pick(eng, te, noon);
            }
            if (issue == null) {
                issue = supportTask(eng, d); // 实在没有排期内的活：现场技术支持
            }
            try {
                pm.logWork(issue, eng, d, BigDecimal.valueOf(hours), null);
            } catch (BizException ignored) {
                continue;
            }
            double need = effort.computeIfAbsent(issue, k -> estimate(k));
            BigDecimal logged = jdbc.queryForObject("SELECT COALESCE(SUM(hours), 0) FROM pm_worklog WHERE issue_id = ?", BigDecimal.class, issue);
            if (logged.doubleValue() >= need) {
                String type = jdbc.queryForObject("SELECT type FROM pm_issue WHERE id = ?", String.class, issue);
                pm.transition(issue, "TASK".equals(type) ? "DONE" : "IN_REVIEW", "TASK".equals(type) ? "FIXED" : null, eng, evening);
            }
        }
    }

    /** 领取问题：自己进行中的优先，其次所在项目当前迭代 / 看板里未分配的待办 */
    private Long pick(long eng, long project, LocalDateTime at) {
        List<Long> mine = jdbc.queryForList("SELECT id FROM pm_issue WHERE assignee_emp_id = ? AND status = 'IN_PROGRESS' AND type <> 'EPIC' AND complaint_id IS NULL ORDER BY id LIMIT 1",
                Long.class, eng);
        if (!mine.isEmpty()) {
            return mine.get(0);
        }
        List<Long> todo = jdbc.queryForList("""
                SELECT i.id FROM pm_issue i JOIN pm_project p ON p.id = i.project_id LEFT JOIN pm_sprint s ON s.id = i.sprint_id
                WHERE i.project_id = ? AND i.status = 'TODO' AND i.type <> 'EPIC' AND i.complaint_id IS NULL AND (i.assignee_emp_id IS NULL OR i.assignee_emp_id = ?)
                  AND (p.board_type = 'KANBAN' OR s.status = 'ACTIVE') ORDER BY FIELD(i.priority,'HIGHEST','HIGH','MEDIUM','LOW'), i.id LIMIT 1""",
                Long.class, project, eng);
        if (todo.isEmpty()) {
            return null;
        }
        long id = todo.get(0);
        pm.assign(id, eng, eng, at);
        pm.transition(id, "IN_PROGRESS", null, eng, at);
        return id;
    }

    private Long pullFromBacklog(long eng, long project, LocalDateTime at) {
        Long sprint = jdbc.queryForList("SELECT id FROM pm_sprint WHERE project_id = ? AND status = 'ACTIVE'", Long.class, project).stream().findFirst().orElse(null);
        if (sprint == null) {
            return null;
        }
        List<Long> backlog = jdbc.queryForList("""
                SELECT id FROM pm_issue WHERE project_id = ? AND sprint_id IS NULL AND status = 'TODO' AND type <> 'EPIC' ORDER BY id LIMIT 1""", Long.class, project);
        if (backlog.isEmpty()) {
            return null;
        }
        pm.moveToSprint(backlog.get(0), sprint, eng, at);
        return pick(eng, project, at);
    }

    private Long supportTask(long eng, LocalDate d) {
        String[] t = {"产线技术支持：装配异常分析", "客户技术咨询回复", "专利交底书撰写", "技术文档整理归档", "供应商样品评估"};
        long id = pm.createIssue(new PmService.IssueReq(te, "TASK", t[rnd.nextInt(t.length)], null, "LOW", eng, null, 1, BigDecimal.valueOf(6), "支持",
                null, null, null), eng, d.atTime(9, 0));
        pm.transition(id, "IN_PROGRESS", null, eng, d.atTime(9, 5));
        return id;
    }

    private double estimate(long issueId) {
        Integer pts = jdbc.queryForObject("SELECT story_points FROM pm_issue WHERE id = ?", Integer.class, issueId);
        return (pts == null ? 2 : pts) * 6.0 * (0.7 + rnd.nextDouble() * 0.7);
    }

    private void maintenanceTask(LocalDate d) {
        String[] t = {"伺服出厂测试程序优化", "编码器零位校准夹具改进", "绕线机张力参数固化", "老化房温控改造评估", "测试台数据接口对接 MES", "新品工装夹具设计"};
        long lead = jdbc.queryForObject("SELECT lead_emp_id FROM pm_project WHERE id = ?", Long.class, te);
        int pts = 2 + rnd.nextInt(4);
        pm.createIssue(new PmService.IssueReq(te, "TASK", t[rnd.nextInt(t.length)], null, "MEDIUM", null, null, pts, BigDecimal.valueOf(pts * 6L), "工装",
                null, null, null), lead, d.atTime(9, 0));
    }

    /** 工程师修订作业指导书：补充注意事项（产生新版本，可在知识库对比版本差异） */
    private void reviseSop(LocalDate d) {
        List<Map<String, Object>> pages = jdbc.queryForList("SELECT id, title, body FROM kb_page WHERE source_type = 'ROUTING' ORDER BY id");
        if (pages.isEmpty()) {
            return;
        }
        Map<String, Object> p = pages.get(rnd.nextInt(pages.size()));
        String[] notes = {"首件确认增加扭矩复测。", "换型后前 5 台 100% 检验关键尺寸。", "绕线嘴每班点检一次，磨损超过 0.05mm 立即更换。",
                "环境湿度 > 70% 时浸漆后延长烘干 20 分钟。", "磁钢粘接后静置 30 分钟再充磁。", "编码器安装使用防静电手环。"};
        String note = notes[rnd.nextInt(notes.length)];
        String body = (String) p.get("body");
        if (body.contains(note)) {
            return;
        }
        Long author = engineers().get(rnd.nextInt(engineers().size()));
        kb.save(((Number) p.get("id")).longValue(), null, null, (String) p.get("title"), body + "- " + note + "（" + d + " 修订）\n", null, true, author,
                "补充：" + note, null, null, d.atTime(15, 0));
    }

    private List<Long> engineers() {
        return jdbc.queryForList("""
                SELECT e.id FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id WHERE d.code = 'RND' AND e.status <> 'LEFT' ORDER BY e.id""", Long.class);
    }

    private static String epicOf(String stage) {
        return switch (stage) {
            case "CONCEPT" -> "概念与规格";
            case "DESIGN" -> "方案设计";
            case "PROTOTYPE" -> "样机验证";
            case "PILOT" -> "试产导入";
            default -> "量产移交";
        };
    }

    private static String stageGoal(String stage) {
        return switch (stage) {
            case "CONCEPT" -> "完成规格定义与技术路线评审";
            case "DESIGN" -> "完成方案设计与 DFMEA";
            case "PROTOTYPE" -> "样机性能达标，关闭设计评审问题";
            case "PILOT" -> "试产良率达标，客户送样通过";
            default -> "量产稳定，成本达标";
        };
    }

    static boolean workday(LocalDate d) {
        return WorkCalendar.isWorkday(d);
    }
}
