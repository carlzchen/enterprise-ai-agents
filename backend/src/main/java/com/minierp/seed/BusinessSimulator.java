package com.minierp.seed;

import com.minierp.common.BizException;
import com.minierp.finance.Expense;
import com.minierp.finance.FinanceService;
import com.minierp.inventory.InventoryService;
import com.minierp.production.ProductionService;
import com.minierp.production.WorkOrder;
import com.minierp.purchase.*;
import com.minierp.quality.Complaint;
import com.minierp.quality.QualityService;
import com.minierp.sales.*;
import com.minierp.seed.SeedCatalog.*;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.core.annotation.Order;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.YearMonth;
import java.time.temporal.ChronoUnit;
import java.util.*;

/**
 * 逐日模拟两年业务，全部通过业务 Service 完成，保证库存、成本、应收应付勾稽一致。
 * 埋入的业务故事（供 BI 与财务分析 Agent 归因）：
 * 1. 2026 年铜价上涨 20%+，罩极/贯流电机毛利率明显下滑，2026-06 起家电客户提价 3% 部分传导；
 * 2. 机器人、工业自动化客户需求快速增长，伺服电机收入占比提升；
 * 3. 佛山顺德宏达电器(C003) 2026 年起回款恶化，逾期累积后触发信用额度冻结；
 * 4. 宁波恒磁(S05) 2026-04~08 磁钢来料不良率上升，IQC 判退增多，无刷/伺服异响客诉增加；
 * 5. 伺服线 L5 2025-11~2026-01 编码器装配问题导致良率下滑；
 * 6. 铜线、磁钢按开票月"点价"结算（到货后 5~12 天开票），2025 下半年稀土、2026 铜价上涨带来正向采购价差，超 3% 的发票走财务审批；
 * 7. S05 磁钢判退导致 N42SH 缺料时，转子组件工单自动领用 N45SH 替代料；
 * 8. 华东前置仓（苏州）、香港出口仓按订货点法每周两次从深圳总仓补货，区域客户就近发货。
 * PLM 侧的 ECN / NPI 时间线见 PlmSimulation。
 */
@Slf4j
@Component
@Order(2)
@RequiredArgsConstructor
public class BusinessSimulator implements CommandLineRunner {
    private static final String[] SALESPEOPLE = {"王磊", "李娜", "张伟", "刘洋", "陈静", "杨帆"};
    private static final String[] BUYERS = {"周敏", "黄涛"};
    private static final String[] INSPECTORS = {"吴志刚", "郑凯"};
    private final SeedCatalog catalog;
    private final com.minierp.srm.SrmService srm;
    private final com.minierp.crm.CrmService crm;
    private final com.minierp.aps.ApsService aps;
    private final com.minierp.gl.PeriodCloseService periodClose;
    private final PlmSimulation plm;
    private final HrSimulation hr;
    private final OpsSimulation ops;
    private final DevSimulation dev;
    private final FinSimulation fin;
    private final SvcSimulation svc;
    private final EhsSimulation ehs;
    private Map<String, Double> lineLoss = Map.of();
    private final SalesService sales;
    private final PurchaseService purchase;
    private final ProductionService production;
    private final QualityService quality;
    private final FinanceService finance;
    private final InventoryService inventory;
    private final GoodsReceiptLineRepository grLines;
    private final com.minierp.transfer.TransferService transfers;
    private final JdbcTemplate jdbc;

    @Value("${minierp.seed.enabled}")
    private boolean enabled;
    @Value("${minierp.seed.start-date}")
    private LocalDate start;
    @Value("${minierp.seed.end-date}")
    private LocalDate end;
    @Value("${minierp.seed.random-seed}")
    private long randomSeed;

    private Random rnd;
    private Model model;
    private final Map<Long, ProdInfo> productById = new HashMap<>();
    private final Map<Long, CustInfo> customerById = new HashMap<>();
    /** 预估日需求（用于期初库存与安全库存） */
    private final Map<Long, Double> productDailyDemand = new HashMap<>();
    private final Map<Long, Double> materialDailyUsage = new HashMap<>();

    private final TreeMap<LocalDate, List<Long>> poArrivals = new TreeMap<>();
    private final TreeMap<LocalDate, List<Long>> customerPayments = new TreeMap<>();
    private final TreeMap<LocalDate, List<Long>> supplierPayments = new TreeMap<>();
    private final TreeMap<LocalDate, List<Complaint>> pendingComplaints = new TreeMap<>();
    private final TreeMap<LocalDate, List<Long[]>> complaintSteps = new TreeMap<>(); // {complaintId, 1=analyze / 2=close}
    private final Map<Long, Integer> complaintDefect = new HashMap<>();
    private final TreeMap<LocalDate, List<Long>> transferArrivals = new TreeMap<>();
    private final TreeMap<LocalDate, List<Long>> invoiceApprovals = new TreeMap<>();
    /** 供应商到货后 5~12 天开票：跨月点价更常见，开票前部分物料已被领用，价差会拆到成本 */
    private final TreeMap<LocalDate, List<Long>> pendingInvoices = new TreeMap<>();
    /** 半成品低阶码（1 = 直接用于成品，2 = 用于半成品） */
    private final Map<Long, Integer> semiLevel = new HashMap<>();
    private final Map<Long, Double> semiDailyUsage = new HashMap<>();

    @Override
    public void run(String... args) {
        Integer existing = jdbc.queryForObject("SELECT COUNT(*) FROM md_product", Integer.class);
        if (!enabled || existing != null && existing > 0) {
            return;
        }
        long t0 = System.currentTimeMillis();
        rnd = new Random(randomSeed);
        log.info("Seeding simulated business data {} ~ {} ...", start, end);
        model = catalog.build(start, end, rnd);
        model.products.forEach(p -> productById.put(p.id(), p));
        model.customers.forEach(c -> customerById.put(c.id(), c));
        model.semis.values().forEach(this::levelOf);
        estimateDemand();
        hr.init(start, rnd);
        ops.init(start, rnd);
        dev.init(start, rnd);
        fin.init(start, rnd);
        svc.init(start, rnd);
        ehs.init(start, rnd);
        openingBalances();
        plm.init(model, start, end, rnd);
        seedContacts();

        for (LocalDate d = start; !d.isAfter(end); d = d.plusDays(1)) {
            if (!isBusinessDay(d)) {
                continue;
            }
            simulateDay(d);
            if (d.getDayOfMonth() <= 7 && d.getDayOfWeek() == DayOfWeek.MONDAY) {
                log.info("  simulated up to {} ({}s)", d, (System.currentTimeMillis() - t0) / 1000);
            }
        }
        aps.run(end.plusDays(1), "prod01");
        log.info("Seeding finished in {}s", (System.currentTimeMillis() - t0) / 1000);
    }

