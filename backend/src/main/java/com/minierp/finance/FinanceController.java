package com.minierp.finance;

import com.minierp.system.Audited;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;

@RestController
@RequestMapping("/api/finance")
@RequiredArgsConstructor
public class FinanceController {
    private final FinanceService finance;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    @PostMapping("/expenses")
    @Audited("finance.submitExpense")
    public Expense submit(@RequestBody Expense e, Authentication auth) {
        if (e.getExpenseDate() == null) {
            e.setExpenseDate(LocalDate.now());
        }
        Long emp = jdbc.queryForList("SELECT employee_id FROM sys_user WHERE username = ?", Long.class, auth.getName())
                .stream().filter(java.util.Objects::nonNull).findFirst().orElse(null);
        if (e.getEmployee() == null || e.getEmployee().isBlank()) {
            e.setEmployee(emp == null ? auth.getName()
                    : jdbc.queryForObject("SELECT name FROM hr_employee WHERE id = ?", String.class, emp));
        }
        return finance.submit(e, emp, java.time.LocalDateTime.now());
    }

    @PostMapping("/expenses/{id}/approve")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("finance.approveExpense")
    public Expense approve(@PathVariable Long id, Authentication auth) {
        return finance.decide(id, true, auth.getName());
    }

    @PostMapping("/expenses/{id}/reject")
    @PreAuthorize("hasAnyRole('ADMIN','FINANCE')")
    @Audited("finance.rejectExpense")
    public Expense reject(@PathVariable Long id, Authentication auth) {
        return finance.decide(id, false, auth.getName());
    }
}
