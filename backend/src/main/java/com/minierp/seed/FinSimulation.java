package com.minierp.seed;

import com.minierp.common.BizException;
import com.minierp.common.WorkCalendar;
import com.minierp.contract.ContractService;
import com.minierp.customs.CustomsService;
import com.minierp.fa.DepreciationCalculator;
import com.minierp.fa.FaService;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.treasury.TreasuryService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Date;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

/**
 * 财务延伸模拟：固定资产（期初建账、资本性支出审批、月度折旧、处置）、资金（银行流水与对账、报销付款、利息手续费）、
 * 预算（年度编制、超预算审批）、合同（年度框架合同、服务合同、保密协议）、关务与增值税（报关、免抵退月结、缴税、退税到账）。
 * 故事：夏季车间水电费超预算触发总经理审批；2026 年研发材料费超预算；伺服扩产、冲床模具备份等资本性支出与设备问题呼应。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class FinSimulation {
    private static final BigDecimal OPENING_CASH = new BigDecimal("80000000");
    private final JdbcTemplate jdbc;
    private final GlService gl;
    private final FaService fa;
    private final TreasuryService treasury;
    private final ContractService contracts;
    private final CustomsService customs;

    private Random rnd;
    private final TreeMap<LocalDate, List<long[]>> delayedStatements = new TreeMap<>();   // {entryId}
    private final TreeMap<LocalDate, List<Long>> pendingBooking = new TreeMap<>();        // 待依据回单入账的流水
    private final TreeMap<LocalDate, List<String>> refunds = new TreeMap<>();              // 退税到账
    private long lastEntryId;

    // ================= 初始化 =================

    public void init(LocalDate start, Random random) {
        rnd = random;
        LocalDate opening = start.minusDays(1);
        jdbc.update("INSERT INTO fin_bank_account (code, bank_name, account_no, currency, gl_account) VALUES ('ICBC-BASIC','中国工商银行深圳宝安支行（基本户）','4000 0231 0920 0012 345','CNY','1002')");
        gl.post(opening, "ADJUST", "期初", "期初银行存款", List.of(Line.dr(GlService.BANK, OPENING_CASH, "期初余额"), Line.cr(GlService.CAPITAL, OPENING_CASH, "实收资本")), null);
        Long entry = jdbc.queryForObject("SELECT MAX(id) FROM gl_entry WHERE account_code = '1002'", Long.class);
        long st = treasury.importStatement("ICBC-BASIC", opening, OPENING_CASH, null, "期初余额", "OPEN");
        jdbc.update("UPDATE fin_bank_statement SET matched_entry_id = ? WHERE id = ?", entry, st);
        lastEntryId = entry;
        createCategories();
        openingAssets(start);
        createBudgets(start);
        serviceContracts(start);
        log.info("Finance seeded: {} fixed assets", jdbc.queryForObject("SELECT COUNT(*) FROM fa_asset", Integer.class));
    }

    private void createCategories() {
        Object[][] c = {{"BUILDING", "房屋及建筑物", 240, "0.05", "SL"}, {"MACHINE", "机器设备", 120, "0.05", "SL"}, {"MOLD", "模具工装", 36, "0", "DDB"},
                {"VEHICLE", "运输设备", 48, "0.05", "SL"}, {"IT", "电子及办公设备", 36, "0.05", "SL"}};
        for (Object[] x : c) {
            jdbc.update("INSERT INTO fa_category (code, name, useful_life_months, residual_rate, method) VALUES (?,?,?,?,?)", x[0], x[1], x[2], new BigDecimal((String) x[3]), x[4]);
        }
    }

    private void openingAssets(LocalDate start) {
        LocalDate asOf = start.minusDays(1);
        List<Object[]> assets = new ArrayList<>(List.of(
                new Object[]{"一期厂房", "BUILDING", "PRD", "2012-06-30", 68_000_000, null}, new Object[]{"二期厂房（伺服车间）", "BUILDING", "WS2", "2018-09-30", 42_000_000, null},
                new Object[]{"研发办公楼", "BUILDING", "GM", "2015-03-31", 26_000_000, null}, new Object[]{"员工宿舍", "BUILDING", "ADM", "2016-05-31", 15_000_000, null},
                new Object[]{"配电房及变压器", "MACHINE", "EQP", "2019-04-30", 3_200_000, null}, new Object[]{"车间中央空调与净化系统", "MACHINE", "EQP", "2019-06-30", 2_800_000, null},
                new Object[]{"老化房", "MACHINE", "WS2", "2020-08-31", 2_000_000, null}, new Object[]{"伺服测试平台（研发）", "MACHINE", "RND", "2022-05-31", 1_800_000, null},
                new Object[]{"货车", "VEHICLE", "LOG", "2021-03-31", 350_000, null}, new Object[]{"货车", "VEHICLE", "LOG", "2022-07-31", 360_000, null},
                new Object[]{"商务车", "VEHICLE", "GM", "2021-10-31", 420_000, null}, new Object[]{"商务车", "VEHICLE", "SAL", "2023-02-28", 380_000, null},
                new Object[]{"ERP 与 MES 服务器", "IT", "IT", "2023-06-30", 600_000, null}, new Object[]{"研发仿真工作站", "IT", "RND", "2024-03-31", 450_000, null},
                new Object[]{"办公电脑", "IT", "ADM", "2023-09-30", 300_000, null}));
        for (int i = 0; i < 6; i++) {
            assets.add(new Object[]{"叉车 #" + (i + 1), "VEHICLE", "LOG", "2022-0" + (1 + i) + "-28", 120_000, null});
        }
        String[][] molds = {{"罩极电机冲片模具", "WS1"}, {"贯流风机模具", "WS1"}, {"齿轮箱注塑模具", "WS1"}, {"无刷电机冲片模具", "WS2"}, {"步进电机冲片模具", "WS2"},
                {"伺服定子冲片级进模", "WS2"}, {"伺服转子铁芯级进模", "WS2"}};
        for (String[] m : molds) {
            assets.add(new Object[]{m[0], "MOLD", m[1], (2023 + rnd.nextInt(2)) + "-0" + (1 + rnd.nextInt(8)) + "-28", 600_000 + rnd.nextInt(8) * 100_000, null});
        }
        jdbc.query("SELECT id, name, line_code, purchase_date, purchase_cost FROM eam_equipment ORDER BY id", rs -> {
            String line = rs.getString(3);
            String dept = line == null ? "EQP" : List.of("L1", "L2", "L6").contains(line) ? "WS1" : "WS2";
            assets.add(new Object[]{rs.getString(2), "MACHINE", dept, rs.getDate(4).toString(), rs.getBigDecimal(5).intValue(), rs.getLong(1)});
        });
        BigDecimal cost = BigDecimal.ZERO, acc = BigDecimal.ZERO;
        for (Object[] a : assets) {
            Map<String, Object> cat = jdbc.queryForMap("SELECT * FROM fa_category WHERE code = ?", a[1]);
            LocalDate in = LocalDate.parse((String) a[3]);
            BigDecimal c = BigDecimal.valueOf(((Number) a[4]).longValue());
            YearMonth first = DepreciationCalculator.firstPeriod(in);
            int months = (int) Math.max(0, (asOf.getYear() * 12L + asOf.getMonthValue()) - (first.getYear() * 12L + first.getMonthValue()) + 1);
            BigDecimal ad = DepreciationCalculator.plannedAccumulated((String) cat.get("method"), c, (BigDecimal) cat.get("residual_rate"),
                    ((Number) cat.get("useful_life_months")).intValue(), months);
            fa.register(new FaService.AssetReq((String) a[0], (String) a[1], (String) a[2], (Long) a[5], in, c), ad, YearMonth.from(asOf).toString(), "OPENING", asOf, null);
            cost = cost.add(c);
            acc = acc.add(ad);
        }
        gl.post(asOf, "ADJUST", "期初", "期初固定资产", List.of(Line.dr(GlService.FIXED_ASSET, cost, "固定资产原值"),
                Line.cr(GlService.ACC_DEPRECIATION, acc, "累计折旧"), Line.cr(GlService.CAPITAL, cost.subtract(acc), "实收资本")), null);
    }

    /** 年度预算：按日常报销规律估算月均，大多数类别留 5% 余量；业务招待费、研发材料费（2026）、夏季水电费偏紧，容易超预算 */
    private void createBudgets(LocalDate start) {
        Object[][] base = { // dept, category, monthly expected, tightness
                {"销售部", "差旅费", 0.9 * 21 * 4900, 1.05}, {"销售部", "业务招待费", 0.7 * 21 * 4250, 0.92}, {"销售部", "运输费", 0.95 * 21 * 14000, 1.05},
                {"采购部", "差旅费", 0.3 * 21 * 2800, 1.1}, {"生产部", "维修费", 0.5 * 21 * 15500, 1.05}, {"生产部", "办公费", 0.3 * 21 * 1600, 1.1},
                {"质量部", "差旅费", 0.35 * 21 * 3400, 1.1}, {"研发中心", "研发材料费", 0.6 * 21 * 23500, 1.05}, {"研发中心", "差旅费", 0.3 * 21 * 3400, 1.1},
                {"行政部", "办公费", 0.5 * 21 * 4150, 1.05}, {"财务部", "办公费", 0.2 * 21 * 1100, 1.1}, {"生产部", "水电费", 580_000, 1.0}};
        for (int y = start.getYear(); y <= 2026; y++) {
            for (int m = 1; m <= 12; m++) {
                if (y == start.getYear() && m < start.getMonthValue()) {
                    continue;
                }
                for (Object[] b : base) {
                    double amt = ((Number) b[2]).doubleValue() * ((Number) b[3]).doubleValue();
                    if ("研发材料费".equals(b[1]) && y >= 2026) {
                        amt *= 1.35; // 2026 预算只加 35%，实际投入加 60%
                    }
                    if ("水电费".equals(b[1])) {
                        amt = 580_000; // 按全年均值上浮编制（能源账单约 53 万 / 月），夏季空调负荷高的月份会超
                    }
                    treasury.setBudget(y, m, (String) b[0], (String) b[1], BigDecimal.valueOf(Math.round(amt / 1000) * 1000));
                }
                if (m == 4 || m == 10) {
                    treasury.setBudget(y, m, "销售部", "展会费", BigDecimal.valueOf(200_000));
                }
            }
        }
    }

    private void serviceContracts(LocalDate start) {
        long eqp = head("EQP"), adm = head("ADM"), it = head("IT"), log = head("LOG");
        Object[][] c = {{"设备年度维保（绕线机 / 嵌线机）", "田中精机 / 日特机械", 380_000, eqp}, {"冲床及模具维保", "扬力集团 / 会田", 260_000, eqp},
                {"物流运输服务", "德邦物流", 900_000, log}, {"IT 运维与云服务", "深圳市云桥信息技术有限公司", 220_000, it}, {"保安保洁服务", "深圳市安居物业管理有限公司", 480_000, adm}};
        for (Object[] x : c) {
            long id = contracts.draft(new ContractService.ContractReq("SERVICE", "OTHER", null, (String) x[1], (String) x[0], BigDecimal.valueOf((Integer) x[2]),
                    "CNY", "按季度结算", start.minusMonths(1), start.plusMonths(11).minusDays(1), true, null), (Long) x[3], start.minusMonths(1));
            jdbc.update("UPDATE ct_contract SET status = 'ACTIVE', sign_date = start_date WHERE id = ?", id); // 期初已生效的存量合同
        }
        jdbc.query("SELECT id, name FROM md_customer WHERE industry IN ('机器人', '工业自动化') ORDER BY id", rs -> {
            long id = contracts.draft(new ContractService.ContractReq("NDA", "CUSTOMER", rs.getLong(1), null, "保密协议（新品技术交流）", BigDecimal.ZERO, "CNY", null,
                    start.minusMonths(2), start.plusYears(3), false, null), head("SAL"), start.minusMonths(2));
            jdbc.update("UPDATE ct_contract SET status = 'ACTIVE', sign_date = start_date WHERE id = ?", id);
        });
    }

    // ================= 每日（日终） =================

    public void endOfDay(LocalDate d) {
        contracts.expire(d);
        customsFlow(d);
        capexEvents(d);
        if (d.getDayOfWeek() == java.time.DayOfWeek.FRIDAY) {
            treasury.payReimbursements(d);
        }
        if (d.getMonthValue() == 12 && d.getDayOfMonth() >= 8 && d.getDayOfMonth() <= 14 && d.getDayOfWeek() == java.time.DayOfWeek.MONDAY) {
            frameworkContracts(d);
        }
        if (d.getDayOfWeek() == java.time.DayOfWeek.WEDNESDAY) {
            jdbc.queryForList("SELECT id FROM ct_contract WHERE status = 'REJECTED'", Long.class).forEach(id -> contracts.submit(id, d.atTime(10, 0)));
        }
        YearMonth ym = YearMonth.from(d);
        if (d.equals(firstWorkdayFrom(ym.atDay(15)))) {
            String prev = ym.minusMonths(1).toString();
            if (!jdbc.queryForList("SELECT id FROM tax_vat_return WHERE period = ? AND paid_date IS NULL", Long.class, prev).isEmpty()) {
                customs.payVat(prev, d);
            }
        }
        for (List<String> ps : drain(refunds, d)) {
            ps.forEach(p -> customs.receiveRefund(p, d));
        }
        for (List<Long> ss : drain(pendingBooking, d)) {
            ss.forEach(s -> treasury.bookFromStatement(s, d));
        }
        if (d.equals(WorkCalendar.lastWorkday(ym))) {
            fa.depreciate(ym, d);
            Map<String, Object> ret = customs.vatReturn(ym, d);
            BigDecimal refund = (BigDecimal) ret.get("refund");
            if (refund.signum() > 0) {
                refunds.computeIfAbsent(d.plusDays(45 + rnd.nextInt(31)), k -> new ArrayList<>()).add(ym.toString());
            }
            bankFee(d, ym);
        }
        if (d.equals(firstWorkdayFrom(ym.atDay(21)))) {
            bankInterest(d, ym);
        }
        bankStatements(d);
        if (d.equals(WorkCalendar.lastWorkday(ym))) {
            treasury.reconcile("ICBC-BASIC", ym, 7);
        }
    }

    private void customsFlow(LocalDate d) {
        for (Long dn : jdbc.queryForList("""
                SELECT d.id FROM sal_delivery d JOIN md_customer c ON c.id = d.customer_id
                WHERE c.is_domestic = 0 AND d.delivery_date = ? AND NOT EXISTS (SELECT 1 FROM cu_declaration x WHERE x.delivery_id = d.id)""", Long.class, Date.valueOf(d))) {
            customs.declare(dn, d, rnd.nextDouble() < 0.7 ? "深圳蛇口" : "深圳盐田");
        }
        jdbc.queryForList("SELECT id FROM cu_declaration WHERE status = 'DECLARED' AND declare_date < ?", Long.class, Date.valueOf(d)).forEach(id -> customs.release(id, d));
        jdbc.queryForList("SELECT id FROM cu_declaration WHERE status = 'RELEASED' AND DATE_ADD(release_date, INTERVAL (1 + id % 4) DAY) <= ?", Long.class, Date.valueOf(d))
                .forEach(id -> customs.export(id, d));
    }

    private void capexEvents(LocalDate d) {
        Object[][] plan = { // date, name, category, dept, cost, inService
                {"2025-03-10", "伺服总装线二期（L5 扩产）", "MACHINE", "WS2", 2_800_000, "2025-05-06"},
                {"2025-10-13", "高速冲床备用模具（L8）", "MOLD", "WS2", 650_000, "2025-11-03"},
                {"2026-04-13", "伺服自动化测试线", "MACHINE", "WS2", 1_500_000, "2026-06-01"},
                {"2026-06-15", "研发 EMC 暗室改造", "MACHINE", "RND", 900_000, "2026-08-03"}};
        for (Object[] p : plan) {
            if (d.equals(LocalDate.parse((String) p[0]))) {
                fa.submitCapex(new FaService.AssetReq((String) p[1], (String) p[2], (String) p[3], null, LocalDate.parse((String) p[5]),
                        BigDecimal.valueOf((Integer) p[4])), head((String) p[3]), "产能 / 质量改善", d.atTime(9, 30));
            }
        }
        if (d.getDayOfMonth() <= 7 && d.getDayOfWeek() == java.time.DayOfWeek.THURSDAY && rnd.nextDouble() < 0.4) {
            String[] depts = {"IT", "RND", "ADM", "QA"};
            String dept = depts[rnd.nextInt(depts.length)];
            fa.submitCapex(new FaService.AssetReq("QA".equals(dept) ? "检测仪器" : "电脑及办公设备", "IT", dept, null, d.plusDays(10),
                    BigDecimal.valueOf(20_000 + rnd.nextInt(7) * 10_000)), head(dept), "日常更新", d.atTime(10, 0));
        }
        if (d.equals(LocalDate.of(2025, 12, 18))) {
            jdbc.queryForList("""
                    SELECT a.id FROM fa_asset a JOIN eam_equipment q ON q.id = a.equipment_id WHERE q.category = 'WINDING' AND a.status = 'IN_USE'
                    ORDER BY a.in_service_date LIMIT 2""", Long.class).forEach(id -> {
                fa.dispose(id, d, BigDecimal.valueOf(3000), BigDecimal.valueOf(500), "设备老化报废，由伺服二期新设备替代");
                jdbc.update("UPDATE eam_equipment SET status = 'SCRAPPED' WHERE id = (SELECT equipment_id FROM fa_asset WHERE id = ?)", id);
            });
        }
        if (d.equals(LocalDate.of(2026, 3, 20))) {
            jdbc.queryForList("SELECT id FROM fa_asset WHERE name = '商务车' AND status = 'IN_USE' ORDER BY in_service_date LIMIT 1", Long.class)
                    .forEach(id -> fa.dispose(id, d, BigDecimal.valueOf(150_000), BigDecimal.ZERO, "车辆更新，旧车出售"));
        }
    }

    /** 年底签订下一年度框架合同：客户按本年实际开票 × 增长预期，供应商按本年采购额 */
    private void frameworkContracts(LocalDate d) {
        int next = d.getYear() + 1;
        LocalDate from = d.minusYears(1);
        long sales = head("SAL"), pur = head("PUR");
        jdbc.queryForList("""
                SELECT c.id, c.industry, SUM(i.amount_cny) AS revenue FROM sal_invoice i JOIN md_customer c ON c.id = i.customer_id
                WHERE i.invoice_date > ? GROUP BY c.id, c.industry ORDER BY revenue DESC LIMIT 15""", Date.valueOf(from)).forEach(r -> {
            double growth = "机器人".equals(r.get("industry")) ? 1.35 : "工业自动化".equals(r.get("industry")) ? 1.15 : 1.0;
            BigDecimal amt = ((BigDecimal) r.get("revenue")).multiply(BigDecimal.valueOf(growth * 365.0 / Math.max(60, java.time.temporal.ChronoUnit.DAYS.between(
                    from.isBefore(LocalDate.of(2024, 10, 1)) ? LocalDate.of(2024, 10, 1) : from, d)))).setScale(-4, RoundingMode.HALF_UP);
            long id = contracts.draft(new ContractService.ContractReq("SALES", "CUSTOMER", ((Number) r.get("id")).longValue(), null, next + " 年度供货框架协议", amt,
                    "CNY", "月结，按客户账期", LocalDate.of(next, 1, 1), LocalDate.of(next, 12, 31), false, "年度采购量承诺"), sales, d);
            contracts.submit(id, d.atTime(10, 0));
        });
        jdbc.queryForList("""
                SELECT s.id, SUM(p.amount) AS amount FROM pur_invoice p JOIN md_supplier s ON s.id = p.supplier_id
                WHERE p.invoice_date > ? AND p.status IN ('OPEN','PARTIAL','PAID') GROUP BY s.id ORDER BY amount DESC LIMIT 8""", Date.valueOf(from)).forEach(r -> {
            long id = contracts.draft(new ContractService.ContractReq("PURCHASE", "SUPPLIER", ((Number) r.get("id")).longValue(), null, next + " 年度采购框架协议",
                    ((BigDecimal) r.get("amount")).setScale(-4, RoundingMode.HALF_UP), "CNY", "票到 60 天", LocalDate.of(next, 1, 1), LocalDate.of(next, 12, 31),
                    false, "价格按月度参考价联动"), pur, d);
            contracts.submit(id, d.atTime(11, 0));
        });
    }

    // ================= 银行流水 =================

    /** 当天银行存款分录生成银行流水：约 25% 的对外付款次 1~3 个工作日才出账（企业已付银行未付） */
    private void bankStatements(LocalDate d) {
        List<Map<String, Object>> entries = jdbc.queryForList("""
                SELECT e.id, e.debit - e.credit AS amount, v.source_type, v.summary FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE e.account_code = '1002' AND e.id > ? AND v.source_type <> 'BANK' ORDER BY e.id""", lastEntryId);
        for (Map<String, Object> e : entries) {
            long id = ((Number) e.get("id")).longValue();
            lastEntryId = Math.max(lastEntryId, id);
            BigDecimal amt = (BigDecimal) e.get("amount");
            boolean delay = amt.signum() < 0 && List.of("PAYMENT", "REIMBURSE", "FA").contains(e.get("source_type")) && rnd.nextDouble() < 0.25;
            LocalDate bankDate = delay ? firstWorkdayFrom(d.plusDays(1 + rnd.nextInt(3))) : d;
            delayedStatements.computeIfAbsent(bankDate, k -> new ArrayList<>()).add(new long[]{id});
        }
        for (List<long[]> list : drain(delayedStatements, d)) {
            for (long[] x : list) {
                Map<String, Object> e = jdbc.queryForMap("""
                        SELECT e.debit - e.credit AS amount, v.summary, v.voucher_no FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.id = ?""", x[0]);
                treasury.importStatement("ICBC-BASIC", d, (BigDecimal) e.get("amount"), null, (String) e.get("summary"), (String) e.get("voucher_no"));
            }
        }
    }

    private void bankInterest(LocalDate d, YearMonth ym) {
        BigDecimal bal = jdbc.queryForObject("SELECT COALESCE(SUM(amount), 0) FROM fin_bank_statement WHERE txn_date <= ?", BigDecimal.class, Date.valueOf(d));
        if (bal.signum() <= 0) {
            return;
        }
        BigDecimal interest = bal.multiply(new BigDecimal("0.0025")).divide(BigDecimal.valueOf(4), 2, RoundingMode.HALF_UP); // 活期 0.25%，按季结息
        if (ym.getMonthValue() % 3 != 0) {
            return;
        }
        long st = treasury.importStatement("ICBC-BASIC", d, interest, "中国工商银行", "季度结息", "INT-" + ym);
        pendingBooking.computeIfAbsent(nthWorkday(ym.plusMonths(1), 3), k -> new ArrayList<>()).add(st);
    }

    private void bankFee(LocalDate d, YearMonth ym) {
        BigDecimal fee = BigDecimal.valueOf(800 + rnd.nextInt(2500)).negate();
        long st = treasury.importStatement("ICBC-BASIC", d, fee, "中国工商银行", "网银及结算手续费", "FEE-" + ym);
        pendingBooking.computeIfAbsent(nthWorkday(ym.plusMonths(1), 3), k -> new ArrayList<>()).add(st);
    }

    // ================= 工具 =================

    private long head(String deptCode) {
        return jdbc.queryForObject("SELECT COALESCE(manager_emp_id, (SELECT manager_emp_id FROM org_dept WHERE code = 'GM')) FROM org_dept WHERE code = ?", Long.class, deptCode);
    }

    private static LocalDate firstWorkdayFrom(LocalDate d) {
        while (!WorkCalendar.isWorkday(d)) {
            d = d.plusDays(1);
        }
        return d;
    }

    private static LocalDate nthWorkday(YearMonth ym, int n) {
        LocalDate d = ym.atDay(1);
        int k = 0;
        while (true) {
            if (WorkCalendar.isWorkday(d) && ++k == n) {
                return d;
            }
            d = d.plusDays(1);
        }
    }

    private static <T> List<T> drain(TreeMap<LocalDate, T> map, LocalDate d) {
        List<T> out = new ArrayList<>();
        while (!map.isEmpty() && !map.firstKey().isAfter(d)) {
            out.add(map.pollFirstEntry().getValue());
        }
        return out;
    }
}