    private void simulateDay(LocalDate d) {
        plm.onDay(d);
        collectCustomerPayments(d);
        paySuppliers(d);
        receiveSupplierInvoices(d);
        approveInvoices(d);
        receiveTransfers(d);
        receivePurchaseOrders(d);
        lineLoss = ops.beforeProduction(d);
        produce(d);
        ops.afterProduction(d);
        deliver(d);
        if (d.getDayOfWeek() == DayOfWeek.MONDAY || d.getDayOfWeek() == DayOfWeek.THURSDAY) {
            replenishDistributionCenters(d);
        }
        progressComplaints(d);
        createSalesOrders(d);
        cancelStaleDrafts(d);
        planProduction(d);
        planSubAssemblies(d);
        planMaterials(d);
        expenses(d);
        hr.onDay(d);
        ops.endOfDay(d);
        dev.onDay(d);
        svc.onDay(d);   // 售后 / IT：保外维修收入、质保金计提须在增值税月结之前
        ehs.onDay(d);
        fin.endOfDay(d);
        crmDaily(d);
        if (isFirstBusinessDayOfMonth(d)) {
            srm.computeMonthly(YearMonth.from(d).minusMonths(1));
            monthlyRfq(d);
            closePeriodsBefore(YearMonth.from(d));
        }
    }

    // ================= CRM =================

    private static final String[] TITLES = {"采购经理", "研发工程师", "质量经理", "项目经理", "总工程师"};
    private static final String[] SURNAMES = {"张", "李", "王", "刘", "陈", "杨", "黄", "赵", "周", "吴"};

    private void seedContacts() {
        for (CustInfo c : model.customers) {
            for (int i = 0; i < 2; i++) {
                String name = c.domestic() ? SURNAMES[rnd.nextInt(SURNAMES.length)] + (i == 0 ? "经理" : "工")
                        : (i == 0 ? "Purchasing Manager" : "R&D Engineer");
                crm.contact(c.id(), name, TITLES[i == 0 ? 0 : 1 + rnd.nextInt(4)], "0" + (755 + rnd.nextInt(200)) + "-" + (80000000 + rnd.nextInt(9999999)),
                        c.code().toLowerCase() + ".contact" + i + "@example.com", i == 0);
            }
        }
    }

    private record OpenOpp(long id, long customerId, String owner, int stageIdx) {
    }

    private final TreeMap<LocalDate, List<OpenOpp>> oppSteps = new TreeMap<>();

    private void crmDaily(LocalDate d) {
        double years = ChronoUnit.DAYS.between(start, d) / 365.0;
        if (rnd.nextDouble() < 0.22) {
            List<CustInfo> pool = model.customers.stream()
                    .filter(c -> !"家电".equals(c.industry()) || rnd.nextDouble() < 0.4).toList();
            CustInfo c = pool.get(rnd.nextInt(pool.size()));
            ProdInfo p = c.affinity().get(rnd.nextInt(c.affinity().size()));
            String series = p.series();
            double amount = avgQty(series) * p.listPrice() * (8 + rnd.nextInt(20)) * ("机器人".equals(c.industry()) ? 1 + years : 1);
            String owner = SALESPEOPLE[rnd.nextInt(SALESPEOPLE.length)];
            String title = String.format("%s %s 新项目配套（%s）", c.name().length() > 8 ? c.name().substring(0, 8) : c.name(),
                    SERIES_TITLE.getOrDefault(series, series), OPP_TOPICS[rnd.nextInt(OPP_TOPICS.length)]);
            long id = crm.create(c.id(), title, series, bd(amount, 2), d.plusDays(60 + rnd.nextInt(90)), owner, d);
            crm.activity(id, c.id(), "VISIT", "初次拜访，了解项目需求与年用量", owner, d);
            oppSteps.computeIfAbsent(d.plusDays(7 + rnd.nextInt(20)), k -> new ArrayList<>()).add(new OpenOpp(id, c.id(), owner, 0));
        }
        for (List<OpenOpp> list : drain(oppSteps, d)) {
            for (OpenOpp o : list) {
                if (rnd.nextDouble() < 0.18) {
                    crm.advance(o.id(), "LOST", LOST_REASONS[rnd.nextInt(LOST_REASONS.length)], d);
                    crm.activity(o.id(), o.customerId(), "CALL", "客户反馈未中标，记录丢单原因", o.owner(), d);
                    continue;
                }
                String next = com.minierp.crm.CrmService.STAGES.get(o.stageIdx() + 1);
                crm.advance(o.id(), next, null, d);
                crm.activity(o.id(), o.customerId(), STAGE_ACTIVITY.get(next)[0], STAGE_ACTIVITY.get(next)[1], o.owner(), d);
                if (!"WON".equals(next)) {
                    oppSteps.computeIfAbsent(d.plusDays(10 + rnd.nextInt(30)), k -> new ArrayList<>())
                            .add(new OpenOpp(o.id(), o.customerId(), o.owner(), o.stageIdx() + 1));
                }
            }
        }
    }

    private static final Map<String, String> SERIES_TITLE = Map.of("SERVO", "伺服电机", "STEPPER", "步进电机", "BLDC", "无刷电机",
            "SHADED_POLE", "罩极电机", "CROSSFLOW", "贯流风机", "GEAR", "减速电机");
    private static final String[] OPP_TOPICS = {"新平台导入", "年度框架协议", "替代进口", "二供导入", "新品试产"};
    private static final String[] LOST_REASONS = {"价格高于竞争对手 8%", "客户项目延期取消", "交期无法满足", "客户选择原供应商", "技术指标未达标（噪音）"};
    private static final Map<String, String[]> STAGE_ACTIVITY = Map.of(
            "QUALIFIED", new String[]{"MEETING", "技术交流，确认电压、转速、安装尺寸等关键参数"},
            "PROPOSAL", new String[]{"SAMPLE", "送样测试并提交报价方案"},
            "NEGOTIATION", new String[]{"QUOTE", "样品测试通过，进入价格与账期谈判"},
            "WON", new String[]{"VISIT", "签订框架协议，转入订单交付"});

    // ================= SRM 询价 =================

    private void monthlyRfq(LocalDate d) {
        List<String> cats = model.suppliersByCategory.entrySet().stream().filter(e -> e.getValue().size() >= 2).map(Map.Entry::getKey).sorted().toList();
        String cat = cats.get(rnd.nextInt(cats.size()));
        List<MatInfo> mats = model.materials.values().stream().filter(m -> m.category().equals(cat)).toList();
        MatInfo m = mats.get(rnd.nextInt(mats.size()));
        long rfq = srm.createRfq(m.id(), bd(roundUp(m.moq() * 2, 100), 3), d.plusDays(14), d, BUYERS[rnd.nextInt(BUYERS.length)]);
        for (SupInfo s : model.suppliersByCategory.get(cat)) {
            double price = materialPrice(m, d) * s.priceFactor() * (0.96 + rnd.nextDouble() * 0.08);
            srm.quote(rfq, s.id(), bd(price, 4), s.leadDays() + rnd.nextInt(5), d, null);
        }
        long winner = ((Number) srm.compare(rfq).get(0).get("supplier_id")).longValue();
        PurchaseOrder po = srm.award(rfq, winner, null, d, BUYERS[0]);
        poArrivals.computeIfAbsent(po.getExpectedDate(), k -> new ArrayList<>()).add(po.getId());
    }

