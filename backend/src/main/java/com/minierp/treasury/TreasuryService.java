package com.minierp.treasury;

import com.minierp.common.BizException;
import com.minierp.common.NotFoundException;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

/**
 * 资金：银行流水导入与对账（余额调节表）、依据银行回单入账（利息 / 手续费，入账时即与流水匹配）、报销集中付款、13 周资金预测；
 * 预算：按月、部门、费用类别控制报销，超预算的报销流程增加总经理审批。
 */
@Service
@RequiredArgsConstructor
public class TreasuryService {
    private final JdbcTemplate jdbc;
    private final GlService gl;

    // ---------- 银行 ----------

    public long accountId(String code) {
        return jdbc.queryForList("SELECT id FROM fin_bank_account WHERE code = ?", Long.class, code).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("银行账户", code));
    }

    @Transactional
    public long importStatement(String accountCode, LocalDate date, BigDecimal amount, String counterparty, String memo, String bankRef) {
        jdbc.update("INSERT INTO fin_bank_statement (account_id, txn_date, amount, counterparty, memo, bank_ref) VALUES (?,?,?,?,?,?)",
                accountId(accountCode), Date.valueOf(date), amount, counterparty, memo, bankRef);
        return jdbc.queryForObject("SELECT MAX(id) FROM fin_bank_statement", Long.class);
    }

    /** 依据银行回单入账（利息收入冲减财务费用、手续费计入财务费用），凭证分录直接与该流水匹配 */
    @Transactional
    public void bookFromStatement(long statementId, LocalDate postDate) {
        Map<String, Object> s = jdbc.queryForList("SELECT * FROM fin_bank_statement WHERE id = ?", statementId).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("银行流水", statementId));
        if (s.get("matched_entry_id") != null) {
            throw new BizException("该流水已入账");
        }
        BigDecimal amt = (BigDecimal) s.get("amount");
        String ref = "BK-" + statementId;
        if (amt.signum() > 0) {
            gl.post(postDate, "BANK", ref, "银行回单：" + s.get("memo"), List.of(Line.dr(GlService.BANK, amt, "利息收入"),
                    Line.cr(GlService.FINANCE_EXP, amt, "利息收入冲减财务费用")), null);
        } else {
            gl.post(postDate, "BANK", ref, "银行回单：" + s.get("memo"), List.of(Line.dr(GlService.FINANCE_EXP, amt.negate(), "银行手续费"),
                    Line.cr(GlService.BANK, amt.negate(), "银行扣费")), null);
        }
        Long entry = jdbc.queryForObject("""
                SELECT e.id FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE v.source_no = ? AND e.account_code = ? ORDER BY e.id DESC LIMIT 1""",
                Long.class, ref, GlService.BANK);
        jdbc.update("UPDATE fin_bank_statement SET matched_entry_id = ? WHERE id = ?", entry, statementId);
    }

    /**
     * 月末银行对账：已人工 / 回单匹配的流水先确定，其余按金额 + 日期窗口自动匹配（写回匹配结果），输出余额调节表与未达账项。
     */
    @Transactional
    public Map<String, Object> reconcile(String accountCode, YearMonth ym, int windowDays) {
        long acc = accountId(accountCode);
        Date end = Date.valueOf(ym.atEndOfMonth());
        BigDecimal bookBal = jdbc.queryForObject("""
                SELECT COALESCE(SUM(e.debit - e.credit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE e.account_code = ? AND v.voucher_date <= ?""", BigDecimal.class, GlService.BANK, end);
        BigDecimal bankBal = jdbc.queryForObject("SELECT COALESCE(SUM(amount), 0) FROM fin_bank_statement WHERE account_id = ? AND txn_date <= ?",
                BigDecimal.class, acc, end);
        // 截至期末口径：已匹配但另一方日期在期末之后的，仍是本期未达账项，且不再参与自动匹配
        List<BankReconciler.Item> fixedBookOnly = jdbc.query("""
                SELECT e.id, v.voucher_date, e.debit - e.credit FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                JOIN fin_bank_statement s ON s.matched_entry_id = e.id WHERE e.account_code = ? AND v.voucher_date <= ? AND s.txn_date > ?""",
                (rs, i) -> new BankReconciler.Item(rs.getLong(1), rs.getDate(2).toLocalDate(), rs.getBigDecimal(3)), GlService.BANK, end, end);
        List<BankReconciler.Item> fixedBankOnly = jdbc.query("""
                SELECT s.id, s.txn_date, s.amount FROM fin_bank_statement s JOIN gl_entry e ON e.id = s.matched_entry_id JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE s.account_id = ? AND s.txn_date <= ? AND v.voucher_date > ?""",
                (rs, i) -> new BankReconciler.Item(rs.getLong(1), rs.getDate(2).toLocalDate(), rs.getBigDecimal(3)), acc, end, end);
        List<BankReconciler.Item> book = jdbc.query("""
                SELECT e.id, v.voucher_date, e.debit - e.credit, v.summary FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE e.account_code = ? AND v.voucher_date <= ? AND NOT EXISTS (SELECT 1 FROM fin_bank_statement s WHERE s.matched_entry_id = e.id)""",
                (rs, i) -> new BankReconciler.Item(rs.getLong(1), rs.getDate(2).toLocalDate(), rs.getBigDecimal(3), rs.getString(4)), GlService.BANK, end);
        List<BankReconciler.Item> bank = jdbc.query("""
                SELECT id, txn_date, amount, memo FROM fin_bank_statement WHERE account_id = ? AND txn_date <= ? AND matched_entry_id IS NULL""",
                (rs, i) -> new BankReconciler.Item(rs.getLong(1), rs.getDate(2).toLocalDate(), rs.getBigDecimal(3), rs.getString(4)), acc, end);
        BankReconciler.Result auto = BankReconciler.reconcile(book, bank, windowDays, bookBal, bankBal);
        jdbc.batchUpdate("UPDATE fin_bank_statement SET matched_entry_id = ? WHERE id = ?",
                auto.matches().stream().map(m -> new Object[]{m.bookId(), m.bankId()}).toList());
        List<BankReconciler.Item> bookOnly = new ArrayList<>(auto.bookOnly());
        bookOnly.addAll(fixedBookOnly);
        List<BankReconciler.Item> bankOnly = new ArrayList<>(auto.bankOnly());
        bankOnly.addAll(fixedBankOnly);
        BigDecimal adjBook = bookBal.add(bankOnly.stream().map(BankReconciler.Item::amount).reduce(BigDecimal.ZERO, BigDecimal::add));
        BigDecimal adjBank = bankBal.add(bookOnly.stream().map(BankReconciler.Item::amount).reduce(BigDecimal.ZERO, BigDecimal::add));
        BankReconciler.Result r = new BankReconciler.Result(auto.matches(), bookOnly, bankOnly, adjBook, adjBank, adjBook.compareTo(adjBank) == 0);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("account", accountCode);
        out.put("period", ym.toString());
        out.put("bookBalance", bookBal);
        out.put("bankBalance", bankBal);
        out.put("matched", r.matches().size());
        out.put("bankReceivedNotBooked", sum(r.bankOnly(), true));
        out.put("bankPaidNotBooked", sum(r.bankOnly(), false));
        out.put("bookReceivedNotBank", sum(r.bookOnly(), true));
        out.put("bookPaidNotBank", sum(r.bookOnly(), false));
        out.put("adjustedBook", r.adjustedBook());
        out.put("adjustedBank", r.adjustedBank());
        out.put("balanced", r.balanced());
        out.put("bookOnly", describeBook(r.bookOnly()));
        out.put("bankOnly", describeBank(r.bankOnly()));
        return out;
    }

    private static BigDecimal sum(List<BankReconciler.Item> items, boolean receipts) {
        return items.stream().map(BankReconciler.Item::amount).filter(a -> receipts == a.signum() > 0).reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    private List<Map<String, Object>> describeBook(List<BankReconciler.Item> items) {
        return items.stream().limit(100).map(i -> jdbc.queryForMap("""
                SELECT v.voucher_no, v.voucher_date, v.summary, e.debit - e.credit AS amount FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.id = ?""",
                i.id())).toList();
    }

    private List<Map<String, Object>> describeBank(List<BankReconciler.Item> items) {
        return items.stream().limit(100).map(i -> jdbc.queryForMap("SELECT id, txn_date, amount, counterparty, memo FROM fin_bank_statement WHERE id = ?", i.id())).toList();
    }

    // ---------- 报销付款 ----------

    /** 已审批未付款的报销集中付款：Dr 其他应付款 / Cr 银行存款 */
    @Transactional
    public BigDecimal payReimbursements(LocalDate date) {
        List<Map<String, Object>> rows = jdbc.queryForList("SELECT id, amount FROM fin_expense WHERE status = 'APPROVED' AND paid_date IS NULL");
        BigDecimal total = rows.stream().map(r -> (BigDecimal) r.get("amount")).reduce(BigDecimal.ZERO, BigDecimal::add);
        if (total.signum() <= 0) {
            return BigDecimal.ZERO;
        }
        gl.post(date, "REIMBURSE", "RB-" + date, "报销集中付款（" + rows.size() + " 笔）", List.of(
                Line.dr(GlService.OTHER_PAYABLE, total, "支付报销款"), Line.cr(GlService.BANK, total, "银行付款")), null);
        jdbc.batchUpdate("UPDATE fin_expense SET paid_date = ? WHERE id = ?", rows.stream().map(r -> new Object[]{Date.valueOf(date), r.get("id")}).toList());
        return total;
    }

    // ---------- 资金预测 ----------

    /** 13 周资金预测：应收按客户历史平均逾期天数折算回款日；应付按到期日；工资按上月人工成本在 10 日发放；未交增值税 15 日缴纳；应收退税 60 天到账 */
    public Map<String, Object> forecast(LocalDate start, int weeks, BigDecimal safety) {
        BigDecimal opening = jdbc.queryForObject("""
                SELECT COALESCE(SUM(e.debit - e.credit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.account_code = ? AND v.voucher_date < ?""",
                BigDecimal.class, GlService.BANK, Date.valueOf(start));
        Map<Long, Double> delay = new HashMap<>();
        jdbc.query("""
                SELECT i.customer_id, AVG(DATEDIFF(r.receipt_date, i.due_date)) FROM sal_receipt r JOIN sal_invoice i ON i.id = r.invoice_id
                WHERE r.receipt_date > ? GROUP BY i.customer_id""", rs -> {
            delay.put(rs.getLong(1), rs.getDouble(2));
        }, Date.valueOf(start.minusMonths(6)));
        List<CashForecaster.Flow> flows = new ArrayList<>();
        jdbc.query("SELECT customer_id, due_date, total_cny - received_cny FROM sal_invoice WHERE total_cny > received_cny", rs -> {
            flows.add(new CashForecaster.Flow(CashForecaster.expectedDate(rs.getDate(2).toLocalDate(), delay.getOrDefault(rs.getLong(1), 0.0), start),
                    rs.getBigDecimal(3), "客户回款"));
        });
        jdbc.query("SELECT due_date, total - paid FROM pur_invoice WHERE status IN ('OPEN','PARTIAL')", rs -> {
            LocalDate due = rs.getDate(1).toLocalDate();
            flows.add(new CashForecaster.Flow(due.isBefore(start) ? start : due, rs.getBigDecimal(2).negate(), "供应商付款"));
        });
        BigDecimal payroll = jdbc.queryForList("SELECT employer_cost FROM hr_payroll ORDER BY period DESC LIMIT 1", BigDecimal.class).stream().findFirst().orElse(BigDecimal.ZERO);
        BigDecimal reimb = jdbc.queryForObject("SELECT COALESCE(SUM(amount), 0) FROM fin_expense WHERE status = 'APPROVED' AND paid_date IS NULL", BigDecimal.class);
        BigDecimal monthlyExpense = jdbc.queryForObject("SELECT COALESCE(SUM(amount), 0) / 3 FROM fin_expense WHERE expense_date > ? AND expense_date <= ?",
                BigDecimal.class, Date.valueOf(start.minusMonths(3)), Date.valueOf(start));
        flows.add(new CashForecaster.Flow(start, reimb.negate(), "报销付款"));
        for (int m = 0; m <= weeks / 4 + 1; m++) {
            LocalDate first = start.withDayOfMonth(1).plusMonths(m);
            if (!first.plusDays(9).isBefore(start)) {
                flows.add(new CashForecaster.Flow(first.plusDays(9), payroll.negate(), "工资社保"));
            }
            if (m > 0) {
                flows.add(new CashForecaster.Flow(first.plusDays(14), monthlyExpense.negate(), "日常费用"));
            }
        }
        jdbc.query("SELECT period, payable + surtax FROM tax_vat_return WHERE paid_date IS NULL AND payable + surtax > 0", rs -> {
            flows.add(new CashForecaster.Flow(YearMonth.parse(rs.getString(1)).plusMonths(1).atDay(15), rs.getBigDecimal(2).negate(), "税款"));
        });
        jdbc.query("SELECT period, refund FROM tax_vat_return WHERE refund > 0 AND refund_received IS NULL", rs -> {
            flows.add(new CashForecaster.Flow(YearMonth.parse(rs.getString(1)).atEndOfMonth().plusDays(60), rs.getBigDecimal(2), "出口退税"));
        });
        CashForecaster.Forecast f = CashForecaster.weekly(start, weeks, opening, flows, safety);
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("start", start);
        r.put("opening", opening);
        r.put("safety", safety);
        r.put("weeks", f.weeks());
        r.put("minBalance", f.minBalance());
        r.put("minWeek", f.minWeek());
        r.put("firstBelowSafety", f.firstBelowSafety());
        return r;
    }

    // ---------- 预算 ----------

    public BudgetControl.Check checkBudget(String dept, String category, LocalDate date, BigDecimal amount) {
        List<Map<String, Object>> b = jdbc.queryForList("SELECT * FROM v_budget_usage WHERE year = ? AND month = ? AND dept = ? AND category = ?",
                date.getYear(), date.getMonthValue(), dept, category);
        if (b.isEmpty()) {
            return new BudgetControl.Check("NO_BUDGET", null, null);
        }
        Map<String, Object> r = b.get(0);
        return BudgetControl.evaluate((BigDecimal) r.get("budget"), (BigDecimal) r.get("actual"), (BigDecimal) r.get("committed"), amount);
    }

    @Transactional
    public void setBudget(int year, int month, String dept, String category, BigDecimal amount) {
        if (month < 1 || month > 12 || amount == null || amount.signum() < 0) {
            throw new BizException("预算期间或金额无效");
        }
        jdbc.update("INSERT INTO fin_budget (year, month, dept, category, amount) VALUES (?,?,?,?,?) ON DUPLICATE KEY UPDATE amount = VALUES(amount)",
                year, month, dept, category, amount);
    }

    /** 预算执行：年度按部门 × 类别汇总预算、实际、执行率 */
    public Map<String, Object> budgetReport(int year, Integer monthTo) {
        int to = monthTo == null ? 12 : monthTo;
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("rows", jdbc.queryForList("""
                SELECT dept, category, SUM(budget) AS budget, SUM(actual) AS actual, ROUND(SUM(actual) / NULLIF(SUM(budget), 0), 4) AS rate,
                       SUM(actual > budget) AS over_months
                FROM v_budget_usage WHERE year = ? AND month <= ? GROUP BY dept, category ORDER BY dept, category""", year, to));
        r.put("monthly", jdbc.queryForList("""
                SELECT month, SUM(budget) AS budget, SUM(actual) AS actual FROM v_budget_usage WHERE year = ? AND month <= ? GROUP BY month ORDER BY month""", year, to));
        return r;
    }
}
