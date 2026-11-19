package com.minierp.eam;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import com.minierp.common.NotFoundException;
import com.minierp.common.WorkCalendar;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.inventory.InventoryService;
import com.minierp.mes.MesService;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.sql.Date;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

/**
 * 设备管理：故障报修 → 派工维修（领用备件，计入制造费用）→ 完工恢复；预防性保养按计划生成工单并滚动下次到期日。
 * 报修同时在产线拉安灯，完工时关闭安灯；停机分钟 × 设备产能损失比例 进入班次 OEE 的时间开动率。
 */
@Service
@RequiredArgsConstructor
public class EamService {
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final InventoryService inventory;
    private final GlService gl;
    private final MesService mes;

    public record SpareReq(long materialId, BigDecimal qty) {
    }

    @Transactional
    public long reportBreakdown(long equipmentId, String faultCode, String desc, String reporter, LocalDateTime at) {
        Map<String, Object> eq = equipment(equipmentId);
        if ("SCRAPPED".equals(eq.get("status"))) {
            throw new BizException("设备已报废");
        }
        Integer open = jdbc.queryForObject("SELECT COUNT(*) FROM eam_work_order WHERE equipment_id = ? AND type = 'BREAKDOWN' AND status <> 'DONE'",
                Integer.class, equipmentId);
        if (open != null && open > 0) {
            throw new BizException("该设备已有未完成的维修单");
        }
        String no = docNo.next("EM", at.toLocalDate());
        jdbc.update("""
                INSERT INTO eam_work_order (wo_no, equipment_id, type, status, fault_code, fault_desc, reported_at, reporter)
                VALUES (?,?,'BREAKDOWN','OPEN',?,?,?,?)""", no, equipmentId, faultCode, desc, Timestamp.valueOf(at), reporter);
        jdbc.update("UPDATE eam_equipment SET status = 'DOWN' WHERE id = ?", equipmentId);
        if (eq.get("line_code") != null) {
            mes.raiseAndon((String) eq.get("line_code"), "EQUIPMENT", eq.get("name") + " 故障：" + desc, no, at, reporter);
        }
        return id(no);
    }

    @Transactional
    public void start(long woId, long technicianEmpId, LocalDateTime at) {
        Map<String, Object> wo = wo(woId);
        if (!"OPEN".equals(wo.get("status"))) {
            throw new BizException("维修单不是待维修状态");
        }
        jdbc.update("UPDATE eam_work_order SET status = 'IN_PROGRESS', started_at = ?, technician_emp_id = ? WHERE id = ?",
                Timestamp.valueOf(at), technicianEmpId, woId);
        mes.respondAndon((String) wo.get("wo_no"), at);
    }

    /** 完工：领用备件出库（Dr 制造费用 / Cr 原材料），计算停机时长，设备恢复运行 */
    @Transactional
    public void complete(long woId, String cause, String action, List<SpareReq> spares, BigDecimal laborHours, LocalDateTime at) {
        Map<String, Object> wo = wo(woId);
        if ("DONE".equals(wo.get("status"))) {
            throw new BizException("维修单已完成");
        }
        String no = (String) wo.get("wo_no");
        BigDecimal spareCost = BigDecimal.ZERO;
        for (SpareReq s : spares == null ? List.<SpareReq>of() : spares) {
            BigDecimal unit = inventory.issue(new InventoryService.Movement(InventoryService.MATERIAL, s.materialId(), s.qty(), at.toLocalDate(),
                    "EAM_ISSUE", "EAM", no));
            BigDecimal amount = Money.amt(s.qty().multiply(unit));
            jdbc.update("INSERT INTO eam_spare_usage (work_order_id, material_id, qty, unit_cost, amount) VALUES (?,?,?,?,?)", woId, s.materialId(), s.qty(), unit, amount);
            spareCost = spareCost.add(amount);
        }
        if (spareCost.signum() > 0) {
            gl.post(at.toLocalDate(), "EAM", no, "设备维修领用备件 " + wo.get("equipment_code"), List.of(
                    Line.dr(GlService.OVERHEAD, spareCost, "维修备件"), Line.cr(GlService.RAW, spareCost, "备件出库")), null);
        }
        LocalDateTime reported = ts(wo.get("reported_at"));
        int downtime = "PM".equals(wo.get("type")) ? ((Number) wo.get("downtime_min")).intValue() : (int) Duration.between(reported, at).toMinutes();
        jdbc.update("""
                UPDATE eam_work_order SET status = 'DONE', cause = ?, action = ?, finished_at = ?, downtime_min = ?, labor_hours = ?, spare_cost = ?,
                  started_at = COALESCE(started_at, ?) WHERE id = ?""", cause, action, Timestamp.valueOf(at), downtime,
                laborHours == null ? BigDecimal.valueOf(downtime / 60.0).setScale(2, java.math.RoundingMode.HALF_UP) : laborHours, spareCost,
                Timestamp.valueOf(reported), woId);
        jdbc.update("UPDATE eam_equipment SET status = 'RUNNING' WHERE id = ?", wo.get("equipment_id"));
        mes.resolveAndon(no, at);
    }