    /** 月结：依次结账当前月之前所有未结期间（含期初建账所在月） */
    private void closePeriodsBefore(YearMonth current) {
        List<String> open = jdbc.queryForList("""
                SELECT DISTINCT DATE_FORMAT(voucher_date, '%Y-%m') AS p FROM gl_voucher
                WHERE DATE_FORMAT(voucher_date, '%Y-%m') < ? AND DATE_FORMAT(voucher_date, '%Y-%m') NOT IN
                  (SELECT period FROM gl_period WHERE status = 'CLOSED') ORDER BY p""", String.class, current.toString());
        for (String p : open) {
            periodClose.close(YearMonth.parse(p), "fin01");
        }
    }

    private static boolean isFirstBusinessDayOfMonth(LocalDate d) {
        for (LocalDate x = d.withDayOfMonth(1); x.isBefore(d); x = x.plusDays(1)) {
            if (isBusinessDay(x)) {
                return false;
            }
        }
        return true;
    }

    // ================= 期初 =================

    private void estimateDemand() {
        for (CustInfo c : model.customers) {
            double ordersPerDay = c.weight() * 0.15;
            for (ProdInfo p : c.affinity()) {
                double qty = avgQty(p.series()) * Math.sqrt(c.weight());
                productDailyDemand.merge(p.id(), ordersPerDay * 2.0 / c.affinity().size() * qty, Double::sum);
            }
        }
        for (ProdInfo p : model.products) {
            double dd = productDailyDemand.getOrDefault(p.id(), 0.0);
            p.bom().forEach((mid, q) -> addUsage(mid, dd * q));
        }
    }

    /** 需求逐层展开：半成品记入半成品日用量，并继续展开到外购件 */
    private void addUsage(long mid, double qty) {
        SemiInfo si = model.semis.get(mid);
        if (si == null) {
            materialDailyUsage.merge(mid, qty, Double::sum);
            return;
        }
        semiDailyUsage.merge(mid, qty, Double::sum);
        si.bom().forEach((child, q) -> addUsage(child, qty * q));
    }

    private int levelOf(SemiInfo si) {
        Integer cached = semiLevel.get(si.id());
        if (cached != null) {
            return cached;
        }
        int level = 1;
        for (SemiInfo parent : model.semis.values()) {
            if (parent.bom().containsKey(si.id())) {
                level = Math.max(level, levelOf(parent) + 1);
            }
        }
        semiLevel.put(si.id(), level);
        return level;
    }

    private void openingBalances() {
        LocalDate d = start.minusDays(1);
        for (MatInfo m : model.materials.values()) {
                double qty = Math.max(m.moq(), materialDailyUsage.getOrDefault(m.id(), 0.0) * 20);
                inventory.adjust(InventoryService.MATERIAL, m.id(), bd(roundUp(qty, "SPARE".equals(m.category()) ? 1 : 100), 3), bd(m.basePrice(), 4), d, "期初");
            }
            for (SemiInfo si : model.semis.values()) {
                double qty = Math.max(si.lot() * 0.5, semiDailyUsage.getOrDefault(si.id(), 0.0) * 5);
                inventory.adjust(InventoryService.MATERIAL, si.id(), bd(roundUp(qty, 10), 3), bd(si.unitCost(), 4), d, "期初");
            }
            for (ProdInfo p : model.products) {
                double qty = Math.max(p.lot() * 0.5, productDailyDemand.getOrDefault(p.id(), 0.0) * 7);
                double unitCost = p.listPrice() * 0.75;
                inventory.adjust(InventoryService.PRODUCT, p.id(), bd(roundUp(qty, 10), 3), bd(unitCost, 4), d, "期初");
            }
    }

    // ================= 销售 =================

    private void createSalesOrders(LocalDate d) {
        double season = new double[]{1.2, 0.6, 1.1, 1.15, 1.1, 1.0, 0.9, 0.9, 1.05, 0.9, 1.0, 1.1}[d.getMonthValue() - 1];
        double years = ChronoUnit.DAYS.between(start, d) / 365.0;
        for (CustInfo c : model.customers) {
            double trend = switch (c.industry()) {
                case "机器人" -> 1 + 0.6 * years;
                case "工业自动化" -> 1 + 0.25 * years;
                case "家电" -> d.getYear() >= 2026 ? 0.95 : 1.0;
                default -> 1.0;
            };
            int n = poisson(c.weight() * 0.15 * season * trend);
            for (int i = 0; i < n; i++) {
                createOrder(c, d);
            }
        }
    }

    private void createOrder(CustInfo c, LocalDate d) {
        int lines = 1 + rnd.nextInt(Math.min(3, c.affinity().size()));
        List<ProdInfo> picks = new ArrayList<>(c.affinity());
        Collections.shuffle(picks, rnd);
        BigDecimal fx = fxRate(c.currency(), d);
        List<SalesService.OrderLineReq> reqLines = new ArrayList<>();
        for (ProdInfo p : picks.subList(0, lines)) {
            double qty = avgQty(p.series()) * Math.sqrt(c.weight()) * (0.4 + rnd.nextDouble() * 1.2);
            double priceCny = p.listPrice() * c.discount() * priceDrift(c, p, d);
            reqLines.add(new SalesService.OrderLineReq(p.id(), bd(roundUp(qty, roundingOf(p.series())), 3),
                    BigDecimal.valueOf(priceCny).divide(fx, 4, RoundingMode.HALF_UP)));
        }
        SalesOrder o = sales.createOrder(new SalesService.CreateOrderReq(c.id(), d, d.plusDays(15 + rnd.nextInt(21)),
                SALESPEOPLE[rnd.nextInt(SALESPEOPLE.length)], null, reqLines));
        try {
            sales.confirm(o.getId());
        } catch (BizException creditBlocked) {
            // 超信用额度：订单停留在草稿，等待回款
        }
    }

    /** 年度价格谈判：2025 年初家电客户压价 2%；2026-06 起铜价上涨部分传导，家电提价 3% */
    private double priceDrift(CustInfo c, ProdInfo p, LocalDate d) {
        double f = 1.0;
        if (!d.isBefore(LocalDate.of(2025, 1, 1))) {
            f *= "家电".equals(c.industry()) ? 0.98 : 0.99;
        }
        if (!d.isBefore(LocalDate.of(2026, 6, 1)) && "家电".equals(c.industry())) {
            f *= 1.03;
        }
        return f;
    }

    private void cancelStaleDrafts(LocalDate d) {
        if (d.getDayOfWeek() != DayOfWeek.FRIDAY) {
            return;
        }
        List<Long> stale = jdbc.queryForList("SELECT id FROM sal_order WHERE status = 'DRAFT' AND order_date < ?",
                Long.class, d.minusDays(45));
        stale.forEach(sales::cancel);
    }

