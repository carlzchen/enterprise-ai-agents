package com.minierp.bi;

import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.time.YearMonth;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 经营看板：所有口径集中在这里，后续财务 Agent 的指标口径库以此为准 */
@RestController
@RequestMapping("/api/bi")
@PreAuthorize("hasAnyRole('ADMIN','FINANCE','MANAGEMENT')")
@RequiredArgsConstructor
public class BiController {
    private final JdbcTemplate jdbc;

    /** 数据中最新的业务月份，作为看板默认月份 */
    @GetMapping("/latest-month")
    public Map<String, String> latestMonth() {
        String m = jdbc.queryForObject("SELECT COALESCE(MAX(month), DATE_FORMAT(CURDATE(), '%Y-%m')) FROM v_sales_fact", String.class);
        return Map.of("month", m);
    }

    @GetMapping("/kpis")
    public Map<String, Object> kpis(@RequestParam String month) {
        YearMonth ym = YearMonth.parse(month);
        LocalDate from = ym.atDay(1), to = ym.atEndOfMonth();
        Map<String, Object> k = new LinkedHashMap<>();
        Map<String, Object> cur = revenueAndGp(from, to);
        Map<String, Object> prev = revenueAndGp(from.minusMonths(1), from.minusDays(1));
        Map<String, Object> ly = revenueAndGp(from.minusYears(1), ym.minusYears(1).atEndOfMonth());
        k.put("revenue", cur.get("revenue"));
        k.put("grossProfit", cur.get("gp"));
        k.put("revenuePrevMonth", prev.get("revenue"));
        k.put("revenueLastYear", ly.get("revenue"));
        k.put("grossProfitLastYear", ly.get("gp"));
        k.put("revenueYtd", revenueAndGp(ym.withMonth(1).atDay(1), to).get("revenue"));
        k.put("arOutstanding", scalar("SELECT COALESCE(SUM(total_cny - received_cny),0) FROM sal_invoice WHERE status <> 'PAID' AND invoice_date <= ?", to));
        k.put("arOverdue", scalar("SELECT COALESCE(SUM(total_cny - received_cny),0) FROM sal_invoice WHERE status <> 'PAID' AND due_date < ?", to));
        k.put("apOutstanding", scalar("SELECT COALESCE(SUM(total - paid),0) FROM pur_invoice WHERE status <> 'PAID'"));
        k.put("inventoryValue", scalar("SELECT COALESCE(SUM(qty * avg_cost),0) FROM inv_stock"));
        k.put("backlog", scalar("SELECT COALESCE(SUM((l.qty - l.shipped_qty) * l.unit_price * o.fx_rate),0) FROM sal_order o "
                + "JOIN sal_order_line l ON l.order_id = o.id WHERE o.status IN ('CONFIRMED','PARTIAL')"));
        k.put("yield", scalar("SELECT SUM(good_qty) / NULLIF(SUM(good_qty + scrap_qty),0) FROM mes_wo_report WHERE report_date BETWEEN ? AND ?", from, to));
        k.put("iqcPassRate", scalar("SELECT AVG(result = 'PASS') FROM qms_iqc WHERE inspect_date BETWEEN ? AND ?", from, to));
        k.put("openComplaints", scalar("SELECT COUNT(*) FROM qms_complaint WHERE status <> 'CLOSED'"));
        k.put("expenses", scalar("SELECT COALESCE(SUM(amount),0) FROM fin_expense WHERE status = 'APPROVED' AND expense_date BETWEEN ? AND ?", from, to));
        return k;
    }

