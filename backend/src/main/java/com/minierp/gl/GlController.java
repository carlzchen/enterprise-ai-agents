package com.minierp.gl;

import com.minierp.common.BizException;
import com.minierp.system.Audited;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/gl")
@PreAuthorize("hasAnyRole('ADMIN','FINANCE','MANAGEMENT')")
@RequiredArgsConstructor
public class GlController {
    private final GlService gl;
    private final JdbcTemplate jdbc;
    private final PeriodCloseService closing;

    public record ManualLine(String account, BigDecimal debit, BigDecimal credit, String summary) {
    }

    public record ManualReq(LocalDate date, String summary, List<ManualLine> lines) {
    }

    @GetMapping("/accounts")
    public List<Map<String, Object>> accounts() {
        return jdbc.queryForList("SELECT * FROM gl_account ORDER BY code");
    }

    @GetMapping("/vouchers/{id}")
    public Map<String, Object> voucher(@PathVariable Long id) {
        Map<String, Object> v = new java.util.HashMap<>(jdbc.queryForMap("SELECT * FROM v_voucher WHERE id = ?", id));
        v.put("entries", jdbc.queryForList("SELECT * FROM v_gl_entry WHERE voucher_id = ? ORDER BY line_no", id));
        return v;
    }

    @PostMapping("/vouchers")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("gl.manualVoucher")
    public Map<String, Object> manual(@RequestBody ManualReq req, Authentication auth) {
        if (req.lines() == null) {
            throw new BizException("缺少分录");
        }
        Long id = gl.manual(req.date() == null ? LocalDate.now() : req.date(), req.summary() == null ? "手工凭证" : req.summary(),
                req.lines().stream().map(l -> new VoucherValidator.Line(l.account(), nz(l.debit()), nz(l.credit()), l.summary())).toList(),
                auth.getName());
        return Map.of("id", id);
    }

    @GetMapping("/trial-balance")
    public Map<String, Object> trialBalance(@RequestParam LocalDate from, @RequestParam LocalDate to) {
        return gl.trialBalance(from, to);
    }

    @GetMapping("/income-statement")
    public Map<String, Object> income(@RequestParam LocalDate from, @RequestParam LocalDate to) {
        return gl.incomeStatement(from, to);
    }

    @GetMapping("/reconciliation")
    public List<Map<String, Object>> reconcile() {
        return gl.reconciliation();
    }

    @GetMapping("/periods")
    public List<Map<String, Object>> periods() {
        return closing.periods();
    }

    @PostMapping("/periods/{period}/close")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("gl.closePeriod")
    public Map<String, Object> close(@PathVariable String period, Authentication auth) {
        return closing.close(java.time.YearMonth.parse(period), auth.getName());
    }

    @PostMapping("/periods/{period}/reopen")
    @PreAuthorize("hasRole('ADMIN')")
    @Audited("gl.reopenPeriod")
    public void reopen(@PathVariable String period) {
        closing.reopen(java.time.YearMonth.parse(period));
    }

    private static BigDecimal nz(BigDecimal v) {
        return v == null ? BigDecimal.ZERO : v;
    }
}