    /** 发货：区域客户优先从前置仓整单发货（前置仓不够则总仓发），库存按仓库分别计算 */
    private void deliver(LocalDate d) {
        long main = jdbc.queryForObject("SELECT id FROM md_warehouse WHERE type = 'FG'", Long.class);
        Map<String, Long> dcByRegion = new HashMap<>();
        jdbc.query("SELECT region, id FROM md_warehouse WHERE type = 'DC'", rs -> {
            dcByRegion.put(rs.getString(1), rs.getLong(2));
        });
        Map<Long, Map<Long, BigDecimal>> fg = new HashMap<>();
        jdbc.query("SELECT warehouse_id, item_id, qty FROM inv_stock WHERE item_type = 'PRODUCT'", rs -> {
            fg.computeIfAbsent(rs.getLong(1), k -> new HashMap<>()).put(rs.getLong(2), rs.getBigDecimal(3));
        });
        List<Map<String, Object>> open = jdbc.queryForList("""
                SELECT o.id AS order_id, o.customer_id, o.required_date, l.id AS line_id, l.product_id, (l.qty - l.shipped_qty) AS open_qty
                FROM sal_order o JOIN sal_order_line l ON l.order_id = o.id
                WHERE o.status IN ('CONFIRMED','PARTIAL') AND l.qty > l.shipped_qty AND o.required_date <= ?
                ORDER BY o.required_date, o.id, l.line_no""", d.plusDays(5));
        Map<Long, List<Map<String, Object>>> linesByOrder = new LinkedHashMap<>();
        open.forEach(r -> linesByOrder.computeIfAbsent(((Number) r.get("order_id")).longValue(), k -> new ArrayList<>()).add(r));
        for (var e : linesByOrder.entrySet()) {
            CustInfo c = customerById.get(((Number) e.getValue().get(0).get("customer_id")).longValue());
            Long dc = dcByRegion.get(c.domestic() ? regionOf(c) : "EXPORT");
            // 前置仓整单有货 → 前置仓发货；否则总仓按原规则（足量或严重逾期部分发货）
            long wh = main;
            if (dc != null) {
                Map<Long, BigDecimal> dcStock = fg.getOrDefault(dc, Map.of());
                boolean covered = e.getValue().stream().allMatch(r -> dcStock.getOrDefault(((Number) r.get("product_id")).longValue(), BigDecimal.ZERO)
                        .compareTo((BigDecimal) r.get("open_qty")) >= 0);
                if (covered) {
                    wh = dc;
                }
            }
            Map<Long, BigDecimal> stock = fg.computeIfAbsent(wh, k -> new HashMap<>());
            List<SalesService.ShipLineReq> lines = new ArrayList<>();
            for (Map<String, Object> r : e.getValue()) {
                long pid = ((Number) r.get("product_id")).longValue();
                BigDecimal need = (BigDecimal) r.get("open_qty");
                BigDecimal have = stock.getOrDefault(pid, BigDecimal.ZERO);
                LocalDate required = ((java.sql.Date) r.get("required_date")).toLocalDate();
                BigDecimal ship;
                if (have.compareTo(need) >= 0) {
                    ship = need;
                } else if (d.isAfter(required.plusDays(7)) && have.compareTo(need.multiply(new BigDecimal("0.3"))) >= 0) {
                    ship = have.setScale(0, RoundingMode.DOWN); // 严重逾期时部分发货
                } else {
                    continue;
                }
                if (ship.signum() <= 0) {
                    continue;
                }
                stock.put(pid, have.subtract(ship));
                lines.add(new SalesService.ShipLineReq(((Number) r.get("line_id")).longValue(), ship));
            }
            if (lines.isEmpty()) {
                continue;
            }
            Delivery dn = sales.ship(e.getKey(), new SalesService.ShipReq(d, lines, wh));
            ops.oqc(dn, d);
            SalesInvoice inv = sales.invoice(dn.getId(), d);
            customerPayments.computeIfAbsent(payDate(inv), k -> new ArrayList<>()).add(inv.getId());
            maybeComplaint(dn, d);
        }
    }

    private final Map<Long, String> customerRegion = new HashMap<>();

    private String regionOf(CustInfo c) {
        return customerRegion.computeIfAbsent(c.id(), id -> jdbc.queryForObject("SELECT region FROM md_customer WHERE id = ?", String.class, id));
    }

    /** 前置仓补货：按订货点建议生成调拨单，当天发出，在途天数后到货 */
    private void replenishDistributionCenters(LocalDate d) {
        for (Long id : transfers.createFromSuggestions(d, "wh01")) {
            transfers.ship(id, d);
            LocalDate expected = jdbc.queryForObject("SELECT expected_date FROM inv_transfer WHERE id = ?", java.sql.Date.class, id).toLocalDate();
            transferArrivals.computeIfAbsent(expected, k -> new ArrayList<>()).add(id);
        }
    }

    private void receiveTransfers(LocalDate d) {
        for (List<Long> ids : drain(transferArrivals, d)) {
            ids.forEach(id -> transfers.receive(id, d));
        }
    }

    private LocalDate payDate(SalesInvoice inv) {
        CustInfo c = customerById.get(inv.getCustomerId());
        int delay = switch (c.payProfile()) {
            case "LATE" -> 15 + rnd.nextInt(35);
            case "DETERIORATING" -> inv.getInvoiceDate().getYear() >= 2026 ? 70 + rnd.nextInt(90) : rnd.nextInt(10);
            default -> rnd.nextInt(18) - 5;
        };
        return inv.getDueDate().plusDays(delay);
    }

    private void collectCustomerPayments(LocalDate d) {
        for (List<Long> ids : drain(customerPayments, d)) {
            for (Long invoiceId : ids) {
                BigDecimal outstanding = jdbc.queryForObject(
                        "SELECT total_cny - received_cny FROM sal_invoice WHERE id = ?", BigDecimal.class, invoiceId);
                if (outstanding == null || outstanding.signum() <= 0) {
                    continue;
                }
                CustInfo c = customerById.get(jdbc.queryForObject("SELECT customer_id FROM sal_invoice WHERE id = ?", Long.class, invoiceId));
                String method = "ACCEPTANCE".equals(c.payProfile()) && rnd.nextDouble() < 0.6 ? "ACCEPTANCE" : "BANK";
                if (rnd.nextDouble() < 0.08) { // 少量分两次付款
                    BigDecimal half = outstanding.divide(BigDecimal.valueOf(2), 2, RoundingMode.HALF_UP);
                    sales.receive(invoiceId, d, half, method);
                    customerPayments.computeIfAbsent(d.plusDays(20 + rnd.nextInt(20)), k -> new ArrayList<>()).add(invoiceId);
                } else {
                    sales.receive(invoiceId, d, outstanding, method);
                }
            }
        }
    }

    // ================= 生产 =================