    /** 月度收入 / 成本 / 毛利 / 毛利率，按内外销拆分收入 */
    @GetMapping("/revenue-trend")
    public List<Map<String, Object>> revenueTrend(@RequestParam(defaultValue = "24") int months, @RequestParam String month) {
        LocalDate to = YearMonth.parse(month).atEndOfMonth();
        LocalDate from = YearMonth.parse(month).minusMonths(months - 1L).atDay(1);
        return jdbc.queryForList("""
                SELECT month, ROUND(SUM(revenue_cny),2) AS revenue, ROUND(SUM(cost_cny),2) AS cost,
                       ROUND(SUM(gross_profit_cny),2) AS gross_profit,
                       ROUND(SUM(gross_profit_cny) / NULLIF(SUM(revenue_cny),0), 4) AS gross_margin,
                       ROUND(SUM(CASE WHEN is_domestic = 1 THEN revenue_cny ELSE 0 END),2) AS domestic_revenue,
                       ROUND(SUM(CASE WHEN is_domestic = 0 THEN revenue_cny ELSE 0 END),2) AS export_revenue
                FROM v_sales_fact WHERE delivery_date BETWEEN ? AND ?
                GROUP BY month ORDER BY month""", from, to);
    }

    /** 分产品系列的月度毛利率趋势 */
    @GetMapping("/series-margin-trend")
    public List<Map<String, Object>> seriesMarginTrend(@RequestParam(defaultValue = "24") int months, @RequestParam String month) {
        LocalDate to = YearMonth.parse(month).atEndOfMonth();
        LocalDate from = YearMonth.parse(month).minusMonths(months - 1L).atDay(1);
        return jdbc.queryForList("""
                SELECT month, series, ROUND(SUM(revenue_cny),2) AS revenue,
                       ROUND(SUM(gross_profit_cny) / NULLIF(SUM(revenue_cny),0), 4) AS gross_margin
                FROM v_sales_fact WHERE delivery_date BETWEEN ? AND ?
                GROUP BY month, series ORDER BY month, series""", from, to);
    }

    @GetMapping("/revenue-by-series")
    public List<Map<String, Object>> bySeries(@RequestParam String from, @RequestParam String to) {
        return jdbc.queryForList("""
                SELECT series, business_unit, ROUND(SUM(revenue_cny),2) AS revenue, ROUND(SUM(gross_profit_cny),2) AS gross_profit,
                       ROUND(SUM(gross_profit_cny) / NULLIF(SUM(revenue_cny),0), 4) AS gross_margin, SUM(qty) AS qty
                FROM v_sales_fact WHERE month BETWEEN ? AND ?
                GROUP BY series, business_unit ORDER BY revenue DESC""", from, to);
    }

    @GetMapping("/top-customers")
    public List<Map<String, Object>> topCustomers(@RequestParam String from, @RequestParam String to,
                                                  @RequestParam(defaultValue = "10") int limit) {
        return jdbc.queryForList("""
                SELECT customer_id, customer_name, industry, region, ROUND(SUM(revenue_cny),2) AS revenue,
                       ROUND(SUM(gross_profit_cny) / NULLIF(SUM(revenue_cny),0), 4) AS gross_margin
                FROM v_sales_fact WHERE month BETWEEN ? AND ?
                GROUP BY customer_id, customer_name, industry, region ORDER BY revenue DESC LIMIT ?""", from, to, Math.min(limit, 50));
    }

    @GetMapping("/revenue-by-industry")
    public List<Map<String, Object>> byIndustry(@RequestParam String from, @RequestParam String to) {
        return jdbc.queryForList("""
                SELECT industry, ROUND(SUM(revenue_cny),2) AS revenue FROM v_sales_fact
                WHERE month BETWEEN ? AND ? GROUP BY industry ORDER BY revenue DESC""", from, to);
    }

