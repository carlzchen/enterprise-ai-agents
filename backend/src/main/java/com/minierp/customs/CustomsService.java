package com.minierp.customs;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Date;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.*;

/**
 * 关务与增值税：
 * - 外销发货生成报关单（按产品归类 HS 编码与退税率，FOB = 发货收入），放行、结关；
 * - 增值税月结（生产企业免抵退）：取当月内销销项、进项、上期留抵与当月结关出口 FOB，计算应纳 / 应退 / 免抵 / 留抵，
 *   记账后把增值税各明细科目余额结转到"未交增值税"，并计提城建税及教育费附加；次月缴纳，应退税额到账后冲减应收出口退税。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class CustomsService {
    public static final BigDecimal LEVY_RATE = new BigDecimal("0.13");
    private static final Set<String> VAT_SUBACCOUNTS = Set.of(GlService.VAT_IN, GlService.VAT_OUT, GlService.VAT_EXPORT_REFUND, GlService.VAT_EXPORT_OFFSET);

    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final GlService gl;

    // ---------- 报关 ----------

    @Transactional
    public long declare(long deliveryId, LocalDate date, String port) {
        Map<String, Object> d = jdbc.queryForList("""
                SELECT d.id, d.customer_id, c.is_domestic, c.currency, o.fx_rate FROM sal_delivery d JOIN md_customer c ON c.id = d.customer_id
                JOIN sal_order o ON o.id = d.order_id WHERE d.id = ?""", deliveryId).stream().findFirst().orElseThrow(() -> new NotFoundException("发货单", deliveryId));
        if (Boolean.TRUE.equals(d.get("is_domestic")) || d.get("is_domestic") instanceof Number n && n.intValue() == 1) {
            throw new BizException("内销发货不需要报关");
        }
        if (!jdbc.queryForList("SELECT id FROM cu_declaration WHERE delivery_id = ?", Long.class, deliveryId).isEmpty()) {
            throw new BizException("该发货单已报关");
        }
        List<Map<String, Object>> lines = jdbc.queryForList("""
                SELECT dl.product_id, dl.qty, ROUND(dl.qty * dl.unit_price, 2) AS amount, p.series, p.power_w FROM sal_delivery_line dl
                JOIN md_product p ON p.id = dl.product_id WHERE dl.delivery_id = ?""", deliveryId);
        BigDecimal fob = lines.stream().map(l -> (BigDecimal) l.get("amount")).reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal fx = (BigDecimal) d.get("fx_rate");
        String no = docNo.next("CD", date);
        jdbc.update("""
                INSERT INTO cu_declaration (decl_no, delivery_id, customer_id, port, trade_mode, currency, fob_amount, fx_rate, fob_cny, declare_date, status)
                VALUES (?,?,?,?,'0110',?,?,?,?,?,'DECLARED')""", no, deliveryId, d.get("customer_id"), port, d.get("currency"), fob, fx,
                fob.multiply(fx).setScale(2, RoundingMode.HALF_UP), Date.valueOf(date));
        long id = jdbc.queryForObject("SELECT id FROM cu_declaration WHERE decl_no = ?", Long.class, no);
        for (Map<String, Object> l : lines) {
            HsClassifier.Hs hs = HsClassifier.classify((String) l.get("series"), (BigDecimal) l.get("power_w"));
            jdbc.update("INSERT INTO cu_declaration_line (decl_id, product_id, hs_code, qty, amount, refund_rate) VALUES (?,?,?,?,?,?)",
                    id, l.get("product_id"), hs.code(), l.get("qty"), l.get("amount"), hs.refundRate());
        }
        return id;
    }

    @Transactional
    public void release(long declId, LocalDate date) {
        if (jdbc.update("UPDATE cu_declaration SET status = 'RELEASED', release_date = ? WHERE id = ? AND status = 'DECLARED'", Date.valueOf(date), declId) == 0) {
            throw new BizException("报关单不是已申报状态");
        }
    }

    @Transactional
    public void export(long declId, LocalDate date) {
        if (jdbc.update("UPDATE cu_declaration SET status = 'EXPORTED', export_date = ? WHERE id = ? AND status = 'RELEASED'", Date.valueOf(date), declId) == 0) {
            throw new BizException("报关单尚未放行");
        }
    }

    // ---------- 增值税月结 ----------

    @Transactional
    public Map<String, Object> vatReturn(YearMonth ym, LocalDate postDate) {
        String period = ym.toString();
        if (!jdbc.queryForList("SELECT id FROM tax_vat_return WHERE period = ?", Long.class, period).isEmpty()) {
            throw new BizException(period + " 增值税已月结");
        }
        Date from = Date.valueOf(ym.atDay(1)), to = Date.valueOf(ym.atEndOfMonth());
        BigDecimal output = sum("SELECT COALESCE(SUM(e.credit - e.debit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.account_code = ? AND v.voucher_date BETWEEN ? AND ?",
                GlService.VAT_OUT, from, to);
        BigDecimal input = sum("SELECT COALESCE(SUM(e.debit - e.credit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.account_code = ? AND v.voucher_date BETWEEN ? AND ?",
                GlService.VAT_IN, from, to);
        BigDecimal unpaidBal = sum("SELECT COALESCE(SUM(e.credit - e.debit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.account_code = ? AND v.voucher_date <= ?",
                GlService.VAT_UNPAID, to, null);
        BigDecimal prevCredit = unpaidBal.signum() < 0 ? unpaidBal.negate() : BigDecimal.ZERO; // 未交增值税借方余额即上期留抵
        List<Long> decls = jdbc.queryForList("SELECT id FROM cu_declaration WHERE status = 'EXPORTED' AND refund_period IS NULL AND export_date <= ?", Long.class, to);
        BigDecimal fob = decls.isEmpty() ? BigDecimal.ZERO : jdbc.queryForObject(
                "SELECT COALESCE(SUM(fob_cny), 0) FROM cu_declaration WHERE id IN (" + String.join(",", Collections.nCopies(decls.size(), "?")) + ")",
                BigDecimal.class, decls.toArray());
        ExportRefundCalculator.Result r = ExportRefundCalculator.compute(output, input, prevCredit, fob, LEVY_RATE, new BigDecimal("0.13"));
        String no = "VAT-" + period;
        if (r.nonDeductible().signum() > 0) {
            gl.post(postDate, "TAX", no, period + " 免抵退税不得免征和抵扣税额（进项转出）", List.of(
                    Line.dr(GlService.COGS, r.nonDeductible(), "进项税额转出"), Line.cr(GlService.VAT_IN, r.nonDeductible(), "进项税额转出")), null);
        }
        if (r.exemptRefund().signum() > 0) {
            List<Line> lines = new ArrayList<>();
            if (r.refund().signum() > 0) {
                lines.add(Line.dr(GlService.EXPORT_REFUND_RECEIVABLE, r.refund(), "应收出口退税"));
            }
            if (r.exemptOffset().signum() > 0) {
                lines.add(Line.dr(GlService.VAT_EXPORT_OFFSET, r.exemptOffset(), "出口抵减内销产品应纳税额"));
            }
            lines.add(Line.cr(GlService.VAT_EXPORT_REFUND, r.exemptRefund(), "免抵退税额"));
            gl.post(postDate, "TAX", no, period + " 出口免抵退税", lines, null);
        }
        // 增值税各明细科目余额结转"未交增值税"（贷方余额为应纳，借方余额为留抵）
        List<Line> sweep = new ArrayList<>();
        BigDecimal net = BigDecimal.ZERO;
        for (String acc : new TreeSet<>(VAT_SUBACCOUNTS)) {
            BigDecimal bal = sum("SELECT COALESCE(SUM(e.debit - e.credit), 0) FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id WHERE e.account_code = ? AND v.voucher_date <= ?",
                    acc, to, null);
            if (bal.signum() > 0) {
                sweep.add(Line.cr(acc, bal, "结转"));
            } else if (bal.signum() < 0) {
                sweep.add(Line.dr(acc, bal.negate(), "结转"));
            }
            net = net.add(bal);
        }
        if (net.signum() > 0) {
            sweep.add(Line.dr(GlService.VAT_UNPAID, net, "留抵税额"));
        } else if (net.signum() < 0) {
            sweep.add(Line.cr(GlService.VAT_UNPAID, net.negate(), "转出未交增值税"));
        }
        if (!sweep.isEmpty()) {
            gl.post(postDate, "TAX", no, period + " 转出未交增值税", sweep, null);
        }
        if (r.surtax().signum() > 0) {
            gl.post(postDate, "TAX", no, period + " 计提城建税及教育费附加", List.of(
                    Line.dr(GlService.TAX_SURCHARGE, r.surtax(), "城建税 7% + 教育费附加 3% + 地方教育附加 2%"),
                    Line.cr(GlService.SURTAX_PAYABLE, r.surtax(), "应交附加税")), null);
        }
        jdbc.update("""
                INSERT INTO tax_vat_return (period, output_vat, input_vat, prev_credit, export_fob, non_deductible, payable, exempt_refund, refund, exempt_offset,
                  end_credit, surtax) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", period, output, input, prevCredit, fob, r.nonDeductible(), r.payable(),
                r.exemptRefund(), r.refund(), r.exemptOffset(), r.endCredit(), r.surtax());
        if (!decls.isEmpty()) {
            jdbc.update("UPDATE cu_declaration SET refund_period = ? WHERE id IN (" + String.join(",", Collections.nCopies(decls.size(), "?")) + ")",
                    prepend(period, decls));
        }
        log.info("{} 增值税月结：应纳 {}，应退 {}，免抵 {}，期末留抵 {}", period, r.payable(), r.refund(), r.exemptOffset(), r.endCredit());
        return jdbc.queryForMap("SELECT * FROM tax_vat_return WHERE period = ?", period);
    }

    /** 缴纳上月增值税与附加税（申报期内） */
    @Transactional
    public BigDecimal payVat(String period, LocalDate date) {
        Map<String, Object> ret = jdbc.queryForList("SELECT * FROM tax_vat_return WHERE period = ?", period).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("增值税申报", period));
        if (ret.get("paid_date") != null) {
            throw new BizException(period + " 已缴纳");
        }
        BigDecimal vat = ((BigDecimal) ret.get("payable")).max(BigDecimal.ZERO), surtax = (BigDecimal) ret.get("surtax");
        List<Line> lines = new ArrayList<>();
        if (vat.signum() > 0) {
            lines.add(Line.dr(GlService.VAT_UNPAID, vat, "缴纳增值税"));
        }
        if (surtax.signum() > 0) {
            lines.add(Line.dr(GlService.SURTAX_PAYABLE, surtax, "缴纳附加税"));
        }
        if (!lines.isEmpty()) {
            lines.add(Line.cr(GlService.BANK, vat.add(surtax), "银行扣税"));
            gl.post(date, "TAX", "VAT-" + period + "-PAY", period + " 缴纳增值税及附加", lines, null);
        }
        jdbc.update("UPDATE tax_vat_return SET paid_date = ? WHERE period = ?", Date.valueOf(date), period);
        return vat.add(surtax);
    }

    /** 出口退税款到账 */
    @Transactional
    public void receiveRefund(String period, LocalDate date) {
        Map<String, Object> ret = jdbc.queryForMap("SELECT * FROM tax_vat_return WHERE period = ?", period);
        BigDecimal refund = (BigDecimal) ret.get("refund");
        if (ret.get("refund_received") != null || refund.signum() <= 0) {
            throw new BizException(period + " 没有待收的出口退税");
        }
        gl.post(date, "TAX", "VAT-" + period + "-RF", period + " 出口退税款到账", List.of(
                Line.dr(GlService.BANK, refund, "退税到账"), Line.cr(GlService.EXPORT_REFUND_RECEIVABLE, refund, "冲应收出口退税")), null);
        jdbc.update("UPDATE tax_vat_return SET refund_received = ? WHERE period = ?", Date.valueOf(date), period);
    }

    private BigDecimal sum(String sql, String acc, Date a, Date b) {
        return b == null ? jdbc.queryForObject(sql, BigDecimal.class, acc, a) : jdbc.queryForObject(sql, BigDecimal.class, acc, a, b);
    }

    private static Object[] prepend(Object first, List<Long> rest) {
        Object[] out = new Object[rest.size() + 1];
        out[0] = first;
        for (int i = 0; i < rest.size(); i++) {
            out[i + 1] = rest.get(i);
        }
        return out;
    }
}