    private void planProduction(LocalDate d) {
        Map<Long, Double> openDemand = sumBy("""
                SELECT l.product_id, SUM(l.qty - l.shipped_qty) FROM sal_order o JOIN sal_order_line l ON l.order_id = o.id
                WHERE o.status IN ('CONFIRMED','PARTIAL') GROUP BY l.product_id""");
        // 只看总仓库存：前置仓的货专供本区域客户，不能调回，避免总仓客户订单"看得见用不着"
        Map<Long, Double> stock = sumBy("""
                SELECT s.item_id, s.qty FROM inv_stock s JOIN md_warehouse w ON w.id = s.warehouse_id
                WHERE s.item_type = 'PRODUCT' AND w.type = 'FG'""");
        Map<Long, Double> wip = sumBy("""
                SELECT product_id, SUM(planned_qty - good_qty - scrap_qty) FROM mes_work_order
                WHERE item_type = 'PRODUCT' AND status IN ('PLANNED','RELEASED','IN_PROGRESS') GROUP BY product_id""");
        for (ProdInfo p : model.products) {
            double safety = productDailyDemand.getOrDefault(p.id(), 0.0) * 3;
            double net = openDemand.getOrDefault(p.id(), 0.0) + safety - stock.getOrDefault(p.id(), 0.0) - wip.getOrDefault(p.id(), 0.0);
            if (net <= 0) {
                continue;
            }
            double qty = Math.ceil(net / p.lot()) * p.lot();
            WorkOrder wo = production.create(p.id(), bd(qty, 3), d, d.plusDays(3 + rnd.nextInt(4)));
            production.release(wo.getId());
        }
    }

    /**
     * 组件工单（MRP 逐层净需求）：按半成品低阶码从上到下，需求 = 未完工上级工单剩余数量 × 用量 + 2 天安全库存，
     * 净需求 = 需求 - 半成品库存 - 未完工组件工单，按经济批量下达，并挂到最早需要它的上级工单。
     */
    private void planSubAssemblies(LocalDate d) {
        int maxLevel = semiLevel.values().stream().max(Integer::compare).orElse(0);
        for (int level = 1; level <= maxLevel; level++) {
            Map<Long, Double> demand = sumBy("""
                    SELECT material_id, SUM(q) FROM (
                      SELECT b.material_id, (w.planned_qty - w.good_qty - w.scrap_qty) * b.qty_per AS q
                      FROM mes_work_order w JOIN md_bom b ON b.product_id = w.product_id
                      WHERE w.item_type = 'PRODUCT' AND w.status IN ('PLANNED','RELEASED','IN_PROGRESS')
                      UNION ALL
                      SELECT b.child_material_id, (w.planned_qty - w.good_qty - w.scrap_qty) * b.qty_per
                      FROM mes_work_order w JOIN md_material_bom b ON b.parent_material_id = w.material_id
                      WHERE w.item_type = 'MATERIAL' AND w.status IN ('PLANNED','RELEASED','IN_PROGRESS')) x GROUP BY material_id""");
            Map<Long, Double> stock = sumBy("SELECT item_id, SUM(qty) FROM inv_stock WHERE item_type = 'MATERIAL' GROUP BY item_id");
            Map<Long, Double> wip = sumBy("""
                    SELECT material_id, SUM(planned_qty - good_qty - scrap_qty) FROM mes_work_order
                    WHERE item_type = 'MATERIAL' AND status IN ('PLANNED','RELEASED','IN_PROGRESS') GROUP BY material_id""");
            for (SemiInfo si : model.semis.values()) {
                if (semiLevel.get(si.id()) != level) {
                    continue;
                }
                double net = demand.getOrDefault(si.id(), 0.0) + semiDailyUsage.getOrDefault(si.id(), 0.0) * 2
                        - stock.getOrDefault(si.id(), 0.0) - wip.getOrDefault(si.id(), 0.0);
                if (net <= 0) {
                    continue;
                }
                double qty = Math.ceil(net / si.lot()) * si.lot();
                Long parent = jdbc.queryForList("""
                        SELECT w.id FROM mes_work_order w
                        LEFT JOIN md_bom b ON w.item_type = 'PRODUCT' AND b.product_id = w.product_id AND b.material_id = ?
                        LEFT JOIN md_material_bom mb ON w.item_type = 'MATERIAL' AND mb.parent_material_id = w.material_id AND mb.child_material_id = ?
                        WHERE w.status IN ('PLANNED','RELEASED','IN_PROGRESS') AND (b.id IS NOT NULL OR mb.id IS NOT NULL)
                        ORDER BY w.plan_start, w.id LIMIT 1""", Long.class, si.id(), si.id()).stream().findFirst().orElse(null);
                WorkOrder wo = production.createSemi(si.id(), bd(qty, 3), d, d.plusDays(2), parent);
                production.release(wo.getId());
            }
        }
    }

    private void produce(LocalDate d) {
        List<Map<String, Object>> active = new ArrayList<>(jdbc.queryForList("""
                SELECT w.id, w.wo_no, w.item_type, w.product_id, w.material_id, w.line_code, (w.planned_qty - w.good_qty - w.scrap_qty) AS remaining
                FROM mes_work_order w WHERE w.status IN ('RELEASED','IN_PROGRESS') ORDER BY w.plan_start, w.id"""));
        // 先做组件（低阶码大的先做），当天完工的组件可供成品工单领用
        active.sort(Comparator.comparingInt((Map<String, Object> w) -> w.get("material_id") == null ? 0
                : -semiLevel.getOrDefault(((Number) w.get("material_id")).longValue(), 1)));
        for (Map<String, Object> w : active) {
            if ("MATERIAL".equals(w.get("item_type"))) {
                produceSemi(w, d);
                continue;
            }
            ProdInfo p = productById.get(((Number) w.get("product_id")).longValue());
            double remaining = ((BigDecimal) w.get("remaining")).doubleValue();
            // 设备停机按损失比例压低当天产出
            double output = Math.min(remaining, Math.round(p.lot() * (0.5 + rnd.nextDouble() * 0.6) * (1 - lineLoss.getOrDefault((String) w.get("line_code"), 0.0))));
            if (output <= 0) {
                continue;
            }
            String line = (String) w.get("line_code");
            double yield = yieldOf(p.series(), line, d);
            double scrap = Math.min(output, Math.round(output * (1 - yield) * (0.6 + rnd.nextDouble() * 0.8)));
            double good = output - scrap;
            try {
                production.report(((Number) w.get("id")).longValue(), d, bd(good, 3), bd(scrap, 3),
                        defectCode(p.series(), line, d), line + "-班组" + (char) ('A' + rnd.nextInt(3)));
            } catch (BizException shortage) {
                // 缺料，工单等待到货，产线拉缺料安灯
                ops.recordShortage(line, (String) w.get("wo_no"), d);
            }
        }
    }

    private void produceSemi(Map<String, Object> w, LocalDate d) {
        SemiInfo si = model.semis.get(((Number) w.get("material_id")).longValue());
        double remaining = ((BigDecimal) w.get("remaining")).doubleValue();
        double output = Math.min(remaining, Math.round(si.lot() * (0.6 + rnd.nextDouble() * 0.7) * (1 - lineLoss.getOrDefault((String) w.get("line_code"), 0.0))));
        if (output <= 0) {
            return;
        }
        double scrap = Math.min(output, Math.round(output * 0.004 * (0.5 + rnd.nextDouble())));
        try {
            production.report(((Number) w.get("id")).longValue(), d, bd(output - scrap, 3), bd(scrap, 3),
                    si.code().startsWith("SA-ST") ? "WINDING" : "APPEARANCE", w.get("line_code") + "-班组" + (char) ('A' + rnd.nextInt(2)));
        } catch (BizException shortage) {
            // 缺料（替代料也不足），组件工单等待到货
            ops.recordShortage((String) w.get("line_code"), (String) w.get("wo_no"), d);
        }
    }