    /** 应收账龄（截至 asOf 日期，按到期日计算逾期天数） */
    @GetMapping("/ar-aging")
    public Map<String, Object> arAging(@RequestParam String asOf) {
        List<Map<String, Object>> buckets = jdbc.queryForList("""
                SELECT CASE WHEN DATEDIFF(?, due_date) <= 0 THEN '未到期'
                            WHEN DATEDIFF(?, due_date) <= 30 THEN '逾期1-30天'
                            WHEN DATEDIFF(?, due_date) <= 60 THEN '逾期31-60天'
                            WHEN DATEDIFF(?, due_date) <= 90 THEN '逾期61-90天'
                            ELSE '逾期90天以上' END AS bucket,
                       ROUND(SUM(total_cny - received_cny),2) AS amount, COUNT(*) AS invoices
                FROM sal_invoice WHERE status <> 'PAID' AND invoice_date <= ?
                GROUP BY bucket""", asOf, asOf, asOf, asOf, asOf);
        List<Map<String, Object>> customers = jdbc.queryForList("""
                SELECT c.name AS customer_name, ROUND(SUM(i.total_cny - i.received_cny),2) AS overdue_amount,
                       MAX(DATEDIFF(?, i.due_date)) AS max_overdue_days
                FROM sal_invoice i JOIN md_customer c ON c.id = i.customer_id
                WHERE i.status <> 'PAID' AND i.due_date < ?
                GROUP BY c.name ORDER BY overdue_amount DESC LIMIT 10""", asOf, asOf);
        return Map.of("buckets", buckets, "topOverdueCustomers", customers);
    }

    @GetMapping("/yield-by-line")
    public List<Map<String, Object>> yieldByLine(@RequestParam(defaultValue = "12") int months, @RequestParam String month) {
        LocalDate to = YearMonth.parse(month).atEndOfMonth();
        LocalDate from = YearMonth.parse(month).minusMonths(months - 1L).atDay(1);
        return jdbc.queryForList("""
                SELECT DATE_FORMAT(report_date, '%Y-%m') AS month, line_code,
                       ROUND(SUM(good_qty) / NULLIF(SUM(good_qty + scrap_qty),0), 4) AS yield_rate,
                       SUM(good_qty) AS good_qty, SUM(scrap_qty) AS scrap_qty
                FROM v_wo_report WHERE report_date BETWEEN ? AND ?
                GROUP BY month, line_code ORDER BY month, line_code""", from, to);
    }

    @GetMapping("/supplier-quality")
    public List<Map<String, Object>> supplierQuality(@RequestParam String from, @RequestParam String to) {
        return jdbc.queryForList("""
                SELECT supplier_name, category, COUNT(*) AS lots, SUM(result = 'FAIL') AS failed_lots,
                       ROUND(AVG(result = 'PASS'), 4) AS pass_rate,
                       ROUND(SUM(defect_qty) / NULLIF(SUM(sample_qty),0), 4) AS defect_rate
                FROM v_iqc WHERE inspect_date BETWEEN ? AND ?
                GROUP BY supplier_name, category ORDER BY pass_rate ASC, lots DESC""", from, to);
    }

    @GetMapping("/expense-by-category")
    public List<Map<String, Object>> expenses(@RequestParam String from, @RequestParam String to) {
        return jdbc.queryForList("""
                SELECT category, dept, ROUND(SUM(amount),2) AS amount FROM fin_expense
                WHERE status = 'APPROVED' AND expense_date BETWEEN ? AND ?
                GROUP BY category, dept ORDER BY amount DESC""", from, to);
    }

    @GetMapping("/complaints-by-type")
    public List<Map<String, Object>> complaints(@RequestParam String from, @RequestParam String to) {
        return jdbc.queryForList("""
                SELECT defect_type, series, COUNT(*) AS cnt, SUM(qty_affected) AS qty
                FROM v_complaint WHERE received_date BETWEEN ? AND ?
                GROUP BY defect_type, series ORDER BY cnt DESC""", from, to);
    }

    private Map<String, Object> revenueAndGp(LocalDate from, LocalDate to) {
        return jdbc.queryForMap("SELECT COALESCE(ROUND(SUM(revenue_cny),2),0) AS revenue, COALESCE(ROUND(SUM(gross_profit_cny),2),0) AS gp "
                + "FROM v_sales_fact WHERE delivery_date BETWEEN ? AND ?", from, to);
    }

    private Object scalar(String sql, Object... args) {
        return jdbc.queryForObject(sql, Object.class, args);
    }
}
