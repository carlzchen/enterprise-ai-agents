package com.minierp.contract;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.workflow.WorkflowFinishedEvent;
import com.minierp.workflow.WorkflowService;
import lombok.RequiredArgsConstructor;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

/**
 * 合同管理：起草 → OA 审批（部门负责人 → 财务 ≥50 万 → 总经理 ≥200 万）→ 生效（签订日 = 审批通过日）→ 到期（自动续约的顺延一年）。
 * 框架合同履约额取合同期内对该客户的销售开票 / 对该供应商的采购收票，计算履约率；到期前 60 天进入提醒清单。
 */
@Service
@RequiredArgsConstructor
public class ContractService {
    private static final Set<String> TYPES = Set.of("SALES", "PURCHASE", "SERVICE", "NDA");
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final WorkflowService workflow;

    public record ContractReq(String type, String counterpartyType, Long counterpartyId, String counterpartyName, String title, BigDecimal amount,
                              String currency, String paymentTerms, LocalDate startDate, LocalDate endDate, Boolean autoRenew, String remark) {
    }

    @Transactional
    public long draft(ContractReq r, long ownerEmpId, LocalDate date) {
        if (!TYPES.contains(r.type())) {
            throw new BizException("合同类型无效");
        }
        if (r.startDate() == null || r.endDate() == null || !r.endDate().isAfter(r.startDate())) {
            throw new BizException("合同期限无效");
        }
        String name = r.counterpartyName();
        if (r.counterpartyId() != null) {
            name = jdbc.queryForObject("SELECT name FROM " + ("CUSTOMER".equals(r.counterpartyType()) ? "md_customer" : "md_supplier") + " WHERE id = ?",
                    String.class, r.counterpartyId());
        }
        String no = docNo.next("CT", date);
        jdbc.update("""
                INSERT INTO ct_contract (contract_no, type, counterparty_type, counterparty_id, counterparty_name, title, amount, currency, payment_terms,
                  start_date, end_date, status, owner_emp_id, auto_renew, remark) VALUES (?,?,?,?,?,?,?,?,?,?,?,'DRAFT',?,?,?)""",
                no, r.type(), r.counterpartyType() == null ? "OTHER" : r.counterpartyType(), r.counterpartyId(), name, r.title(),
                r.amount() == null ? BigDecimal.ZERO : r.amount(), r.currency() == null ? "CNY" : r.currency(), r.paymentTerms(),
                Date.valueOf(r.startDate()), Date.valueOf(r.endDate()), ownerEmpId, Boolean.TRUE.equals(r.autoRenew()), r.remark());
        return jdbc.queryForObject("SELECT id FROM ct_contract WHERE contract_no = ?", Long.class, no);
    }

    @Transactional
    public long submit(long id, LocalDateTime now) {
        Map<String, Object> c = contract(id);
        if (!List.of("DRAFT", "REJECTED").contains(c.get("status"))) {
            throw new BizException("只有草稿或被驳回的合同可以提交审批");
        }
        long wf = workflow.start(new WorkflowService.StartReq("CONTRACT", "合同审批：" + c.get("title") + "（" + c.get("counterparty_name") + "）",
                Map.of("contractNo", c.get("contract_no"), "type", c.get("type"), "counterparty", c.get("counterparty_name")), "CONTRACT", id,
                (BigDecimal) c.get("amount"), null), ((Number) c.get("owner_emp_id")).longValue(), now);
        jdbc.update("UPDATE ct_contract SET status = 'APPROVING', wf_instance_id = ? WHERE id = ?", wf, id);
        return wf;
    }

    @EventListener
    public void onFinished(WorkflowFinishedEvent ev) {
        if (!"CONTRACT".equals(ev.defCode()) || ev.bizId() == null) {
            return;
        }
        if ("APPROVED".equals(ev.status())) {
            jdbc.update("UPDATE ct_contract SET status = 'ACTIVE', sign_date = ? WHERE id = ?", Date.valueOf(ev.finishedAt().toLocalDate()), ev.bizId());
        } else {
            jdbc.update("UPDATE ct_contract SET status = ? WHERE id = ?", "WITHDRAWN".equals(ev.status()) ? "DRAFT" : "REJECTED", ev.bizId());
        }
    }

    @Transactional
    public void terminate(long id, String reason) {
        if (jdbc.update("UPDATE ct_contract SET status = 'TERMINATED', remark = CONCAT(COALESCE(remark, ''), ' 终止：', ?) WHERE id = ? AND status = 'ACTIVE'", reason, id) == 0) {
            throw new BizException("只有生效中的合同可以终止");
        }
    }

    /** 到期处理：自动续约的顺延一年，其余置为已到期 */
    @Transactional
    public int expire(LocalDate date) {
        int renewed = jdbc.update("""
                UPDATE ct_contract SET end_date = DATE_ADD(end_date, INTERVAL 1 YEAR), remark = CONCAT(COALESCE(remark, ''), ' 自动续约至 ', DATE_ADD(end_date, INTERVAL 1 YEAR))
                WHERE status = 'ACTIVE' AND auto_renew = 1 AND end_date < ?""", Date.valueOf(date));
        return renewed + jdbc.update("UPDATE ct_contract SET status = 'EXPIRED' WHERE status = 'ACTIVE' AND end_date < ?", Date.valueOf(date));
    }

    public Map<String, Object> detail(long id) {
        Map<String, Object> r = new LinkedHashMap<>(jdbc.queryForList("SELECT * FROM v_contract WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("合同", id)));
        BigDecimal amt = (BigDecimal) r.get("amount"), done = (BigDecimal) r.get("fulfilled_amount");
        r.put("fulfillmentRate", amt != null && amt.signum() > 0 && done != null ? done.divide(amt, 4, java.math.RoundingMode.HALF_UP) : null);
        return r;
    }

    public List<Map<String, Object>> expiring(LocalDate date, int days) {
        return jdbc.queryForList("SELECT * FROM v_contract WHERE status = 'ACTIVE' AND end_date BETWEEN ? AND ? ORDER BY end_date",
                Date.valueOf(date), Date.valueOf(date.plusDays(days)));
    }

    private Map<String, Object> contract(long id) {
        return jdbc.queryForList("SELECT * FROM ct_contract WHERE id = ?", id).stream().findFirst().orElseThrow(() -> new NotFoundException("合同", id));
    }
}