    private double yieldOf(String series, String line, LocalDate d) {
        double base = switch (series) {
            case "SHADED_POLE" -> 0.992;
            case "CROSSFLOW" -> 0.99;
            case "BLDC" -> 0.985;
            case "STEPPER" -> 0.988;
            case "SERVO" -> 0.976;
            default -> 0.986;
        };
        if ("L5".equals(line) && between(d, "2025-11-01", "2026-01-31")) {
            base -= 0.045; // 编码器装配问题
        }
        if (("L3".equals(line) || "L5".equals(line)) && between(d, "2026-05-01", "2026-08-31")) {
            base -= 0.015; // 磁钢一致性问题
        }
        return base;
    }

    private String defectCode(String series, String line, LocalDate d) {
        if ("L5".equals(line) && between(d, "2025-11-01", "2026-01-31")) {
            return "ENCODER";
        }
        String[] codes = switch (series) {
            case "SERVO" -> new String[]{"ENCODER", "NOISE", "CURRENT"};
            case "BLDC" -> new String[]{"NOISE", "CURRENT", "SPEED"};
            case "STEPPER" -> new String[]{"NOISE", "WINDING", "APPEARANCE"};
            default -> new String[]{"WINDING", "NOISE", "APPEARANCE", "SPEED"};
        };
        return codes[rnd.nextInt(codes.length)];
    }

    // ================= 采购 =================

    private void planMaterials(LocalDate d) {
        Map<Long, Double> need = sumBy("""
                SELECT material_id, SUM(q) FROM (
                  SELECT b.material_id, (w.planned_qty - w.good_qty - w.scrap_qty) * b.qty_per AS q
                  FROM mes_work_order w JOIN md_bom b ON b.product_id = w.product_id
                  WHERE w.item_type = 'PRODUCT' AND w.status IN ('RELEASED','IN_PROGRESS')
                  UNION ALL
                  SELECT b.child_material_id, (w.planned_qty - w.good_qty - w.scrap_qty) * b.qty_per
                  FROM mes_work_order w JOIN md_material_bom b ON b.parent_material_id = w.material_id
                  WHERE w.item_type = 'MATERIAL' AND w.status IN ('RELEASED','IN_PROGRESS')) x GROUP BY material_id""");
        Map<Long, Double> stock = sumBy("SELECT item_id, SUM(qty) FROM inv_stock WHERE item_type = 'MATERIAL' GROUP BY item_id");
        Map<Long, Double> onOrder = sumBy("""
                SELECT l.material_id, SUM(l.qty - l.received_qty) FROM pur_order o JOIN pur_order_line l ON l.po_id = o.id
                WHERE o.status IN ('CONFIRMED','PARTIAL') GROUP BY l.material_id""");
        if (d.getDayOfWeek() == DayOfWeek.MONDAY) {
            // 每周按近 30 天实际耗用滚动更新日均用量
            sumBy("SELECT item_id, -SUM(qty) / 21 FROM inv_txn WHERE txn_type IN ('WO_ISSUE', 'EAM_ISSUE') AND txn_date > '" + d.minusDays(30) + "' GROUP BY item_id")
                    .forEach((mid, u) -> materialDailyUsage.merge(mid, u, Math::max));
        }
        Map<SupInfo, List<PurchaseService.PoLineReq>> bySupplier = new LinkedHashMap<>();
        for (MatInfo m : model.materials.values()) {
            double usage = materialDailyUsage.getOrDefault(m.id(), 0.0);
            double shortage = need.getOrDefault(m.id(), 0.0) + usage * 10
                    - stock.getOrDefault(m.id(), 0.0) - onOrder.getOrDefault(m.id(), 0.0);
            if (shortage <= 0) {
                continue;
            }
            double qty = roundUp(Math.max(Math.max(shortage, usage * 10), m.moq()), "SPARE".equals(m.category()) ? 1 : 100);
            SupInfo s = pickSupplier(m.category());
            double price = materialPrice(m, d) * s.priceFactor();
            bySupplier.computeIfAbsent(s, k -> new ArrayList<>())
                    .add(new PurchaseService.PoLineReq(m.id(), bd(qty, 3), bd(price, 4)));
        }
        for (var e : bySupplier.entrySet()) {
            SupInfo s = e.getKey();
            LocalDate eta = d.plusDays(s.leadDays() + rnd.nextInt(4) - 1);
            PurchaseOrder po = purchase.createOrder(new PurchaseService.CreatePoReq(s.id(), d, eta,
                    BUYERS[rnd.nextInt(BUYERS.length)], e.getValue()));
            purchase.confirm(po.getId());
            poArrivals.computeIfAbsent(eta, k -> new ArrayList<>()).add(po.getId());
        }
    }

    private void receivePurchaseOrders(LocalDate d) {
        for (List<Long> ids : drain(poArrivals, d)) {
            for (Long poId : ids) {
                List<PurchaseService.ReceiveLineReq> lines = jdbc.query(
                        "SELECT id, qty - received_qty FROM pur_order_line WHERE po_id = ? AND qty > received_qty",
                        (rs, i) -> new PurchaseService.ReceiveLineReq(rs.getLong(1), rs.getBigDecimal(2)), poId);
                if (lines.isEmpty()) {
                    continue;
                }
                GoodsReceipt gr = purchase.receive(poId, new PurchaseService.ReceiveReq(d, lines));
                SupInfo s = supplierOf(gr.getSupplierId());
                boolean anyAccepted = false;
                for (GoodsReceiptLine gl : grLines.findByGrId(gr.getId())) {
                    int sample = (int) Math.min(gl.getQty().doubleValue(), 80 + rnd.nextInt(46));
                    double p = s.baseDefect();
                    if ("S05".equals(s.code()) && between(d, "2026-04-01", "2026-08-31")) {
                        p = 0.035; // 磁钢供应商质量下滑
                    }
                    int defects = binomial(sample, p);
                    var q = quality.inspect(gl.getId(), d, sample, defects,
                            defects > 0 ? defectDesc(s.category()) : null, INSPECTORS[rnd.nextInt(INSPECTORS.length)]);
                    anyAccepted |= "PASS".equals(q.getResult());
                }
                if (anyAccepted) {
                    pendingInvoices.computeIfAbsent(d.plusDays(5 + rnd.nextInt(8)), k -> new ArrayList<>()).add(gr.getId());
                }
            }
        }
    }

