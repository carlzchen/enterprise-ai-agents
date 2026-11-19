package com.minierp.crm;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/** CRM：商机按阶段推进（只能前进一步，赢单/丢单为终态），跟进记录，漏斗统计 */
@Service
@RequiredArgsConstructor
public class CrmService {
    public static final List<String> STAGES = List.of("LEAD", "QUALIFIED", "PROPOSAL", "NEGOTIATION", "WON");
    public static final Map<String, Integer> PROBABILITY = Map.of("LEAD", 10, "QUALIFIED", 30, "PROPOSAL", 50,
            "NEGOTIATION", 70, "WON", 100, "LOST", 0);

    private final JdbcTemplate jdbc;
    private final DocNoService docNo;

    @Transactional
    public long create(long customerId, String title, String series, BigDecimal amount, LocalDate expectedClose, String owner, LocalDate date) {
        if (amount == null || amount.signum() <= 0) {
            throw new BizException("预计金额必须大于 0");
        }
        String no = docNo.next("OPP", date);
        jdbc.update("INSERT INTO crm_opportunity (opp_no, customer_id, title, series, stage, amount, probability, expected_close, owner, created_date) "
                + "VALUES (?,?,?,?, 'LEAD', ?, 10, ?, ?, ?)", no, customerId, title, series, amount, Date.valueOf(expectedClose), owner, Date.valueOf(date));
        return jdbc.queryForObject("SELECT id FROM crm_opportunity WHERE opp_no = ?", Long.class, no);
    }

    /** 推进到下一阶段；target = LOST 时必须填写原因 */
    @Transactional
    public void advance(long id, String target, String lostReason, LocalDate date) {
        List<String> cur = jdbc.queryForList("SELECT stage FROM crm_opportunity WHERE id = ?", String.class, id);
        if (cur.isEmpty()) {
            throw new NotFoundException("商机", id);
        }
        String stage = cur.get(0);
        if ("WON".equals(stage) || "LOST".equals(stage)) {
            throw new BizException("商机已关闭");
        }
        if ("LOST".equals(target)) {
            if (lostReason == null || lostReason.isBlank()) {
                throw new BizException("丢单必须填写原因");
            }
        } else if (STAGES.indexOf(target) != STAGES.indexOf(stage) + 1) {
            throw new BizException("只能从 " + stage + " 推进到下一阶段");
        }
        boolean closed = "WON".equals(target) || "LOST".equals(target);
        jdbc.update("UPDATE crm_opportunity SET stage=?, probability=?, lost_reason=?, closed_date=? WHERE id=?",
                target, PROBABILITY.get(target), lostReason, closed ? Date.valueOf(date) : null, id);
    }

    @Transactional
    public void activity(Long opportunityId, long customerId, String type, String subject, String owner, LocalDate date) {
        jdbc.update("INSERT INTO crm_activity (opportunity_id, customer_id, activity_date, type, subject, owner) VALUES (?,?,?,?,?,?)",
                opportunityId, customerId, Date.valueOf(date), type, subject, owner);
    }

    @Transactional
    public void contact(long customerId, String name, String title, String phone, String email, boolean primary) {
        jdbc.update("INSERT INTO crm_contact (customer_id, name, title, phone, email, is_primary) VALUES (?,?,?,?,?,?)",
                customerId, name, title, phone, email, primary);
    }

    public List<Map<String, Object>> funnel() {
        return jdbc.queryForList("""
                SELECT stage, COUNT(*) AS cnt, ROUND(SUM(amount), 2) AS amount, ROUND(SUM(amount * probability / 100), 2) AS weighted
                FROM crm_opportunity GROUP BY stage
                ORDER BY FIELD(stage, 'LEAD','QUALIFIED','PROPOSAL','NEGOTIATION','WON','LOST')""");
    }
}
