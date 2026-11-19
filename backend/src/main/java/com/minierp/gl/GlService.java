package com.minierp.gl;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.PreparedStatement;
import java.sql.Statement;
import java.time.LocalDate;
import java.util.*;

/**
 * 总账：业务单据在各自事务内调用 post() 自动生成凭证（业财一体化），保证"有业务必有凭证、凭证必平衡"。
 * 金额为 0 的分录自动剔除；整张凭证为 0 时不生成。
 */
@Service
@RequiredArgsConstructor
public class GlService {
    public static final String BANK = "1002", NOTES_RECEIVABLE = "1121", AR = "1122", RAW = "1403", FG = "1405", TRANSIT = "1402", SEMI = "1411", PAYROLL_PAYABLE = "2211", IIT_PAYABLE = "222103", SOCIAL_PAYABLE = "224101",
            PENDING_LOSS = "1901", AP = "2202", AP_ESTIMATE = "220202", VAT_IN = "222101", VAT_OUT = "222102", OTHER_PAYABLE = "2241",
            CAPITAL = "4001", WIP = "5001", OVERHEAD = "5101", REVENUE = "6001", COGS = "6401", SELLING = "6601",
            ADMIN = "6602", FINANCE_EXP = "6603", RND = "6604", NON_OPERATING = "6711", TAX_SURCHARGE = "6403", ASSET_DISPOSAL = "6115",
            FIXED_ASSET = "1601", ACC_DEPRECIATION = "1602", FA_CLEARING = "1606", EXPORT_REFUND_RECEIVABLE = "1221",
            VAT_EXPORT_REFUND = "222104", VAT_UNPAID = "222105", VAT_EXPORT_OFFSET = "222106", SURTAX_PAYABLE = "222109",
            WARRANTY_PROVISION = "2801", OTHER_REVENUE = "6051", OTHER_COST = "6402";

    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private Set<String> accounts;
    private volatile Set<String> closedPeriods;

    public void refreshClosedPeriods() {
        closedPeriods = new HashSet<>(jdbc.queryForList("SELECT period FROM gl_period WHERE status = 'CLOSED'", String.class));
    }

    private boolean periodClosed(LocalDate date) {
        if (closedPeriods == null) {
            refreshClosedPeriods();
        }
        return closedPeriods.contains(java.time.YearMonth.from(date).toString());
    }

    private Set<String> accounts() {
        if (accounts == null) {
            accounts = new HashSet<>(jdbc.queryForList("SELECT code FROM gl_account", String.class));
        }
        return accounts;
    }

    @Transactional(propagation = Propagation.REQUIRED)
    public Long post(LocalDate date, String sourceType, String sourceNo, String summary, List<VoucherValidator.Line> raw,
                     String user) {
        List<VoucherValidator.Line> lines = raw.stream()
                .map(l -> new VoucherValidator.Line(l.account(), Money.amt(l.debit()), Money.amt(l.credit()), l.summary()))
                .filter(l -> l.debit().signum() != 0 || l.credit().signum() != 0).toList();
        if (lines.isEmpty()) {
            return null;
        }
        if (periodClosed(date)) {
            throw new BizException("会计期间 " + java.time.YearMonth.from(date) + " 已结账，不能记账（" + sourceType + " " + sourceNo + "）");
        }
        try {
            VoucherValidator.validate(lines, accounts());
        } catch (IllegalArgumentException e) {
            throw new BizException("凭证校验失败（" + sourceType + " " + sourceNo + "）：" + e.getMessage());
        }
        String no = docNo.next("JV", date);
        var keyHolder = new org.springframework.jdbc.support.GeneratedKeyHolder();
        jdbc.update(con -> {
            PreparedStatement ps = con.prepareStatement("INSERT INTO gl_voucher (voucher_no, voucher_date, source_type, source_no, summary, created_by) VALUES (?,?,?,?,?,?)",
                    Statement.RETURN_GENERATED_KEYS);
            ps.setString(1, no);
            ps.setDate(2, Date.valueOf(date));
            ps.setString(3, sourceType);
            ps.setString(4, sourceNo);
            ps.setString(5, summary.length() > 200 ? summary.substring(0, 200) : summary);
            ps.setString(6, user == null ? "system" : user);
            return ps;
        }, keyHolder);
        long vid = Objects.requireNonNull(keyHolder.getKey()).longValue();
        List<Object[]> rows = new ArrayList<>();
        int n = 1;
        for (VoucherValidator.Line l : lines) {
            rows.add(new Object[]{vid, n++, l.account(), l.debit(), l.credit(), l.summary() == null ? summary : l.summary()});
        }
        jdbc.batchUpdate("INSERT INTO gl_entry (voucher_id, line_no, account_code, debit, credit, summary) VALUES (?,?,?,?,?,?)", rows);
        return vid;
    }