    private void receiveSupplierInvoices(LocalDate d) {
        for (List<Long> ids : drain(pendingInvoices, d)) {
            for (Long grId : ids) {
                GoodsReceipt gr = jdbc.queryForObject("SELECT id, po_id FROM pur_receipt WHERE id = ?", (rs, i) -> {
                    GoodsReceipt g = new GoodsReceipt();
                    g.setId(rs.getLong(1));
                    g.setPoId(rs.getLong(2));
                    return g;
                }, grId);
                PurchaseInvoice inv = purchase.invoice(grId, d, invoicePrices(gr, d));
                if ("PENDING_APPROVAL".equals(inv.getStatus())) {
                    invoiceApprovals.computeIfAbsent(d.plusDays(1 + rnd.nextInt(3)), k -> new ArrayList<>()).add(inv.getId());
                } else {
                    supplierPayments.computeIfAbsent(inv.getDueDate().plusDays(rnd.nextInt(6)), k -> new ArrayList<>()).add(inv.getId());
                }
            }
        }
    }

    /**
     * 铜线、磁钢按开票月点价结算：发票价 = 订单价 × 开票月价格指数 / 下单月价格指数，
     * 发票价 = 订单价 × 开票月价格指数 / 下单月价格指数；价格上涨月份产生正向价差，跨月涨幅超过 3% 的发票需审批。
     */
    private Map<Long, BigDecimal> invoicePrices(GoodsReceipt gr, LocalDate d) {
        LocalDate orderDate = jdbc.queryForObject("SELECT order_date FROM pur_order WHERE id = ?", java.sql.Date.class, gr.getPoId()).toLocalDate();
        if (YearMonth.from(orderDate).equals(YearMonth.from(d))) {
            return Map.of();
        }
        Map<Long, BigDecimal> prices = new HashMap<>();
        for (GoodsReceiptLine gl : grLines.findByGrId(gr.getId())) {
            MatInfo m = model.materials.get(gl.getMaterialId());
            if (m == null || !("COPPER".equals(m.category()) || "MAGNET".equals(m.category()))) {
                continue;
            }
            double factor = SeedCatalog.priceIndex(m.category(), YearMonth.from(d)) / SeedCatalog.priceIndex(m.category(), YearMonth.from(orderDate));
            prices.put(gl.getId(), gl.getUnitPrice().multiply(BigDecimal.valueOf(factor)).setScale(4, RoundingMode.HALF_UP));
        }
        return prices;
    }

    /** 财务 1~3 天内审批价差发票；个别涨幅异常（>12%）的驳回，供应商按订单价重开 */
    private void approveInvoices(LocalDate d) {
        for (List<Long> ids : drain(invoiceApprovals, d)) {
            for (Long id : ids) {
                BigDecimal rate = jdbc.queryForObject("SELECT ABS(variance) / estimate_amount FROM pur_invoice WHERE id = ?", BigDecimal.class, id);
                PurchaseInvoice inv;
                if (rate != null && rate.compareTo(new BigDecimal("0.12")) > 0) {
                    purchase.reject(id, d, "cfo");
                    Long grId = jdbc.queryForObject("SELECT gr_id FROM pur_invoice WHERE id = ?", Long.class, id);
                    inv = purchase.invoice(grId, d);
                } else {
                    inv = purchase.approve(id, d, "cfo");
                }
                supplierPayments.computeIfAbsent(inv.getDueDate().plusDays(rnd.nextInt(6)), k -> new ArrayList<>()).add(inv.getId());
            }
        }
    }

    private void paySuppliers(LocalDate d) {
        for (List<Long> ids : drain(supplierPayments, d)) {
            for (Long invoiceId : ids) {
                BigDecimal outstanding = jdbc.queryForObject("SELECT total - paid FROM pur_invoice WHERE id = ?", BigDecimal.class, invoiceId);
                if (outstanding != null && outstanding.signum() > 0) {
                    purchase.pay(invoiceId, d, outstanding);
                }
            }
        }
    }

    // ================= 质量 =================

    private void maybeComplaint(Delivery dn, LocalDate d) {
        for (Map<String, Object> l : jdbc.queryForList("SELECT product_id, qty FROM sal_delivery_line WHERE delivery_id = ?", dn.getId())) {
            ProdInfo p = productById.get(((Number) l.get("product_id")).longValue());
            double prob = 0.012;
            String defect = null;
            if ("SERVO".equals(p.series()) && between(d, "2025-11-01", "2026-02-28")) {
                prob = 0.15;
                defect = "ENCODER";
            } else if (("BLDC".equals(p.series()) || "SERVO".equals(p.series())) && between(d, "2026-05-01", "2026-09-30")) {
                prob = 0.08;
                defect = "NOISE";
            }
            if (rnd.nextDouble() >= prob) {
                continue;
            }
            if (defect == null) {
                String[] types = {"NOISE", "NO_START", "OVERHEAT", "SPEED", "APPEARANCE"};
                defect = types[rnd.nextInt(types.length)];
            }
            double shipped = ((BigDecimal) l.get("qty")).doubleValue();
            Complaint c = new Complaint();
            c.setCustomerId(dn.getCustomerId());
            c.setProductId(p.id());
            c.setDeliveryId(dn.getId());
            c.setDefectType(defect);
            double affected = Math.max(1, Math.round(shipped * (0.002 + rnd.nextDouble() * 0.02)));
            c.setQtyAffected(bd(affected, 3));
            c.setSeverity(affected / shipped > 0.015 ? "HIGH" : affected / shipped > 0.006 ? "MEDIUM" : "LOW");
            c.setOwner(INSPECTORS[rnd.nextInt(INSPECTORS.length)]);
            LocalDate received = d.plusDays(7 + rnd.nextInt(24));
            c.setReceivedDate(received);
            pendingComplaints.computeIfAbsent(received, k -> new ArrayList<>()).add(c);
        }
    }

    private void progressComplaints(LocalDate d) {
        for (List<Complaint> list : drain(pendingComplaints, d)) {
            for (Complaint c : list) {
                c.setReceivedDate(d);
                Complaint saved = quality.createComplaint(c);
                complaintDefect.put(saved.getId(), defectIndex(c.getDefectType()));
                LocalDate analyze = d.plusDays(2 + rnd.nextInt(5));
                complaintSteps.computeIfAbsent(analyze, k -> new ArrayList<>()).add(new Long[]{saved.getId(), 1L});
                complaintSteps.computeIfAbsent(analyze.plusDays(7 + rnd.nextInt(25)), k -> new ArrayList<>()).add(new Long[]{saved.getId(), 2L});
            }
        }
        for (List<Long[]> steps : drain(complaintSteps, d)) {
            for (Long[] s : steps) {
                if (s[1] == 1L) {
                    quality.updateComplaint(s[0], "ANALYZING", null, null, d);
                } else {
                    String[] rc = ROOT_CAUSES[complaintDefect.get(s[0])];
                    quality.updateComplaint(s[0], "CLOSED", rc[0], rc[1], d);
                }
            }
        }
    }

