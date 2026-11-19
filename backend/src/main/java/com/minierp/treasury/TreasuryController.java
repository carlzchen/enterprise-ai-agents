package com.minierp.treasury;

import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.Map;

@Tag(name = "treasury", description = "资金与预算：银行对账、资金预测、报销付款、预算控制")
@RestController
@RequestMapping("/api/treasury")
@RequiredArgsConstructor
@PreAuthorize("hasAnyRole('ADMIN','FINANCE','MANAGEMENT')")
public class TreasuryController {
    private final TreasuryService treasury;

    public record BudgetReq(int year, int month, String dept, String category, BigDecimal amount) {
    }

    @Operation(summary = "银行对账：余额调节表与未达账项")
    @PostMapping("/reconcile")
    @Audited("treasury.reconcile")
    public Map<String, Object> reconcile(@RequestParam(defaultValue = "ICBC-BASIC") String account, @RequestParam String period,
                                         @RequestParam(defaultValue = "7") int window) {
        return treasury.reconcile(account, YearMonth.parse(period), window);
    }

    @Operation(summary = "依据银行回单入账（利息 / 手续费）")
    @PostMapping("/statements/{id}/book")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("treasury.book")
    public void book(@PathVariable long id) {
        treasury.bookFromStatement(id, LocalDate.now());
    }

    @PostMapping("/reimbursements/pay")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("treasury.payReimbursements")
    public Map<String, Object> pay() {
        return Map.of("amount", treasury.payReimbursements(LocalDate.now()));
    }

    @Operation(summary = "13 周资金滚动预测")
    @GetMapping("/forecast")
    public Map<String, Object> forecast(@RequestParam(required = false) LocalDate start, @RequestParam(defaultValue = "13") int weeks,
                                        @RequestParam(defaultValue = "10000000") BigDecimal safety) {
        return treasury.forecast(start == null ? LocalDate.now() : start, Math.min(weeks, 26), safety);
    }

    @GetMapping("/budget")
    public Map<String, Object> budget(@RequestParam int year, @RequestParam(required = false) Integer monthTo) {
        return treasury.budgetReport(year, monthTo);
    }

    @PostMapping("/budget")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("treasury.setBudget")
    public void setBudget(@RequestBody BudgetReq r) {
        treasury.setBudget(r.year(), r.month(), r.dept(), r.category(), r.amount());
    }

    @Operation(summary = "预算检查：报销前查询执行后使用率")
    @GetMapping("/budget/check")
    @PreAuthorize("isAuthenticated()")
    public BudgetControl.Check check(@RequestParam String dept, @RequestParam String category, @RequestParam BigDecimal amount,
                                     @RequestParam(required = false) LocalDate date) {
        return treasury.checkBudget(dept, category, date == null ? LocalDate.now() : date, amount);
    }
}