    /** 手工凭证（财务录入），同样经过借贷平衡校验 */
    @Transactional
    public Long manual(LocalDate date, String summary, List<VoucherValidator.Line> lines, String user) {
        return post(date, "MANUAL", null, summary, lines, user);
    }

    /** 科目余额表：期初、本期借贷发生额、期末（按科目方向计算余额） */
    public Map<String, Object> trialBalance(LocalDate from, LocalDate to) {
        List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT a.code, a.name, a.category, a.direction,
                       COALESCE(SUM(CASE WHEN v.voucher_date < ? THEN e.debit - e.credit END), 0) AS opening_dr,
                       COALESCE(SUM(CASE WHEN v.voucher_date BETWEEN ? AND ? THEN e.debit END), 0) AS period_debit,
                       COALESCE(SUM(CASE WHEN v.voucher_date BETWEEN ? AND ? THEN e.credit END), 0) AS period_credit,
                       COALESCE(SUM(CASE WHEN v.voucher_date <= ? THEN e.debit - e.credit END), 0) AS closing_dr
                FROM gl_account a LEFT JOIN gl_entry e ON e.account_code = a.code LEFT JOIN gl_voucher v ON v.id = e.voucher_id
                GROUP BY a.code, a.name, a.category, a.direction ORDER BY a.code""", from, from, to, from, to, to);
        BigDecimal pd = BigDecimal.ZERO, pc = BigDecimal.ZERO, closingDr = BigDecimal.ZERO;
        for (Map<String, Object> r : rows) {
            boolean debitSide = "DEBIT".equals(r.get("direction"));
            BigDecimal open = (BigDecimal) r.get("opening_dr"), close = (BigDecimal) r.get("closing_dr");
            r.put("opening", debitSide ? open : open.negate());
            r.put("closing", debitSide ? close : close.negate());
            pd = pd.add((BigDecimal) r.get("period_debit"));
            pc = pc.add((BigDecimal) r.get("period_credit"));
            closingDr = closingDr.add(close);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("rows", rows);
        result.put("periodDebit", pd);
        result.put("periodCredit", pc);
        result.put("balanced", pd.compareTo(pc) == 0 && closingDr.signum() == 0);
        return result;
    }

    /** 利润表（简化）：收入 − 成本 − 费用 */
    public Map<String, Object> incomeStatement(LocalDate from, LocalDate to) {
        Map<String, BigDecimal> amt = new HashMap<>();
        jdbc.query("""
                SELECT e.account_code, SUM(e.credit - e.debit) AS net FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE v.voucher_date BETWEEN ? AND ? AND v.source_type <> 'CLOSING' AND e.account_code IN ('6001','6051','6115','6401','6402','6403','6601','6602','6603','6604','6711')
                GROUP BY e.account_code""", rs -> {
            amt.put(rs.getString(1), rs.getBigDecimal(2));
        }, from, to);
        BigDecimal otherRevenue = amt.getOrDefault(OTHER_REVENUE, BigDecimal.ZERO);
        BigDecimal otherCost = amt.getOrDefault(OTHER_COST, BigDecimal.ZERO).negate();
        // 营业收入 = 主营 + 其他业务收入（保外维修服务费）；营业成本同理
        BigDecimal revenue = amt.getOrDefault(REVENUE, BigDecimal.ZERO).add(otherRevenue);
        BigDecimal cogs = amt.getOrDefault(COGS, BigDecimal.ZERO).negate().add(otherCost);
        BigDecimal selling = amt.getOrDefault(SELLING, BigDecimal.ZERO).negate();
        BigDecimal admin = amt.getOrDefault(ADMIN, BigDecimal.ZERO).negate();
        BigDecimal rnd = amt.getOrDefault(RND, BigDecimal.ZERO).negate();
        BigDecimal other = amt.getOrDefault(NON_OPERATING, BigDecimal.ZERO).negate();
        BigDecimal taxes = amt.getOrDefault(TAX_SURCHARGE, BigDecimal.ZERO).negate();
        BigDecimal finance = amt.getOrDefault(FINANCE_EXP, BigDecimal.ZERO).negate();
        BigDecimal disposal = amt.getOrDefault(ASSET_DISPOSAL, BigDecimal.ZERO);
        BigDecimal gross = revenue.subtract(cogs);
        BigDecimal profit = gross.subtract(taxes).subtract(selling).subtract(admin).subtract(rnd).subtract(finance).add(disposal).subtract(other);
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("revenue", revenue);
        r.put("cogs", cogs);
        r.put("otherRevenue", otherRevenue);
        r.put("otherCost", otherCost);
        r.put("grossProfit", gross);
        r.put("sellingExpense", selling);
        r.put("adminExpense", admin);
        r.put("taxesAndSurcharges", taxes);
        r.put("financeExpense", finance);
        r.put("assetDisposalGain", disposal);
        r.put("rndExpense", rnd);
        r.put("nonOperating", other);
        r.put("totalProfit", profit);
        return r;
    }

    /** 总账与子账对账：存货（库存现存量×平均成本）、应收、应付、应付暂估（已入库未收票） */
    public List<Map<String, Object>> reconciliation() {
        return jdbc.queryForList("""
                SELECT a.code AS account_code, a.name AS account_name,
                       (SELECT COALESCE(SUM(CASE WHEN a.direction = 'DEBIT' THEN e.debit - e.credit ELSE e.credit - e.debit END), 0)
                          FROM gl_entry e WHERE e.account_code = a.code) AS gl_balance,
                       CASE a.code
                         WHEN '1402' THEN (SELECT COALESCE(SUM(l.amount), 0) FROM inv_transfer t JOIN inv_transfer_line l ON l.transfer_id = t.id
                                           WHERE t.status = 'SHIPPED')
                         WHEN '1403' THEN (SELECT COALESCE(ROUND(SUM(s.qty * s.avg_cost), 2), 0) FROM inv_stock s JOIN md_material m ON m.id = s.item_id
                                           WHERE s.item_type = 'MATERIAL' AND m.supply_type = 'BUY')
                         WHEN '1411' THEN (SELECT COALESCE(ROUND(SUM(s.qty * s.avg_cost), 2), 0) FROM inv_stock s JOIN md_material m ON m.id = s.item_id
                                           WHERE s.item_type = 'MATERIAL' AND m.supply_type = 'MAKE')
                         WHEN '1405' THEN (SELECT COALESCE(ROUND(SUM(qty * avg_cost), 2), 0) FROM inv_stock WHERE item_type = 'PRODUCT')
                         WHEN '1122' THEN (SELECT COALESCE(SUM(total_cny - received_cny), 0) FROM sal_invoice)
                         WHEN '1601' THEN (SELECT COALESCE(SUM(original_cost), 0) FROM fa_asset WHERE status = 'IN_USE')
                         WHEN '1602' THEN (SELECT COALESCE(SUM(accumulated_dep), 0) FROM fa_asset WHERE status = 'IN_USE')
                         WHEN '2241' THEN (SELECT COALESCE(SUM(amount), 0) FROM fin_expense WHERE status = 'APPROVED' AND paid_date IS NULL)
                         WHEN '1221' THEN (SELECT COALESCE(SUM(refund), 0) FROM tax_vat_return WHERE refund_received IS NULL)
                         WHEN '2801' THEN (SELECT COALESCE(SUM(amount), 0) FROM svc_warranty_ledger)
                         WHEN '2211' THEN (SELECT COALESCE(SUM(employer_cost), 0) FROM hr_payroll WHERE status = 'APPROVED')
                         WHEN '2202' THEN (SELECT COALESCE(SUM(total - paid), 0) FROM pur_invoice WHERE status IN ('OPEN','PARTIAL','PAID'))
                         WHEN '220202' THEN (SELECT COALESCE(ROUND(SUM(l.accepted_qty * l.unit_price), 2), 0) FROM pur_receipt g
                                             JOIN pur_receipt_line l ON l.gr_id = g.id WHERE g.status = 'COMPLETED')
                       END AS subledger_balance
                FROM gl_account a WHERE a.code IN ('1402', '1403', '1405', '1411', '1601', '1602', '1122', '1221', '2202', '220202', '2211', '2241', '2801') ORDER BY a.code""").stream().map(r -> {
            Map<String, Object> m = new LinkedHashMap<>(r);
            m.put("difference", ((BigDecimal) r.get("gl_balance")).subtract((BigDecimal) r.get("subledger_balance")));
            return m;
        }).toList();
    }

    /** 费用科目：销售部→销售费用，研发中心→研发费用，生产 / 质量 / 仓储等车间部门→制造费用，其余→管理费用 */
    public static String expenseAccount(String dept) {
        return switch (dept) {
            case "销售部", "外贸部", "客户服务部" -> SELLING;
            case "研发中心" -> RND;
            case "生产部", "质量部", "仓储物流部", "物流部", "家电电机车间", "智能驱控车间" -> OVERHEAD;
            default -> ADMIN;
        };
    }

    /** 人工成本科目（按部门 cost_type） */
    public static String laborAccount(String costType) {
        return switch (costType) {
            case "PRODUCTION" -> OVERHEAD;
            case "SALES" -> SELLING;
            case "RND" -> RND;
            default -> ADMIN;
        };
    }
}