    private static final String[] DEFECT_TYPES = {"NOISE", "NO_START", "OVERHEAT", "SPEED", "ENCODER", "APPEARANCE"};
    private static final String[][] ROOT_CAUSES = {
            {"磁钢批次充磁不均匀，气隙磁场不对称导致运行异响；部分批次轴承游隙超差", "要求供应商 8D 整改并加严磁通量抽检；来料 IQC 抽样由 80 提至 200；成品增加噪声测试工位"},
            {"绕组匝间短路/启动电容容量偏低，低压工况下无法启动", "绕线工序增加匝间耐压测试；启动电容来料加测容量；客户端批次全检替换"},
            {"漆包线漆膜在嵌线时损伤，局部匝间短路导致温升异常", "优化嵌线治具倒角；增加温升抽检频次；对在库成品按批次复检"},
            {"磁钢退磁/铁芯叠压不良导致转速偏离规格", "调整叠压参数并增加首件检验；磁钢来料增加退磁曲线抽测"},
            {"编码器插接件虚焊，振动环境下信号中断，驱动器报编码器故障", "编码器装配改用压接端子；L5 线增加振动老化测试；对 2025-11 至 2026-01 批次追溯召回检测"},
            {"包装防护不足，运输过程磕碰导致外观损伤", "包装增加 EPE 缓冲并通过跌落测试；更换物流承运商"},
    };

    private static int defectIndex(String type) {
        return Math.max(0, Arrays.asList(DEFECT_TYPES).indexOf(type));
    }

    private static String defectDesc(String category) {
        return switch (category) {
            case "MAGNET" -> "磁通量偏低/尺寸超差";
            case "COPPER" -> "漆膜针孔";
            case "STEEL" -> "毛刺超标";
            case "BEARING" -> "游隙超差";
            case "PLASTIC" -> "缩水/飞边";
            case "PCB", "ENCODER" -> "功能测试不良";
            default -> "尺寸不良";
        };
    }

    // ================= 费用 =================

    private void expenses(LocalDate d) {
        Object[][] daily = {
                // dept, category, min, max, probability per day
                {"销售部", "差旅费", 800, 9000, 0.9}, {"销售部", "业务招待费", 500, 8000, 0.7}, {"销售部", "运输费", 3000, 25000, 0.95},
                {"采购部", "差旅费", 600, 5000, 0.3}, {"生产部", "维修费", 1000, 30000, 0.5}, {"生产部", "办公费", 200, 3000, 0.3},
                {"质量部", "差旅费", 800, 6000, 0.35}, {"研发中心", "研发材料费", 2000, 45000, 0.6}, {"研发中心", "差旅费", 800, 6000, 0.3},
                {"行政部", "办公费", 300, 8000, 0.5}, {"财务部", "办公费", 200, 2000, 0.2},
        };
        for (Object[] e : daily) {
            double p = (Double) e[4];
            double scale = 1.0;
            if ("研发材料费".equals(e[1]) && d.getYear() >= 2026) {
                scale = 1.6; // 2026 年加大灵巧手/关节模组研发投入
            }
            if (rnd.nextDouble() < p) {
                submitExpense(d, (String) e[0], (String) e[1], ((Integer) e[2] + rnd.nextDouble() * ((Integer) e[3] - (Integer) e[2])) * scale, null);
            }
        }
        // 水电费由能源管理按电表读数出账后提交（EhsSimulation.payBill）
        // 4 月、10 月展会（广交会 / 汉诺威工博会）
        if ((d.getMonthValue() == 4 || d.getMonthValue() == 10) && d.getDayOfMonth() <= 5 && rnd.nextDouble() < 0.5) {
            submitExpense(d, "销售部", "展会费", 80_000 + rnd.nextDouble() * 170_000, "展会展位及搭建费");
        }
    }

    /** 报销人从该部门在职职员中选；走 OA 报销审批流（直属上级 → 部门负责人 ≥5000 → 财务 → 总经理 ≥50000） */
    private void submitExpense(LocalDate d, String dept, String category, double amount, String desc) {
        Long emp = hr.pickEmployee(dept);
        Expense e = new Expense();
        e.setExpenseDate(d);
        e.setDept(dept);
        e.setCategory(category);
        e.setAmount(bd(amount, 2));
        e.setEmployee(emp == null ? "行政部" : hr.nameOf(emp));
        e.setDescription(desc != null ? desc : category);
        finance.submit(e, emp, d.atTime(9 + rnd.nextInt(3), rnd.nextInt(60)));
    }

    // ================= 工具 =================

    private SupInfo pickSupplier(String category) {
        List<SupInfo> list = model.suppliersByCategory.get(category);
        double r = rnd.nextDouble(), acc = 0;
        for (SupInfo s : list) {
            acc += s.share();
            if (r < acc) {
                return s;
            }
        }
        return list.get(0);
    }

    private SupInfo supplierOf(long supplierId) {
        return model.suppliersByCategory.values().stream().flatMap(List::stream)
                .filter(s -> s.id() == supplierId).findFirst().orElseThrow();
    }

    private double materialPrice(MatInfo m, LocalDate d) {
        return m.basePrice() * SeedCatalog.priceIndex(m.category(), YearMonth.from(d));
    }

    private BigDecimal fxRate(String currency, LocalDate d) {
        if ("CNY".equals(currency)) {
            return BigDecimal.ONE;
        }
        return jdbc.queryForObject("SELECT rate FROM fin_fx_rate WHERE currency = ? AND rate_month = ?",
                BigDecimal.class, currency, d.withDayOfMonth(1));
    }

    private Map<Long, Double> sumBy(String sql) {
        Map<Long, Double> m = new HashMap<>();
        jdbc.query(sql, rs -> {
            m.put(rs.getLong(1), rs.getDouble(2));
        });
        return m;
    }

    /** 取出并移除 <= d 的所有计划事项 */
    private static <T> List<T> drain(TreeMap<LocalDate, T> map, LocalDate d) {
        List<T> out = new ArrayList<>();
        while (!map.isEmpty() && !map.firstKey().isAfter(d)) {
            out.add(map.pollFirstEntry().getValue());
        }
        return out;
    }

    private static double avgQty(String series) {
        return switch (series) {
            case "SHADED_POLE" -> 6000;
            case "CROSSFLOW" -> 2000;
            case "BLDC" -> 1500;
            case "STEPPER" -> 800;
            case "SERVO" -> 300;
            default -> 1500;
        };
    }

    private static double roundingOf(String series) {
        return switch (series) {
            case "SHADED_POLE" -> 500;
            case "SERVO" -> 10;
            default -> 100;
        };
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

    private int binomial(int n, double p) {
        int k = 0;
        for (int i = 0; i < n; i++) {
            if (rnd.nextDouble() < p) {
                k++;
            }
        }
        return k;
    }

    private static boolean isBusinessDay(LocalDate d) {
        return com.minierp.common.WorkCalendar.isWorkday(d);
    }

    private static boolean between(LocalDate d, String from, String to) {
        return !d.isBefore(LocalDate.parse(from)) && !d.isAfter(LocalDate.parse(to));
    }


    private static double roundUp(double v, double step) {
        return Math.ceil(v / step) * step;
    }

    private static BigDecimal bd(double v, int scale) {
        return BigDecimal.valueOf(v).setScale(scale, RoundingMode.HALF_UP);
    }
}
