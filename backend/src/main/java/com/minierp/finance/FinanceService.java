package com.minierp.finance;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class FinanceService {
    private final ExpenseRepository expenses;
    private final DocNoService docNo;
    private final GlService gl;
    private final com.minierp.workflow.WorkflowService workflow;
    private final com.minierp.treasury.TreasuryService treasury;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    /** 提交报销并发起 EXPENSE 审批流程（报销人为员工时）；流程结束由 onWorkflowFinished 入账 */
    @Transactional
    public Expense submit(Expense e, Long employeeId, java.time.LocalDateTime now) {
        // 预算控制：在保存本单之前检查（否则本单会同时计入"审批中"而被重复计算），执行后超预算的流程增加总经理审批
        com.minierp.treasury.BudgetControl.Check budget = employeeId == null ? null
                : treasury.checkBudget(e.getDept(), e.getCategory(), e.getExpenseDate() == null ? now.toLocalDate() : e.getExpenseDate(), e.getAmount());
        Expense saved = submit(e);
        if (employeeId != null) {
            saved.setEmployeeId(employeeId);
            boolean over = "OVER".equals(budget.status());
            long wf = workflow.start(new com.minierp.workflow.WorkflowService.StartReq("EXPENSE",
                    saved.getDept() + " " + saved.getCategory() + " " + saved.getAmount().toPlainString() + " 元（" + saved.getEmployee() + "）" + (over ? "【超预算】" : ""),
                    java.util.Map.of("category", saved.getCategory(), "description", saved.getDescription() == null ? "" : saved.getDescription(),
                            "expenseNo", saved.getExpenseNo(), "overBudget", over ? "Y" : "N", "budgetStatus", budget.status(),
                            "budgetUsage", budget.usageAfter() == null ? "-" : budget.usageAfter().toPlainString()),
                    "EXPENSE", saved.getId(), saved.getAmount(), null), employeeId, now);
            saved.setWfInstanceId(wf);
        }
        return saved;
    }

    @org.springframework.context.event.EventListener
    public void onWorkflowFinished(com.minierp.workflow.WorkflowFinishedEvent ev) {
        if (!"EXPENSE".equals(ev.defCode()) || ev.bizId() == null) {
            return;
        }
        Expense e = expenses.findById(ev.bizId()).orElseThrow();
        String approver = ev.lastApproverEmpId() == null ? "系统（无需审批）"
                : jdbc.queryForObject("SELECT name FROM hr_employee WHERE id = ?", String.class, ev.lastApproverEmpId());
        // 费用在审批通过日入账（审批可能跨月，报销日所在期间可能已结账）
        apply(e, "APPROVED".equals(ev.status()), approver, ev.finishedAt().toLocalDate());
    }

    @Transactional
    public Expense submit(Expense e) {
        if (e.getAmount() == null || e.getAmount().signum() <= 0) {
            throw new BizException("报销金额必须大于 0");
        }
        e.setId(null);
        e.setExpenseNo(docNo.next("EX", e.getExpenseDate()));
        e.setStatus("SUBMITTED");
        e.setApprovedBy(null);
        return expenses.save(e);
    }

    /** 审批：不能审批自己提交的报销（职责分离） */
    @Transactional
    public Expense decide(Long id, boolean approve, String approver) {
        Expense e = expenses.findById(id).orElseThrow(() -> new NotFoundException("报销单", id));
        if (e.getWfInstanceId() != null) {
            throw new BizException("该报销单走审批流程，请在审批中心处理");
        }
        if (!"SUBMITTED".equals(e.getStatus())) {
            throw new BizException("报销单已处理");
        }
        if (approver != null && approver.equals(e.getEmployee())) {
            throw new BizException("不能审批本人提交的报销单");
        }
        return apply(e, approve, approver, e.getExpenseDate());
    }

    private Expense apply(Expense e, boolean approve, String approver, java.time.LocalDate postDate) {
        if (!"SUBMITTED".equals(e.getStatus())) {
            throw new BizException("报销单已处理");
        }
        e.setStatus(approve ? "APPROVED" : "REJECTED");
        e.setApprovedBy(approver);
        if (approve) {
            gl.post(postDate, "EXPENSE", e.getExpenseNo(), e.getDept() + " " + e.getCategory(), java.util.List.of(
                    Line.dr(GlService.expenseAccount(e.getDept()), e.getAmount(), e.getCategory()),
                    Line.cr(GlService.OTHER_PAYABLE, e.getAmount(), "应付报销款 " + e.getEmployee())), approver);
        }
        return e;
    }
}