    /** 执行到期保养：生成 PM 工单（计划停机），完成后下次到期日 = 本次 + 周期（顺延到工作日） */
    @Transactional
    public long doPm(long planId, long technicianEmpId, LocalDateTime start, List<SpareReq> spares) {
        Map<String, Object> p = jdbc.queryForList("SELECT * FROM eam_pm_plan WHERE id = ?", planId).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("保养计划", planId));
        String no = docNo.next("EM", start.toLocalDate());
        int minutes = ((Number) p.get("est_minutes")).intValue();
        jdbc.update("""
                INSERT INTO eam_work_order (wo_no, equipment_id, type, status, fault_desc, reported_at, started_at, downtime_min, technician_emp_id, pm_plan_id)
                VALUES (?,?,'PM','IN_PROGRESS',?,?,?,?,?,?)""", no, p.get("equipment_id"), p.get("name"), Timestamp.valueOf(start), Timestamp.valueOf(start),
                minutes, technicianEmpId, planId);
        long id = id(no);
        complete(id, "计划保养", p.get("checklist") == null ? "按保养清单执行" : (String) p.get("checklist"), spares, null, start.plusMinutes(minutes));
        LocalDate next = start.toLocalDate().plusDays(((Number) p.get("interval_days")).intValue());
        while (!WorkCalendar.isWorkday(next)) {
            next = next.plusDays(1);
        }
        jdbc.update("UPDATE eam_pm_plan SET last_done = ?, next_due = ? WHERE id = ?", Date.valueOf(start.toLocalDate()), Date.valueOf(next), planId);
        return id;
    }

    @Transactional
    public void changePmInterval(long planId, int intervalDays) {
        if (intervalDays < 1) {
            throw new BizException("保养周期必须大于 0");
        }
        jdbc.update("UPDATE eam_pm_plan SET interval_days = ?, next_due = LEAST(next_due, DATE_ADD(COALESCE(last_done, next_due), INTERVAL ? DAY)) WHERE id = ?",
                intervalDays, intervalDays, planId);
    }

    /** 单台设备可靠性：期间内故障次数、MTBF、MTTR、固有可用度（运行时间 = 工作日 × 班次 × 8h） */
    public Map<String, Object> reliability(long equipmentId, LocalDate from, LocalDate to) {
        Map<String, Object> eq = equipment(equipmentId);
        Integer shifts = eq.get("line_code") == null ? 3
                : jdbc.queryForObject("SELECT shifts FROM mes_line WHERE line_code = ?", Integer.class, eq.get("line_code"));
        double hours = WorkCalendar.workdays(from, to) * 8.0 * (shifts == null ? 1 : shifts);
        Map<String, Object> agg = jdbc.queryForMap("""
                SELECT COUNT(*) AS n, COALESCE(SUM(downtime_min), 0) AS mins FROM eam_work_order
                WHERE equipment_id = ? AND type = 'BREAKDOWN' AND status = 'DONE' AND DATE(reported_at) BETWEEN ? AND ?""",
                equipmentId, Date.valueOf(from), Date.valueOf(to));
        ReliabilityCalculator.Reliability r = ReliabilityCalculator.compute(hours, ((Number) agg.get("n")).intValue(), ((Number) agg.get("mins")).doubleValue());
        Map<String, Object> out = new LinkedHashMap<>(eq);
        out.put("from", from);
        out.put("to", to);
        out.put("operatingHours", hours);
        out.put("failures", r.failures());
        out.put("mtbfHours", r.mtbfHours());
        out.put("mttrHours", r.mttrHours());
        out.put("inherentAvailability", r.availability());
        out.put("workOrders", jdbc.queryForList("SELECT * FROM v_eam_work_order WHERE equipment_id = ? ORDER BY reported_at DESC LIMIT 50", equipmentId));
        out.put("pmPlans", jdbc.queryForList("SELECT * FROM v_pm_plan WHERE equipment_id = ?", equipmentId));
        return out;
    }

    /** 设备看板：按月故障次数 / 停机时长 / 备件费用、PM 按期完成率、停机 TOP 设备 */
    public Map<String, Object> dashboard(LocalDate from, LocalDate to) {
        Map<String, Object> r = new LinkedHashMap<>();
        r.put("monthly", jdbc.queryForList("""
                SELECT DATE_FORMAT(reported_at, '%Y-%m') AS month, SUM(type = 'BREAKDOWN') AS breakdowns,
                       SUM(IF(type = 'BREAKDOWN', downtime_min, 0)) AS breakdown_min, SUM(type = 'PM') AS pms, SUM(spare_cost) AS spare_cost
                FROM eam_work_order WHERE DATE(reported_at) BETWEEN ? AND ? GROUP BY month ORDER BY month""", Date.valueOf(from), Date.valueOf(to)));
        r.put("topDowntime", jdbc.queryForList("""
                SELECT q.code, q.name, q.line_code, COUNT(*) AS breakdowns, SUM(w.downtime_min) AS downtime_min, ROUND(AVG(w.downtime_min)) AS avg_repair_min
                FROM eam_work_order w JOIN eam_equipment q ON q.id = w.equipment_id
                WHERE w.type = 'BREAKDOWN' AND DATE(w.reported_at) BETWEEN ? AND ? GROUP BY q.id, q.code, q.name, q.line_code
                ORDER BY downtime_min DESC LIMIT 10""", Date.valueOf(from), Date.valueOf(to)));
        r.put("faultCodes", jdbc.queryForList("""
                SELECT fault_code, COUNT(*) AS n FROM eam_work_order WHERE type = 'BREAKDOWN' AND DATE(reported_at) BETWEEN ? AND ?
                GROUP BY fault_code ORDER BY n DESC""", Date.valueOf(from), Date.valueOf(to)));
        r.put("pmOverdue", jdbc.queryForObject("SELECT COUNT(*) FROM eam_pm_plan WHERE next_due < ?", Long.class, Date.valueOf(to)));
        return r;
    }

    private Map<String, Object> equipment(long id) {
        return jdbc.queryForList("SELECT * FROM eam_equipment WHERE id = ?", id).stream().findFirst().orElseThrow(() -> new NotFoundException("设备", id));
    }

    private Map<String, Object> wo(long id) {
        return jdbc.queryForList("SELECT * FROM v_eam_work_order WHERE id = ?", id).stream().findFirst().orElseThrow(() -> new NotFoundException("维修单", id));
    }

    private long id(String no) {
        return jdbc.queryForObject("SELECT id FROM eam_work_order WHERE wo_no = ?", Long.class, no);
    }

    private static LocalDateTime ts(Object v) {
        return v instanceof Timestamp t ? t.toLocalDateTime() : (LocalDateTime) v;
    }
}
