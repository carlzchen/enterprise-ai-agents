package com.minierp.seed;

import com.minierp.aftersales.AfterSalesService;
import com.minierp.common.SlaClock;
import com.minierp.common.WorkCalendar;
import com.minierp.finance.Expense;
import com.minierp.finance.FinanceService;
import com.minierp.itsm.ItsmService;
import com.minierp.itsm.PriorityMatrix;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 售后与 IT 服务台模拟。
 * 售后：
 *   - 伺服现场故障按在保装机量随机发生（按序列号追溯）；
 *   - 其他客户的技术咨询、安装调试、批次故障；
 *   - 中高严重度客诉开 RMA 退回检验，严重客诉另有客户索赔；
 *   - 月末计提质量保证金，年末调整。
 * 故事：2025-11 ~ 2026-01 生产的伺服编码器虚焊（与 QMS 客诉、8D 呼应），2025-12 起现场故障明显上升，多数换新；
 *   2026-04 起开始出现保外付费维修。
 * IT：
 *   - 日常故障与服务请求；
 *   - 入离职开通与回收；
 *   - 每月补丁变更、每季度 ERP 发版；
 *   - 2025-08 MES 服务器磁盘写满（P1），扩容变更；
 *   - 2026-02 钓鱼邮件致 3 个邮箱失陷（P1），高风险变更上线 MFA；
 *   - SolidWorks 席位不足，2025-03 增购。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class SvcSimulation {
    private final JdbcTemplate jdbc;
    private final AfterSalesService svc;
    private final ItsmService itsm;
    private final FinanceService finance;
    private final HrSimulation hr;

    private Random rnd;
    private final TreeMap<LocalDate, List<Runnable>> tasks = new TreeMap<>();
    private final Map<YearMonth, long[]> servoBase = new HashMap<>(); // {minId, installed}
    private long[] storyRange;
    private YearMonth storyMonth;
    private LocalDate today;
    private int ciSeq;

    // ================= 初始化 =================

    public void init(LocalDate start, Random random) {
        rnd = random;
        svc.opening(new BigDecimal("300000"), start.minusDays(1));
        cmdb(start);
        licenses(start);
        log.info("Service desk seeded: {} CIs", jdbc.queryForObject("SELECT COUNT(*) FROM it_ci", Integer.class));
    }

    private void cmdb(LocalDate start) {
        Long erpFa = jdbc.queryForList("SELECT id FROM fa_asset WHERE name = 'ERP 与 MES 服务器'", Long.class).stream().findFirst().orElse(null);
        Long wsFa = jdbc.queryForList("SELECT id FROM fa_asset WHERE name = '研发仿真工作站'", Long.class).stream().findFirst().orElse(null);
        Long it = dept("IT");
        Object[][] fixed = { // type, name, model, criticality, dept, date, fa
                {"SERVER", "MES 应用服务器", "Dell PowerEdge R750", "HIGH", it, "2023-06-20", erpFa}, {"SERVER", "ERP 数据库服务器", "Dell PowerEdge R750", "HIGH", it, "2023-06-20", erpFa},
                {"SERVER", "文件服务器", "Dell PowerEdge R450", "MEDIUM", it, "2021-04-10", null}, {"SERVER", "域控制器（AD）", "Dell PowerEdge R350", "HIGH", it, "2021-04-10", null},
                {"SERVER", "备份一体机", "Synology SA3400", "MEDIUM", it, "2022-09-15", null}, {"NETWORK", "邮件安全网关", "Fortinet FortiMail", "HIGH", it, "2020-11-01", null},
                {"NETWORK", "核心交换机", "Huawei S6730", "HIGH", it, "2021-04-10", null}, {"NETWORK", "防火墙", "Fortinet FortiGate 200F", "HIGH", it, "2022-03-01", null},
                {"DESKTOP", "研发仿真工作站", "HP Z8 G5", "MEDIUM", dept("RND"), "2024-03-28", wsFa}};
        for (Object[] f : fixed) {
            ci((String) f[0], (String) f[1], (String) f[2], "IN_USE", null, (Long) f[4], "机房", LocalDate.parse((String) f[5]), (String) f[3], (Long) f[6]);
        }
        for (String ws : List.of("WS1", "WS2", "LOG", "EQP")) {
            ci("NETWORK", deptName(ws) + " 接入交换机", "Huawei S5735", "IN_USE", null, dept(ws), deptName(ws), LocalDate.of(2021, 4, 10), "MEDIUM", null);
        }
        for (int i = 1; i <= 6; i++) {
            ci("NETWORK", "无线 AP-" + i, "Huawei AirEngine 5761", "IN_USE", null, it, "办公楼 " + i + " 层", LocalDate.of(2022, 3, 1), "LOW", null);
        }
        for (String p : List.of("ADM", "FIN", "SAL", "PUR", "QA", "LOG", "WS1", "WS2")) {
            ci("PRINTER", deptName(p) + " 打印机", "HP LaserJet M507", "IN_USE", null, dept(p), deptName(p), LocalDate.of(2020 + rnd.nextInt(4), 1 + rnd.nextInt(12), 5), "LOW", null);
        }
        jdbc.query("SELECT line_code, name FROM mes_line ORDER BY line_code", rs -> {
            String line = rs.getString(1);
            String ws = List.of("L1", "L2", "L6").contains(line) ? "WS1" : "WS2";
            for (int i = 1; i <= 2; i++) {
                ci("TERMINAL", line + " MES 终端 #" + i, "研华 PPC-3150", "IN_USE", null, dept(ws), rs.getString(2), LocalDate.of(2023, 6, 20), "MEDIUM", null);
            }
        });
        String[] models = {"ThinkPad T14", "ThinkPad E14", "Dell Latitude 5440", "HP EliteBook 840"};
        for (Map<String, Object> e : jdbc.queryForList("""
                SELECT e.id, e.dept_id, d.name FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id
                WHERE e.status <> 'LEFT' AND e.leave_date IS NULL AND e.wage_type = 'MONTHLY' ORDER BY e.id""")) {
            ci("LAPTOP", "笔记本电脑", models[rnd.nextInt(models.length)], "IN_USE", ((Number) e.get("id")).longValue(), ((Number) e.get("dept_id")).longValue(),
                    (String) e.get("name"), start.minusDays(60 + rnd.nextInt(365 * 5)), "LOW", null);
        }
        for (int i = 0; i < 6; i++) {
            ci("LAPTOP", "笔记本电脑", "ThinkPad E14", "STOCK", null, it, "信息部库房", start.minusDays(20 + rnd.nextInt(60)), "LOW", null);
        }
    }

    private void licenses(LocalDate start) {
        int office = jdbc.queryForObject("SELECT COUNT(*) FROM hr_employee WHERE status <> 'LEFT' AND leave_date IS NULL AND wage_type = 'MONTHLY'", Integer.class);
        Object[][] ls = {{"Microsoft 365 商业标准版", "Microsoft", office + 6, 1150, "ALL", "2025-06-30"}, {"SolidWorks Premium", "Dassault Systèmes", 12, 18000, "RND", "2025-03-31"},
                {"Altium Designer", "Altium", 4, 16000, "RND", "2025-09-30"}, {"AutoCAD", "Autodesk", 3, 9800, "EQP", "2025-05-31"},
                {"MATLAB", "MathWorks", 3, 12500, "RND", "2025-11-30"}, {"Minitab", "Minitab", 3, 9000, "QA", "2025-08-31"}};
        for (Object[] l : ls) {
            jdbc.update("INSERT INTO it_license (name, vendor, seats, unit_cost, scope, renew_date) VALUES (?,?,?,?,?,?)", l[0], l[1], l[2], l[3], l[4], Date.valueOf((String) l[5]));
            long id = jdbc.queryForObject("SELECT id FROM it_license WHERE name = ?", Long.class, l[0]);
            for (Long emp : jdbc.queryForList("""
                    SELECT e.id FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id WHERE e.status <> 'LEFT' AND e.leave_date IS NULL AND e.wage_type = 'MONTHLY'
                      AND (? = 'ALL' OR d.code = ?) ORDER BY e.hire_date""", Long.class, l[4], l[4])) {
                if (!itsm.assignLicense(id, emp, start.minusDays(1))) {
                    break;
                }
            }
        }
    }

    private long ci(String type, String name, String model, String status, Long owner, Long dept, String location, LocalDate bought, String crit, Long fa) {
        String prefix = switch (type) {
            case "LAPTOP" -> "LT";
            case "DESKTOP" -> "DT";
            case "SERVER" -> "SV";
            case "NETWORK" -> "NW";
            case "PRINTER" -> "PR";
            default -> "TM";
        };
        String no = prefix + "-" + String.format("%04d", ++ciSeq);
        jdbc.update("""
                INSERT INTO it_ci (ci_no, type, name, model, status, owner_emp_id, dept_id, location, purchase_date, criticality, fa_asset_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                no, type, name, model, status, owner, dept, location, Date.valueOf(bought), crit, fa);
        return jdbc.queryForObject("SELECT id FROM it_ci WHERE ci_no = ?", Long.class, no);
    }

    // ================= 每日 =================

    public void onDay(LocalDate d) {
        today = d;
        servoFaults(d);
        otherTickets(d);
        complaintRmas(d);
        itIncidents(d);
        itRequests(d);
        joiners(d);
        itStories(d);
        implementChanges(d);
        if (isFirstWorkday(d)) {
            monthlyIt(d);
        }
        renewLicenses(d);
        // 任务里可能再排当天或更早的后续任务（如收货后检验），循环到当天无待办为止，保证在月结前入账
        for (List<List<Runnable>> due = drain(d); !due.isEmpty(); due = drain(d)) {
            due.forEach(rs -> rs.forEach(Runnable::run));
        }
        YearMonth ym = YearMonth.from(d);
        if (d.equals(WorkCalendar.lastWorkday(ym))) {
            svc.accrue(ym, d);
            if (ym.getMonthValue() == 12) {
                svc.yearEnd(ym.getYear(), d);
            }
        }
    }

    // ================= 售后：伺服现场故障 =================

    private static final String[] FAULTS = {"NOISE", "ENCODER", "OVERHEAT", "NO_START", "SPEED", "PARAM"};
    private static final double[] FAULT_W = {0.22, 0.15, 0.15, 0.15, 0.13, 0.20};
    private static final Map<String, String> SYMPTOM = Map.of("NOISE", "电机运行异响、振动偏大", "ENCODER", "驱动器报编码器通讯故障（Err 21）",
            "OVERHEAT", "电机温升过高，驱动器过热报警", "NO_START", "上电后电机不转，驱动器无输出", "SPEED", "低速爬行、速度波动大",
            "PARAM", "增益参数不匹配，定位超调");

    private void servoFaults(LocalDate d) {
        long[] base = servoBase.computeIfAbsent(YearMonth.from(d), ym -> {
            Long minId = jdbc.queryForObject("SELECT MIN(id) FROM mes_serial WHERE produced_date >= ?", Long.class, Date.valueOf(d.minusMonths(18)));
            if (minId == null) {
                return new long[]{0, 0};
            }
            return new long[]{minId, jdbc.queryForObject("SELECT COUNT(*) FROM mes_serial WHERE id >= ? AND delivery_id IS NOT NULL", Long.class, minId)};
        });
        int n = poisson(base[1] * 0.000018);
        for (int i = 0; i < n; i++) {
            boolean old = !d.isBefore(LocalDate.of(2026, 4, 1)) && rnd.nextDouble() < 0.12;
            String sn = old ? randomSerial(1, base[0] - 1) : randomSerial(base[0], Long.MAX_VALUE);
            if (sn != null) {
                servoTicket(d, sn, pickFault());
            }
        }
        if (!d.isBefore(LocalDate.of(2025, 12, 10)) && d.isBefore(LocalDate.of(2026, 7, 1))) {
            if (storyRange == null || !YearMonth.from(d).equals(storyMonth)) { // 故事批次在 2026-01 前仍在生产，按月刷新范围
                storyMonth = YearMonth.from(d);
                Map<String, Object> r = jdbc.queryForMap("SELECT MIN(id) AS lo, MAX(id) AS hi FROM mes_serial WHERE produced_date BETWEEN '2025-11-01' AND '2026-01-31'");
                storyRange = r.get("lo") == null ? new long[]{0, 0} : new long[]{((Number) r.get("lo")).longValue(), ((Number) r.get("hi")).longValue()};
            }
            int extra = storyRange[1] > 0 ? poisson(d.isBefore(LocalDate.of(2026, 3, 1)) ? 1.5 : 0.8) : 0;
            for (int i = 0; i < extra; i++) {
                String sn = randomSerial(storyRange[0], storyRange[1]);
                if (sn != null) {
                    servoTicket(d, sn, "ENCODER");
                }
            }
        }
    }

    private String randomSerial(long lo, long hi) {
        Long max = jdbc.queryForObject("SELECT MAX(id) FROM mes_serial", Long.class);
        if (max == null || lo < 1) {
            return null;
        }
        long top = Math.min(hi, max);
        if (top <= lo) {
            return null;
        }
        long pick = lo + (long) (rnd.nextDouble() * (top - lo));
        List<String> sn = jdbc.queryForList("""
                SELECT s.sn FROM mes_serial s WHERE s.id >= ? AND s.id <= ? AND s.delivery_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM svc_ticket t WHERE t.sn = s.sn AND t.status <> 'CLOSED') ORDER BY s.id LIMIT 1""", String.class, pick, top);
        return sn.isEmpty() ? null : sn.get(0);
    }

    private String pickFault() {
        double r = rnd.nextDouble(), acc = 0;
        for (int i = 0; i < FAULTS.length; i++) {
            acc += FAULT_W[i];
            if (r < acc) {
                return FAULTS[i];
            }
        }
        return "PARAM";
    }

    private void servoTicket(LocalDate d, String sn, String fault) {
        String prio = rnd.nextDouble() < 0.2 ? "P1" : rnd.nextDouble() < 0.55 ? "P2" : "P3";
        LocalDateTime at = d.atTime("P1".equals(prio) && rnd.nextDouble() < 0.25 ? 18 + rnd.nextInt(4) : 8 + rnd.nextInt(10), rnd.nextInt(60));
        long id = svc.createTicket(new AfterSalesService.TicketReq(null, null, sn, channel(), "FAULT", prio, SYMPTOM.get(fault), fault), at);
        boolean inWarranty = "IN".equals(jdbc.queryForObject("SELECT warranty_status FROM svc_ticket WHERE id = ?", String.class, id));
        String resolution;
        if ("PARAM".equals(fault)) {
            resolution = "REMOTE";
        } else if ("ENCODER".equals(fault)) {
            resolution = rnd.nextDouble() < 0.75 ? "RMA" : "ONSITE";
        } else {
            double r = rnd.nextDouble();
            resolution = r < 0.3 ? "REMOTE" : r < 0.62 ? "ONSITE" : r < 0.95 ? "RMA" : "NO_FAULT";
        }
        runTicket(id, prio, at, resolution, inWarranty, fault);
    }

    /** 工单生命周期：派工 → 处理（远程 / 上门 / RMA）→ 关闭评价 */
    private void runTicket(long id, String prio, LocalDateTime created, String resolution, boolean inWarranty, String fault) {
        boolean biz = !"P1".equals(prio);
        boolean slow = rnd.nextDouble() < 0.06; // 少量工单因人手紧张响应超时
        double resp = switch (prio) {
            case "P1" -> slow ? 2 + rnd.nextDouble() * 2 : 0.2 + rnd.nextDouble() * 1.6;
            case "P2" -> slow ? 4 + rnd.nextDouble() * 3 : 0.3 + rnd.nextDouble() * 3.2;
            default -> slow ? 8 + rnd.nextDouble() * 6 : 0.5 + rnd.nextDouble() * 6;
        };
        LocalDateTime assignAt = SlaClock.due(created, resp, biz);
        Long eng = hr.pickEmployee("客户服务部");
        if (eng == null) {
            return;
        }
        later(assignAt.toLocalDate(), () -> svc.assign(id, eng, assignAt));
        double hours = switch (resolution) {
            case "REMOTE", "NO_FAULT" -> 0.5 + rnd.nextDouble() * (biz ? 12 : 8);
            case "ONSITE" -> biz ? 4 + rnd.nextDouble() * (rnd.nextDouble() < 0.1 ? 30 : 15) : 3 + rnd.nextDouble() * (rnd.nextDouble() < 0.1 ? 26 : 16);
            default -> 1 + rnd.nextDouble() * (biz ? 14 : 16);
        };
        LocalDateTime doneAt = SlaClock.due(assignAt, hours, biz);
        BigDecimal fee = !inWarranty && !"NO_FAULT".equals(resolution) ? BigDecimal.valueOf(("ONSITE".equals(resolution) ? 800 : 400) + rnd.nextInt(12) * 100) : BigDecimal.ZERO;
        later(doneAt.toLocalDate(), () -> {
            String note = switch (resolution) {
                case "REMOTE" -> "远程指导调整参数 / 接线后恢复";
                case "ONSITE" -> "工程师上门检修";
                case "RMA" -> inWarranty ? "先行发货换新，故障件返厂分析" : "保外返厂维修（客户付费）";
                default -> "现场确认为客户接线 / 负载问题，产品无故障";
            };
            BigDecimal f = "RMA".equals(resolution) && inWarranty ? BigDecimal.ZERO : fee;
            // 收费要入账：计划完成日已过（周末完成、周一处理）时按处理当天，避免落到已做增值税月结的日期
            LocalDateTime at = f.signum() > 0 && doneAt.toLocalDate().isBefore(today) ? today.atTime(9, 30) : doneAt;
            Long rma = svc.resolve(id, resolution, note, f, at);
            if ("ONSITE".equals(resolution)) {
                travel(eng, doneAt.toLocalDate());
            }
            if (rma != null) {
                runRma(rma, doneAt.toLocalDate(), "ENCODER".equals(fault) ? (rnd.nextDouble() < 0.85 ? "REPLACE" : "REPAIR")
                        : !inWarranty ? "REPAIR" : rnd.nextDouble() < 0.45 ? "REPLACE" : rnd.nextDouble() < 0.75 ? "REPAIR" : "NO_FAULT", false);
            }
            LocalDate closeDay = doneAt.toLocalDate().plusDays(1 + rnd.nextInt(4));
            later(closeDay, () -> {
                boolean breached = Boolean.TRUE.equals(jdbc.queryForObject("SELECT sla_breached = 1 FROM svc_ticket WHERE id = ?", Boolean.class, id));
                int sat = breached ? 2 + rnd.nextInt(3) : rnd.nextDouble() < 0.7 ? 5 : 4;
                svc.close(id, sat, closeDay.atTime(10, rnd.nextInt(60)));
            });
        });
    }

    private void runRma(long rmaId, LocalDate from, String disposition, boolean claim) {
        LocalDate recv = from.plusDays(3 + rnd.nextInt(6));
        later(recv, () -> {
            svc.receiveRma(rmaId, recv);
            LocalDate insp = recv.plusDays(1 + rnd.nextInt(4));
            later(insp, () -> {
                String finding = switch (disposition) {
                    case "REPLACE" -> "拆解分析确认故障，整机换新";
                    case "REPAIR" -> "更换故障部件后测试合格";
                    default -> "复测合格，未发现故障";
                };
                svc.inspectRma(rmaId, disposition, finding, insp);
                if (claim) {
                    LocalDate pay = insp.plusDays(20 + rnd.nextInt(25));
                    later(pay, () -> svc.payClaim(rmaId, BigDecimal.valueOf(8000 + rnd.nextInt(38) * 1000), pay)); // 停线损失、挑选返工费
                }
            });
        });
    }

    private void travel(long eng, LocalDate d) {
        Expense e = new Expense();
        e.setExpenseDate(d);
        e.setDept("客户服务部");
        e.setCategory("差旅费");
        e.setAmount(BigDecimal.valueOf(600 + rnd.nextInt(30) * 100));
        e.setEmployee(hr.nameOf(eng));
        e.setDescription("客户现场服务差旅");
        finance.submit(e, eng, d.atTime(17, 30));
    }

    // ================= 售后：其他工单与客诉 RMA =================

    private void otherTickets(LocalDate d) {
        int n = poisson(0.9);
        for (int i = 0; i < n; i++) {
            List<Map<String, Object>> cand = jdbc.queryForList("""
                    SELECT dl.customer_id, l.product_id, p.series FROM sal_delivery dl JOIN sal_delivery_line l ON l.delivery_id = dl.id JOIN md_product p ON p.id = l.product_id
                    WHERE dl.delivery_date BETWEEN ? AND ? AND p.series <> 'SERVO' ORDER BY dl.id DESC LIMIT 200""", Date.valueOf(d.minusDays(120)), Date.valueOf(d));
            if (cand.isEmpty()) {
                return;
            }
            Map<String, Object> c = cand.get(rnd.nextInt(cand.size()));
            double r = rnd.nextDouble();
            String type = r < 0.55 ? "CONSULT" : r < 0.72 ? "INSTALL" : "FAULT";
            String prio = "FAULT".equals(type) && rnd.nextDouble() < 0.4 ? "P2" : rnd.nextDouble() < 0.15 ? "P2" : "P3";
            String fault = "FAULT".equals(type) ? new String[]{"NOISE", "OVERHEAT", "NO_START", "SPEED"}[rnd.nextInt(4)] : null;
            String symptom = switch (type) {
                case "CONSULT" -> new String[]{"选型咨询：负载与转速匹配", "要求提供 RoHS / REACH 报告", "替代老型号的兼容性确认", "安装尺寸与接线图"}[rnd.nextInt(4)];
                case "INSTALL" -> "新产线导入，申请现场安装调试支持";
                default -> "批次使用中 " + SYMPTOM.get(fault);
            };
            LocalDateTime at = d.atTime(8 + rnd.nextInt(10), rnd.nextInt(60));
            long id = svc.createTicket(new AfterSalesService.TicketReq(((Number) c.get("customer_id")).longValue(), ((Number) c.get("product_id")).longValue(), null,
                    channel(), type, prio, symptom, fault), at);
            String res = "INSTALL".equals(type) ? "ONSITE" : "CONSULT".equals(type) ? "REMOTE" : rnd.nextDouble() < 0.2 ? "RMA" : rnd.nextBoolean() ? "REMOTE" : "ONSITE";
            runTicket(id, prio, at, res, true, fault);
        }
    }

    /** 中高严重度客诉：开 RMA 退回不良品检验处置；严重客诉另有客户索赔（停线损失、挑选返工费） */
    private void complaintRmas(LocalDate d) {
        for (Map<String, Object> c : jdbc.queryForList("""
                SELECT q.id, q.customer_id, q.product_id, q.qty_affected, q.severity FROM qms_complaint q
                WHERE q.severity IN ('HIGH','MEDIUM') AND q.received_date <= ? AND q.received_date >= ?
                  AND NOT EXISTS (SELECT 1 FROM svc_rma r WHERE r.complaint_id = q.id)""", Date.valueOf(d.minusDays(2)), Date.valueOf(d.minusDays(30)))) {
            long rma = svc.createRma("COMPLAINT", null, ((Number) c.get("id")).longValue(), ((Number) c.get("customer_id")).longValue(),
                    ((Number) c.get("product_id")).longValue(), null, (BigDecimal) c.get("qty_affected"), true, d);
            double r = rnd.nextDouble();
            runRma(rma, d.plusDays(2), r < 0.6 ? "REPLACE" : r < 0.9 ? "REPAIR" : "NO_FAULT", "HIGH".equals(c.get("severity")));
        }
    }

    private String channel() {
        return new String[]{"PHONE", "PHONE", "WECHAT", "EMAIL", "PORTAL"}[rnd.nextInt(5)];
    }

    // ================= IT =================

    private static final Object[][] INCIDENTS = { // category, weight, title, impact, urgency, root cause, resolution
            {"电脑故障", 25, "电脑蓝屏 / 无法开机", "LOW", "MEDIUM", "系统更新失败 / 硬盘坏道", "重装系统或更换硬盘，数据已恢复"},
            {"网络", 12, "无法上网 / 网速慢", "LOW", "MEDIUM", "IP 冲突 / 网线松动", "重新分配地址，更换网线"},
            {"打印机", 15, "打印机卡纸 / 无法打印", "LOW", "LOW", "驱动异常 / 硒鼓寿命到期", "重装驱动，更换硒鼓"},
            {"账号密码", 18, "域账号被锁定 / 忘记密码", "LOW", "HIGH", "密码过期后多次输错", "解锁并重置密码"},
            {"邮箱", 8, "邮件收发异常", "LOW", "MEDIUM", "邮箱容量满 / 客户端配置错误", "归档旧邮件，重新配置客户端"},
            {"ERP", 12, "ERP 单据保存报错 / 权限不足", "MEDIUM", "MEDIUM", "角色权限未分配 / 操作不规范", "调整角色权限，培训操作"},
            {"MES", 10, "MES 终端扫码无响应", "MEDIUM", "HIGH", "扫码枪接口松动 / 终端网络中断", "更换扫码枪，恢复网络"}};
    private static final Object[][] REQUESTS = {
            {"权限申请", 30, "申请共享盘 / ERP 模块权限"}, {"软件安装", 25, "申请安装专业软件"}, {"设备申领", 15, "申领显示器 / 键鼠"},
            {"打印耗材", 20, "申领硒鼓"}, {"会议支持", 10, "视频会议设备调试"}};

    private void itIncidents(LocalDate d) {
        int n = poisson(2.2);
        for (int i = 0; i < n; i++) {
            Object[] x = weighted(INCIDENTS);
            String cat = (String) x[0];
            Long requester = "MES".equals(cat) ? hr.pickEmployee("生产部") : officeEmployee();
            if (requester == null) {
                continue;
            }
            Long ci = switch (cat) {
                case "电脑故障", "账号密码", "邮箱" -> jdbc.queryForList("SELECT id FROM it_ci WHERE owner_emp_id = ? AND status = 'IN_USE' LIMIT 1", Long.class, requester).stream().findFirst().orElse(null);
                case "MES" -> randomCi("TERMINAL");
                case "打印机" -> randomCi("PRINTER");
                case "网络" -> randomCi("NETWORK");
                case "ERP" -> jdbc.queryForObject("SELECT id FROM it_ci WHERE name = 'ERP 数据库服务器'", Long.class);
                default -> null;
            };
            LocalDateTime at = d.atTime(8 + rnd.nextInt(9), rnd.nextInt(60));
            long id = itsm.createTicket(new ItsmService.TicketReq("INCIDENT", cat, (String) x[2], null, requester, ci, (String) x[3], (String) x[4]), at);
            runIt(id, at, (String) x[6], (String) x[5]);
        }
    }

    private void itRequests(LocalDate d) {
        int n = poisson(1.3);
        for (int i = 0; i < n; i++) {
            Object[] x = weighted(REQUESTS);
            Long requester = officeEmployee();
            if (requester == null) {
                continue;
            }
            LocalDateTime at = d.atTime(8 + rnd.nextInt(9), rnd.nextInt(60));
            long id = itsm.createTicket(new ItsmService.TicketReq("REQUEST", (String) x[0], (String) x[2], null, requester, null, "LOW", rnd.nextDouble() < 0.3 ? "MEDIUM" : "LOW"), at);
            runIt(id, at, "已处理", null);
        }
    }

    private void runIt(long id, LocalDateTime created, String resolution, String rootCause) {
        String prio = jdbc.queryForObject("SELECT priority FROM it_ticket WHERE id = ?", String.class, id);
        boolean biz = PriorityMatrix.sla(prio).businessHours();
        boolean slow = rnd.nextDouble() < 0.05;
        double resp = switch (prio) {
            case "P1" -> 0.05 + rnd.nextDouble() * 0.4;
            case "P2" -> slow ? 1 + rnd.nextDouble() : 0.1 + rnd.nextDouble() * 0.8;
            case "P3" -> slow ? 4 + rnd.nextDouble() * 3 : 0.2 + rnd.nextDouble() * 3;
            default -> slow ? 8 + rnd.nextDouble() * 6 : 0.5 + rnd.nextDouble() * 6.5;
        };
        double work = switch (prio) {
            case "P1" -> 0.5 + rnd.nextDouble() * 4;
            case "P2" -> 0.5 + rnd.nextDouble() * (rnd.nextDouble() < 0.1 ? 9 : 5.5);
            case "P3" -> 0.5 + rnd.nextDouble() * (rnd.nextDouble() < 0.08 ? 34 : 17);
            default -> 1 + rnd.nextDouble() * (rnd.nextDouble() < 0.06 ? 50 : 28);
        };
        LocalDateTime assignAt = SlaClock.due(created, resp, biz), doneAt = SlaClock.due(assignAt, work, biz);
        Long eng = hr.pickEmployee("信息部");
        if (eng == null) {
            return;
        }
        later(assignAt.toLocalDate(), () -> itsm.assign(id, eng, assignAt));
        later(doneAt.toLocalDate(), () -> {
            itsm.resolve(id, resolution, rootCause, doneAt);
            LocalDate close = doneAt.toLocalDate().plusDays(1 + rnd.nextInt(3));
            later(close, () -> {
                boolean breached = Boolean.TRUE.equals(jdbc.queryForObject("SELECT sla_breached = 1 FROM it_ticket WHERE id = ?", Boolean.class, id));
                itsm.close(id, breached ? 3 + rnd.nextInt(2) : rnd.nextDouble() < 0.75 ? 5 : 4, close.atTime(11, rnd.nextInt(60)));
            });
        });
    }

    /** 入离职：当天入职的职员开通账号领电脑，离职的回收 */
    private void joiners(LocalDate d) {
        for (Long emp : jdbc.queryForList("SELECT id FROM hr_employee WHERE hire_date = ? AND wage_type = 'MONTHLY'", Long.class, Date.valueOf(d))) {
            LocalDateTime at = d.atTime(9, 0);
            runIt(itsm.onboard(emp, at), at, "账号、邮箱、电脑、软件许可已开通", null);
        }
        for (Long emp : jdbc.queryForList("SELECT id FROM hr_employee WHERE leave_date = ? AND wage_type = 'MONTHLY'", Long.class, Date.valueOf(d))) {
            LocalDateTime at = d.atTime(16, 0);
            runIt(itsm.offboard(emp, at), at, "设备已回收，账号已停用", null);
        }
    }

    private void itStories(LocalDate d) {
        long itHead = head("IT");
        if (d.equals(LocalDate.of(2025, 8, 18))) {
            LocalDateTime at = d.atTime(10, 20);
            Long mes = jdbc.queryForObject("SELECT id FROM it_ci WHERE name = 'MES 应用服务器'", Long.class);
            long id = itsm.createTicket(new ItsmService.TicketReq("INCIDENT", "MES", "MES 系统全厂无法报工（P1 重大故障）", "8 条产线终端报工、扫码全部超时",
                    hr.pickEmployee("生产部"), mes, "HIGH", "HIGH"), at);
            itsm.assign(id, itHead, at.plusMinutes(8));
            itsm.resolve(id, "清理归档日志、释放磁盘空间后重启应用服务，13:40 恢复", "MES 应用日志未配置轮转，数据盘写满导致数据库连接池耗尽", d.atTime(13, 40));
            itsm.submitChange(new ItsmService.ChangeReq("MES 服务器数据盘扩容与日志轮转策略", "扩容 2TB，日志按天轮转保留 30 天，增加磁盘使用率监控告警（>80%）", "MEDIUM",
                    LocalDateTime.of(2025, 8, 23, 20, 0), id, mes), itHead, d.atTime(16, 0));
        }
        if (d.equals(LocalDate.of(2026, 2, 26))) {
            LocalDateTime at = d.atTime(9, 5);
            Long gw = jdbc.queryForObject("SELECT id FROM it_ci WHERE name = '邮件安全网关'", Long.class);
            long id = itsm.createTicket(new ItsmService.TicketReq("INCIDENT", "邮箱", "钓鱼邮件导致 3 个邮箱账号失陷，向客户发送虚假付款通知", "财务、销售收到仿冒“银行账户变更”邮件",
                    hr.pickEmployee("财务部"), gw, "HIGH", "HIGH"), at);
            itsm.assign(id, itHead, at.plusMinutes(12));
            itsm.resolve(id, "重置失陷账号密码、清除邮件转发规则、通知客户核实付款信息，16:00 处置完成", "员工点击仿冒登录页泄露密码；邮箱未启用多因素认证", d.atTime(16, 0));
            itsm.submitChange(new ItsmService.ChangeReq("邮件安全网关升级与全员启用多因素认证（MFA）", "网关启用 URL 沙箱与仿冒域名检测；全员 MFA；财务付款信息变更需电话复核",
                    "HIGH", LocalDateTime.of(2026, 3, 7, 9, 0), id, gw), itHead, d.atTime(17, 0));
        }
        // 每月第二个周二提交补丁变更，周六晚实施
        if (d.getDayOfWeek() == DayOfWeek.TUESDAY && d.getDayOfMonth() >= 8 && d.getDayOfMonth() <= 14) {
            itsm.submitChange(new ItsmService.ChangeReq(YearMonth.from(d) + " 月度系统补丁", "Windows 客户端与服务器安全补丁，先测试环境验证", "LOW",
                    d.plusDays(4).atTime(21, 0), null, null), itHead, d.atTime(10, 0));
        }
        // 每季度 ERP 发版
        if (List.of(1, 4, 7, 10).contains(d.getMonthValue()) && d.getDayOfWeek() == DayOfWeek.WEDNESDAY && d.getDayOfMonth() >= 8 && d.getDayOfMonth() <= 14) {
            itsm.submitChange(new ItsmService.ChangeReq("ERP 季度版本发布（" + d.getYear() + "Q" + ((d.getMonthValue() - 1) / 3 + 1) + "）", "新功能与缺陷修复，停机窗口 2 小时，回滚方案已演练",
                    "MEDIUM", d.plusDays(10).atTime(20, 0), null, jdbc.queryForObject("SELECT id FROM it_ci WHERE name = 'ERP 数据库服务器'", Long.class)), itHead, d.atTime(10, 30));
        }
        if (d.equals(LocalDate.of(2025, 3, 17))) {
            itsm.addSeats(jdbc.queryForObject("SELECT id FROM it_license WHERE name = 'SolidWorks Premium'", Long.class), 4, d);
        }
    }

    private void implementChanges(LocalDate d) {
        for (Map<String, Object> c : jdbc.queryForList("""
                SELECT id, planned_start, responded_at FROM it_ticket WHERE type = 'CHANGE' AND status = 'APPROVED' AND planned_start < ?""", Date.valueOf(d.plusDays(1)))) {
            LocalDateTime done = ts(c.get("planned_start")).plusHours(2), approved = ts(c.get("responded_at"));
            if (approved != null && done.isBefore(approved)) {
                done = approved.toLocalDate().plusDays(1).atTime(20, 0); // 审批晚于计划窗口，顺延到批准后的次日晚上实施
            }
            if (!done.toLocalDate().isAfter(d)) {
                itsm.implementChange(((Number) c.get("id")).longValue(), "按计划实施完成，验证通过", done);
            }
        }
    }

    /** 月初：M365 席位不足补购，笔记本库存低于 3 台补货 */
    private void monthlyIt(LocalDate d) {
        for (Map<String, Object> l : itsm.licenseCompliance()) {
            if ("ALL".equals(l.get("scope")) && ((Number) l.get("waiting")).intValue() > 0) {
                itsm.addSeats(((Number) l.get("id")).longValue(), ((Number) l.get("waiting")).intValue() + 5, d);
            }
        }
        int stock = jdbc.queryForObject("SELECT COUNT(*) FROM it_ci WHERE type = 'LAPTOP' AND status = 'STOCK'", Integer.class);
        if (stock < 3) {
            for (int i = 0; i < 5; i++) {
                ci("LAPTOP", "笔记本电脑", "ThinkPad T14", "STOCK", null, dept("IT"), "信息部库房", d, "LOW", null);
            }
        }
    }

    /** 软件许可到期续费：走费用报销（信息部 软件服务费） */
    private void renewLicenses(LocalDate d) {
        for (Map<String, Object> l : jdbc.queryForList("SELECT id, name, seats, unit_cost, renew_date FROM it_license WHERE renew_date <= ?", Date.valueOf(d))) {
            BigDecimal amount = ((BigDecimal) l.get("unit_cost")).multiply(BigDecimal.valueOf(((Number) l.get("seats")).intValue()));
            Long emp = hr.pickEmployee("信息部");
            Expense e = new Expense();
            e.setExpenseDate(d);
            e.setDept("信息部");
            e.setCategory("软件服务费");
            e.setAmount(amount);
            e.setEmployee(emp == null ? "信息部" : hr.nameOf(emp));
            e.setDescription(l.get("name") + " 年度订阅续费 " + l.get("seats") + " 席");
            finance.submit(e, emp, d.atTime(10, 0));
            jdbc.update("UPDATE it_license SET renew_date = DATE_ADD(renew_date, INTERVAL 1 YEAR) WHERE id = ?", l.get("id"));
        }
    }

    // ================= 工具 =================

    private static LocalDateTime ts(Object o) {
        return o == null ? null : o instanceof java.sql.Timestamp t ? t.toLocalDateTime() : (LocalDateTime) o;
    }

    private Long officeEmployee() {
        List<Long> ids = jdbc.queryForList("SELECT id FROM hr_employee WHERE status <> 'LEFT' AND leave_date IS NULL AND wage_type = 'MONTHLY'", Long.class);
        return ids.isEmpty() ? null : ids.get(rnd.nextInt(ids.size()));
    }

    private Long randomCi(String type) {
        List<Long> ids = jdbc.queryForList("SELECT id FROM it_ci WHERE type = ? AND status = 'IN_USE'", Long.class, type);
        return ids.isEmpty() ? null : ids.get(rnd.nextInt(ids.size()));
    }

    private Object[] weighted(Object[][] items) {
        int total = Arrays.stream(items).mapToInt(x -> (Integer) x[1]).sum(), r = rnd.nextInt(total);
        for (Object[] x : items) {
            r -= (Integer) x[1];
            if (r < 0) {
                return x;
            }
        }
        return items[0];
    }

    private int poisson(double lambda) {
        double l = Math.exp(-lambda), p = 1;
        int k = 0;
        do {
            k++;
            p *= rnd.nextDouble();
        } while (p > l);
        return k - 1;
    }

    private Long dept(String code) {
        return jdbc.queryForObject("SELECT id FROM org_dept WHERE code = ?", Long.class, code);
    }

    private String deptName(String code) {
        return jdbc.queryForObject("SELECT name FROM org_dept WHERE code = ?", String.class, code);
    }

    private long head(String code) {
        return jdbc.queryForObject("SELECT COALESCE(manager_emp_id, (SELECT manager_emp_id FROM org_dept WHERE code = 'GM')) FROM org_dept WHERE code = ?", Long.class, code);
    }

    private static boolean isFirstWorkday(LocalDate d) {
        LocalDate x = d.withDayOfMonth(1);
        while (!WorkCalendar.isWorkday(x)) {
            x = x.plusDays(1);
        }
        return x.equals(d);
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
