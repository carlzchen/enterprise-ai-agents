package com.minierp.fa;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.workflow.WorkflowFinishedEvent;
import com.minierp.workflow.WorkflowService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.event.EventListener;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Date;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.YearMonth;
import java.util.*;

/**
 * 固定资产：新增（资本性支出经 OA 审批后入账）、月度计提折旧（按使用部门计入制造费用 / 期间费用）、处置（通过固定资产清理结转损益）。
 * 折旧规则见 DepreciationCalculator；月结前必须完成当期折旧（PeriodCloseService 校验）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class FaService {
    private static final BigDecimal VAT = new BigDecimal("0.13");
    private final JdbcTemplate jdbc;
    private final DocNoService docNo;
    private final GlService gl;
    private final WorkflowService workflow;
    private final com.fasterxml.jackson.databind.ObjectMapper json;

    public record AssetReq(String name, String categoryCode, String deptCode, Long equipmentId, LocalDate inServiceDate, BigDecimal cost) {
    }

    /** 登记资产卡片（不记账；期初建账与新增入账由调用方记账） */
    @Transactional
    public long register(AssetReq r, BigDecimal accumulatedDep, String lastDepPeriod, String changeType, LocalDate date, Long wfId) {
        Map<String, Object> c = jdbc.queryForList("SELECT * FROM fa_category WHERE code = ?", r.categoryCode()).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("资产类别", r.categoryCode()));
        Long dept = jdbc.queryForList("SELECT id FROM org_dept WHERE code = ?", Long.class, r.deptCode()).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("部门", r.deptCode()));
        if (r.cost() == null || r.cost().signum() <= 0) {
            throw new BizException("资产原值必须大于 0");
        }
        String no = docNo.next("FA", date);
        jdbc.update("""
                INSERT INTO fa_asset (asset_no, name, category_id, dept_id, equipment_id, in_service_date, original_cost, residual_rate, useful_life_months,
                  method, accumulated_dep, last_dep_period, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'IN_USE')""",
                no, r.name(), c.get("id"), dept, r.equipmentId(), Date.valueOf(r.inServiceDate()), r.cost(), c.get("residual_rate"),
                c.get("useful_life_months"), c.get("method"), accumulatedDep == null ? BigDecimal.ZERO : accumulatedDep, lastDepPeriod);
        long id = jdbc.queryForObject("SELECT id FROM fa_asset WHERE asset_no = ?", Long.class, no);
        jdbc.update("INSERT INTO fa_change (asset_id, change_type, change_date, amount, remark, wf_instance_id) VALUES (?,?,?,?,?,?)",
                id, changeType, Date.valueOf(date), r.cost(), r.name(), wfId);
        return id;
    }

    /** 新增资产入账：Dr 固定资产 + 进项税额 / Cr 银行存款 */
    @Transactional
    public long acquire(AssetReq r, LocalDate date, Long wfId) {
        long id = register(r, BigDecimal.ZERO, null, "ACQUIRE", date, wfId);
        String no = jdbc.queryForObject("SELECT asset_no FROM fa_asset WHERE id = ?", String.class, id);
        BigDecimal tax = r.cost().multiply(VAT).setScale(2, RoundingMode.HALF_UP);
        gl.post(date, "FA", no, "购入固定资产 " + r.name(), List.of(
                Line.dr(GlService.FIXED_ASSET, r.cost(), r.name()), Line.dr(GlService.VAT_IN, tax, "设备进项税额"),
                Line.cr(GlService.BANK, r.cost().add(tax), "银行付款")), null);
        return id;
    }

    /** 提交资本性支出审批（通过后自动入账建卡） */
    @Transactional
    public long submitCapex(AssetReq r, long applicantEmpId, String reason, LocalDateTime now) {
        Map<String, Object> form = new LinkedHashMap<>();
        form.put("name", r.name());
        form.put("categoryCode", r.categoryCode());
        form.put("deptCode", r.deptCode());
        form.put("inServiceDate", r.inServiceDate().toString());
        form.put("cost", r.cost());
        form.put("reason", reason);
        if (r.equipmentId() != null) {
            form.put("equipmentId", r.equipmentId());
        }
        return workflow.start(new WorkflowService.StartReq("CAPEX", "资本性支出：" + r.name() + " " + r.cost().toPlainString() + " 元", form, "CAPEX", null,
                r.cost(), null), applicantEmpId, now);
    }

    @EventListener
    public void onFinished(WorkflowFinishedEvent ev) {
        if (!"CAPEX".equals(ev.defCode()) || !"APPROVED".equals(ev.status())) {
            return;
        }
        Map<String, Object> f;
        try {
            f = json.readValue(jdbc.queryForObject("SELECT form_data FROM wf_instance WHERE id = ?", String.class, ev.instanceId()),
                    new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {
                    });
        } catch (Exception e) {
            throw new BizException("资本性支出表单无法解析");
        }
        LocalDate approved = ev.finishedAt().toLocalDate();
        LocalDate inService = LocalDate.parse(String.valueOf(f.get("inServiceDate")));
        acquire(new AssetReq((String) f.get("name"), (String) f.get("categoryCode"), (String) f.get("deptCode"),
                f.get("equipmentId") == null ? null : ((Number) f.get("equipmentId")).longValue(), inService.isBefore(approved) ? approved : inService,
                new BigDecimal(String.valueOf(f.get("cost")))), approved, ev.instanceId());
    }

    /** 计提某期间折旧：逐项计算、写折旧明细，按折旧费用科目汇总生成一张凭证（重复执行跳过已计提资产） */
    @Transactional
    public Map<String, Object> depreciate(YearMonth period, LocalDate postDate) {
        List<Map<String, Object>> assets = jdbc.queryForList("""
                SELECT a.*, d.cost_type FROM fa_asset a JOIN org_dept d ON d.id = a.dept_id
                WHERE a.status = 'IN_USE' AND (a.last_dep_period IS NULL OR a.last_dep_period < ?)""", period.toString());
        Map<String, BigDecimal> byAccount = new TreeMap<>();
        List<Object[]> rows = new ArrayList<>();
        BigDecimal total = BigDecimal.ZERO;
        int count = 0;
        for (Map<String, Object> a : assets) {
            BigDecimal acc = (BigDecimal) a.get("accumulated_dep");
            BigDecimal amt = DepreciationCalculator.forPeriod((String) a.get("method"), (BigDecimal) a.get("original_cost"), (BigDecimal) a.get("residual_rate"),
                    ((Number) a.get("useful_life_months")).intValue(), ((Date) a.get("in_service_date")).toLocalDate(), acc, period);
            long id = ((Number) a.get("id")).longValue();
            jdbc.update("UPDATE fa_asset SET last_dep_period = ?, accumulated_dep = accumulated_dep + ? WHERE id = ?", period.toString(), amt, id);
            if (amt.signum() <= 0) {
                continue;
            }
            String account = GlService.laborAccount((String) a.get("cost_type"));
            rows.add(new Object[]{id, period.toString(), amt, acc.add(amt), account});
            byAccount.merge(account, amt, BigDecimal::add);
            total = total.add(amt);
            count++;
        }
        jdbc.batchUpdate("INSERT INTO fa_depreciation (asset_id, period, amount, accumulated_after, expense_account) VALUES (?,?,?,?,?)", rows);
        if (total.signum() > 0) {
            List<Line> lines = new ArrayList<>();
            byAccount.forEach((acc, amt) -> lines.add(Line.dr(acc, amt, period + " 折旧")));
            lines.add(Line.cr(GlService.ACC_DEPRECIATION, total, "累计折旧"));
            gl.post(postDate, "FA", "DEP-" + period, period + " 计提固定资产折旧（" + count + " 项）", lines, null);
        }
        return Map.of("period", period.toString(), "assets", count, "amount", total, "byAccount", byAccount);
    }

    /** 处置：当月照提折旧 → 转入清理 → 收到处置款 / 支付清理费 → 结转处置损益 */
    @Transactional
    public BigDecimal dispose(long assetId, LocalDate date, BigDecimal proceeds, BigDecimal clearingCost, String reason) {
        Map<String, Object> a = asset(assetId);
        if (!"IN_USE".equals(a.get("status"))) {
            throw new BizException("资产已处置");
        }
        YearMonth ym = YearMonth.from(date);
        String last = (String) a.get("last_dep_period");
        if (last == null || last.compareTo(ym.toString()) < 0) {
            BigDecimal amt = DepreciationCalculator.forPeriod((String) a.get("method"), (BigDecimal) a.get("original_cost"), (BigDecimal) a.get("residual_rate"),
                    ((Number) a.get("useful_life_months")).intValue(), ((Date) a.get("in_service_date")).toLocalDate(), (BigDecimal) a.get("accumulated_dep"), ym);
            if (amt.signum() > 0) {
                String account = GlService.laborAccount(jdbc.queryForObject("SELECT cost_type FROM org_dept WHERE id = ?", String.class, a.get("dept_id")));
                jdbc.update("INSERT INTO fa_depreciation (asset_id, period, amount, accumulated_after, expense_account) VALUES (?,?,?,?,?)",
                        assetId, ym.toString(), amt, ((BigDecimal) a.get("accumulated_dep")).add(amt), account);
                gl.post(date, "FA", a.get("asset_no") + "-DEP", "处置当月计提折旧 " + a.get("name"), List.of(
                        Line.dr(account, amt, "折旧"), Line.cr(GlService.ACC_DEPRECIATION, amt, "累计折旧")), null);
            }
            jdbc.update("UPDATE fa_asset SET accumulated_dep = accumulated_dep + ?, last_dep_period = ? WHERE id = ?", amt, ym.toString(), assetId);
            a = asset(assetId);
        }
        BigDecimal cost = (BigDecimal) a.get("original_cost"), acc = (BigDecimal) a.get("accumulated_dep");
        BigDecimal net = cost.subtract(acc);
        BigDecimal p = proceeds == null ? BigDecimal.ZERO : proceeds, c = clearingCost == null ? BigDecimal.ZERO : clearingCost;
        BigDecimal gain = DepreciationCalculator.disposalGain(cost, acc, p, c);
        List<Line> lines = new ArrayList<>(List.of(
                Line.dr(GlService.FA_CLEARING, net, "转入清理"), Line.dr(GlService.ACC_DEPRECIATION, acc, "冲销累计折旧"),
                Line.cr(GlService.FIXED_ASSET, cost, "注销原值")));
        if (p.signum() > 0) {
            lines.add(Line.dr(GlService.BANK, p, "处置收入"));
            lines.add(Line.cr(GlService.FA_CLEARING, p, "处置收入"));
        }
        if (c.signum() > 0) {
            lines.add(Line.dr(GlService.FA_CLEARING, c, "清理费用"));
            lines.add(Line.cr(GlService.BANK, c, "支付清理费"));
        }
        if (gain.signum() > 0) {
            lines.add(Line.dr(GlService.FA_CLEARING, gain, "结转处置收益"));
            lines.add(Line.cr(GlService.ASSET_DISPOSAL, gain, "资产处置收益"));
        } else if (gain.signum() < 0) {
            lines.add(Line.dr(GlService.ASSET_DISPOSAL, gain.negate(), "资产处置损失"));
            lines.add(Line.cr(GlService.FA_CLEARING, gain.negate(), "结转处置损失"));
        }
        gl.post(date, "FA", a.get("asset_no") + "-DSP", "处置固定资产 " + a.get("name") + "（" + reason + "）", lines, null);
        jdbc.update("UPDATE fa_asset SET status = 'DISPOSED', disposal_date = ?, disposal_proceeds = ? WHERE id = ?", Date.valueOf(date), p, assetId);
        jdbc.update("INSERT INTO fa_change (asset_id, change_type, change_date, amount, remark) VALUES (?,?,?,?,?)", assetId, "DISPOSE", Date.valueOf(date), p, reason);
        return gain;
    }

    public Map<String, Object> detail(long id) {
        Map<String, Object> r = new LinkedHashMap<>(jdbc.queryForList("SELECT * FROM v_fa_asset WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("固定资产", id)));
        r.put("changes", jdbc.queryForList("SELECT * FROM fa_change WHERE asset_id = ? ORDER BY change_date, id", id));
        r.put("depreciation", jdbc.queryForList("SELECT period, amount, accumulated_after, expense_account FROM fa_depreciation WHERE asset_id = ? ORDER BY period DESC LIMIT 36", id));
        Map<String, Object> a = asset(id);
        r.put("schedule", DepreciationCalculator.schedule((String) a.get("method"), (BigDecimal) a.get("original_cost"), (BigDecimal) a.get("residual_rate"),
                ((Number) a.get("useful_life_months")).intValue(), ((Date) a.get("in_service_date")).toLocalDate()).stream()
                .filter(x -> x.period().getMonthValue() == 12).map(x -> Map.of("period", x.period().toString(), "accumulated", x.accumulated())).toList());
        return r;
    }

    public Map<String, Object> summary() {
        return Map.of("byCategory", jdbc.queryForList("""
                        SELECT category_name, COUNT(*) AS n, SUM(original_cost) AS cost, SUM(accumulated_dep) AS acc, SUM(net_value) AS net
                        FROM v_fa_asset WHERE status = 'IN_USE' GROUP BY category_name ORDER BY cost DESC"""),
                "monthly", jdbc.queryForList("""
                        SELECT period, expense_account, SUM(amount) AS amount FROM fa_depreciation GROUP BY period, expense_account ORDER BY period"""));
    }

    private Map<String, Object> asset(long id) {
        return jdbc.queryForList("SELECT * FROM fa_asset WHERE id = ?", id).stream().findFirst().orElseThrow(() -> new NotFoundException("固定资产", id));
    }
}
