package com.minierp.hr;

import com.minierp.common.BizException;
import com.minierp.common.WorkCalendar;
import com.minierp.org.OrgService;
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
import java.util.Map;

/**
 * 请假与离职：发起 OA 流程，流程通过后回写业务。
 * 请假通过 → 请假期间考勤记为 LEAVE（已生成的考勤行更新，未来日期由考勤生成时识别）；
 * 离职通过 → 登记最后工作日，到期离职生效并停用账号。
 */
@Service
@RequiredArgsConstructor
public class LeaveService {
    private static final Map<String, String> TYPES = Map.of("ANNUAL", "年假", "SICK", "病假", "PERSONAL", "事假");
    private final JdbcTemplate jdbc;
    private final WorkflowService workflow;
    private final OrgService orgs;
    private final com.fasterxml.jackson.databind.ObjectMapper json;

    @Transactional
    public long applyLeave(long empId, String type, LocalDate start, LocalDate end, String reason, LocalDateTime now) {
        if (!TYPES.containsKey(type)) {
            throw new BizException("假别无效：" + type);
        }
        if (start == null || end == null || end.isBefore(start)) {
            throw new BizException("请假日期无效");
        }
        int days = WorkCalendar.workdays(start, end);
        if (days == 0) {
            throw new BizException("请假期间没有工作日");
        }
        Integer overlap = jdbc.queryForObject("""
                SELECT COUNT(*) FROM hr_leave WHERE employee_id = ? AND status IN ('PENDING','APPROVED') AND start_date <= ? AND end_date >= ?""",
                Integer.class, empId, Date.valueOf(end), Date.valueOf(start));
        if (overlap != null && overlap > 0) {
            throw new BizException("与已有请假时间重叠");
        }
        jdbc.update("INSERT INTO hr_leave (employee_id, leave_type, start_date, end_date, days, reason, status) VALUES (?,?,?,?,?,?,'PENDING')",
                empId, type, Date.valueOf(start), Date.valueOf(end), days, reason);
        long id = jdbc.queryForObject("SELECT MAX(id) FROM hr_leave WHERE employee_id = ?", Long.class, empId);
        String name = jdbc.queryForObject("SELECT name FROM hr_employee WHERE id = ?", String.class, empId);
        long wf = workflow.start(new WorkflowService.StartReq("LEAVE", name + " " + TYPES.get(type) + " " + days + " 天（" + start + " ~ " + end + "）",
                Map.of("leaveType", type, "startDate", start.toString(), "endDate", end.toString(), "reason", reason == null ? "" : reason),
                "LEAVE", id, null, BigDecimal.valueOf(days)), empId, now);
        jdbc.update("UPDATE hr_leave SET wf_instance_id = ? WHERE id = ?", wf, id);
        return wf;
    }

    @Transactional
    public long applyResign(long empId, LocalDate lastDay, String reason, LocalDateTime now) {
        if (lastDay == null || lastDay.isBefore(now.toLocalDate())) {
            throw new BizException("最后工作日不能早于今天");
        }
        Integer running = jdbc.queryForObject("SELECT COUNT(*) FROM wf_instance WHERE def_code = 'RESIGN' AND applicant_emp_id = ? AND status = 'RUNNING'",
                Integer.class, empId);
        if (running != null && running > 0) {
            throw new BizException("已有审批中的离职申请");
        }
        String name = jdbc.queryForObject("SELECT name FROM hr_employee WHERE id = ?", String.class, empId);
        return workflow.start(new WorkflowService.StartReq("RESIGN", name + " 离职申请（最后工作日 " + lastDay + "）",
                Map.of("lastDay", lastDay.toString(), "reason", reason == null ? "" : reason), "RESIGN", empId, null, null), empId, now);
    }

    @EventListener
    public void onFinished(WorkflowFinishedEvent ev) {
        if ("LEAVE".equals(ev.defCode()) && ev.bizId() != null) {
            String status = switch (ev.status()) {
                case "APPROVED" -> "APPROVED";
                case "WITHDRAWN" -> "WITHDRAWN";
                default -> "REJECTED";
            };
            jdbc.update("UPDATE hr_leave SET status = ? WHERE id = ?", status, ev.bizId());
            if ("APPROVED".equals(status)) {
                // 已生成的考勤（事后补假）改为请假
                jdbc.update("""
                        UPDATE hr_attendance a JOIN hr_leave l ON l.employee_id = a.employee_id
                        SET a.status = 'LEAVE', a.leave_type = l.leave_type, a.work_hours = 0, a.overtime_hours = 0, a.clock_in = NULL, a.clock_out = NULL
                        WHERE l.id = ? AND a.work_date BETWEEN l.start_date AND l.end_date""", ev.bizId());
            }
        } else if ("RESIGN".equals(ev.defCode()) && "APPROVED".equals(ev.status()) && ev.bizId() != null) {
            Map<String, Object> form;
            try {
                form = json.readValue(jdbc.queryForObject("SELECT form_data FROM wf_instance WHERE id = ?", String.class, ev.instanceId()),
                        new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {
                        });
            } catch (Exception e) {
                throw new BizException("离职申请表单无法解析");
            }
            LocalDate lastDay = LocalDate.parse(String.valueOf(form.get("lastDay")));
            String reason = form.get("reason") == null || String.valueOf(form.get("reason")).isBlank() ? "个人原因" : String.valueOf(form.get("reason"));
            orgs.scheduleLeave(ev.bizId(), lastDay, reason, ev.instanceId());
        }
    }
}
