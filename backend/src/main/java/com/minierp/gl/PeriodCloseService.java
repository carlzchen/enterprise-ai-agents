package com.minierp.gl;

import com.minierp.common.BizException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 月结：校验试算平衡 → 结转制造费用差异 → 结转损益到本年利润 →（12 月）本年利润转未分配利润 → 锁定期间。
 * 必须按顺序结账；反结账只能从最后一个已结期间开始，删除该期结转凭证并解锁。
 */
@Service
@RequiredArgsConstructor
public class PeriodCloseService {
    private final JdbcTemplate jdbc;
    private final GlService gl;

    @Transactional
    public Map<String, Object> close(YearMonth ym, String user) {
        String period = ym.toString();
        if (isClosed(period)) {
            throw new BizException(period + " 已结账");
        }
        String firstOpen = jdbc.queryForObject("""
                SELECT MIN(DATE_FORMAT(v.voucher_date, '%Y-%m')) FROM gl_voucher v
                WHERE DATE_FORMAT(v.voucher_date, '%Y-%m') NOT IN (SELECT period FROM gl_period WHERE status = 'CLOSED')""", String.class);
        if (firstOpen != null && firstOpen.compareTo(period) < 0) {
            throw new BizException("请先结账 " + firstOpen);
        }
        LocalDate from = ym.atDay(1), to = ym.atEndOfMonth();
        // 结账前置检查：固定资产折旧已计提、增值税已月结（有业务的期间）
        Integer undepreciated = jdbc.queryForObject("""
                SELECT COUNT(*) FROM fa_asset WHERE status = 'IN_USE' AND DATE_FORMAT(DATE_ADD(in_service_date, INTERVAL 1 MONTH), '%Y-%m') <= ?
                  AND accumulated_dep < ROUND(original_cost * (1 - residual_rate), 2) AND (last_dep_period IS NULL OR last_dep_period < ?)""",
                Integer.class, period, period);
        if (undepreciated != null && undepreciated > 0) {
            throw new BizException(period + " 还有 " + undepreciated + " 项固定资产未计提折旧");
        }
        Integer vatEntries = jdbc.queryForObject("""
                SELECT COUNT(*) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE e.account_code IN ('222101','222102') AND v.voucher_date BETWEEN ? AND ?""", Integer.class, from, to);
        Integer vatReturn = jdbc.queryForObject("SELECT COUNT(*) FROM tax_vat_return WHERE period = ?", Integer.class, period);
        if (vatEntries != null && vatEntries > 0 && (vatReturn == null || vatReturn == 0)) {
            throw new BizException(period + " 增值税尚未月结（免抵退计算与结转未交增值税）");
        }
        Integer revenueEntries = jdbc.queryForObject("""
                SELECT COUNT(*) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.account_code = '6001' AND v.voucher_date BETWEEN ? AND ?""",
                Integer.class, from, to);
        Integer accrual = jdbc.queryForObject("SELECT COUNT(*) FROM svc_warranty_ledger WHERE period = ? AND type = 'ACCRUAL'", Integer.class, period);
        if (revenueEntries != null && revenueEntries > 0 && (accrual == null || accrual == 0)) {
            throw new BizException(period + " 尚未计提产品质量保证金");
        }
        Map<String, Object> tb = gl.trialBalance(from, to);
        if (!Boolean.TRUE.equals(tb.get("balanced"))) {
            throw new BizException(period + " 试算不平衡，不能结账");
        }
        Map<String, BigDecimal> net = new HashMap<>();
        jdbc.query("""
                SELECT e.account_code, SUM(e.debit - e.credit) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                WHERE v.voucher_date BETWEEN ? AND ? GROUP BY e.account_code""", rs -> {
            net.put(rs.getString(1), rs.getBigDecimal(2));
        }, from, to);
        ClosingCalculator.Result r = ClosingCalculator.close(net);
        if (!r.overheadLines().isEmpty()) {
            gl.post(to, "CLOSING", period, period + " 结转制造费用差异", r.overheadLines(), user);
        }
        if (!r.profitLines().isEmpty()) {
            gl.post(to, "CLOSING", period, period + " 结转损益", r.profitLines(), user);
        }
        if (ym.getMonthValue() == 12) {
            BigDecimal yearProfit = jdbc.queryForObject("""
                    SELECT COALESCE(SUM(e.credit - e.debit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id
                    WHERE e.account_code = '4103' AND v.voucher_date <= ?""", BigDecimal.class, to);
            if (yearProfit.signum() != 0) {
                gl.post(to, "CLOSING", period, ym.getYear() + " 年度利润转入未分配利润", yearProfit.signum() > 0
                        ? List.of(VoucherValidator.Line.dr("4103", yearProfit, "年末结转"), VoucherValidator.Line.cr("4104", yearProfit, "年末结转"))
                        : List.of(VoucherValidator.Line.dr("4104", yearProfit.negate(), "年末结转"), VoucherValidator.Line.cr("4103", yearProfit.negate(), "年末结转")), user);
            }
        }
        jdbc.update("INSERT INTO gl_period (period, status, closed_by, closed_at, profit) VALUES (?, 'CLOSED', ?, NOW(), ?) "
                + "ON DUPLICATE KEY UPDATE status='CLOSED', closed_by=VALUES(closed_by), closed_at=NOW(), profit=VALUES(profit)", period, user, r.profit());
        gl.refreshClosedPeriods();
        return Map.of("period", period, "profit", r.profit());
    }

    @Transactional
    public void reopen(YearMonth ym) {
        String period = ym.toString();
        if (!isClosed(period)) {
            throw new BizException(period + " 未结账");
        }
        Integer later = jdbc.queryForObject("SELECT COUNT(*) FROM gl_period WHERE status = 'CLOSED' AND period > ?", Integer.class, period);
        if (later != null && later > 0) {
            throw new BizException("请先反结账之后的期间");
        }
        jdbc.update("DELETE e FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE v.source_type = 'CLOSING' AND v.source_no = ?", period);
        jdbc.update("DELETE FROM gl_voucher WHERE source_type = 'CLOSING' AND source_no = ?", period);
        jdbc.update("UPDATE gl_period SET status = 'OPEN', profit = NULL WHERE period = ?", period);
        gl.refreshClosedPeriods();
    }

    public List<Map<String, Object>> periods() {
        return jdbc.queryForList("""
                SELECT m.period, COALESCE(p.status, 'OPEN') AS status, p.closed_by, p.closed_at, p.profit, m.voucher_count
                FROM (SELECT DATE_FORMAT(voucher_date, '%Y-%m') AS period, COUNT(*) AS voucher_count FROM gl_voucher GROUP BY period) m
                LEFT JOIN gl_period p ON p.period = m.period ORDER BY m.period DESC""");
    }

    private boolean isClosed(String period) {
        Integer n = jdbc.queryForObject("SELECT COUNT(*) FROM gl_period WHERE period = ? AND status = 'CLOSED'", Integer.class, period);
        return n != null && n > 0;
    }
}
