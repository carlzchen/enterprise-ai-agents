package com.minierp.production;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import com.minierp.common.NotFoundException;
import com.minierp.inventory.InventoryService;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import com.minierp.masterdata.BomService;
import com.minierp.masterdata.MasterDataService;
import com.minierp.masterdata.Material;
import com.minierp.masterdata.Product;
import org.springframework.jdbc.core.JdbcTemplate;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * 生产工单：计划 → 下达 → 报工（按 BOM 倒冲领料、完工入库、归集成本）→ 完工。
 * 完工单位成本 = (本次耗用材料 + 本次加工费) / 本次良品数，报废损失由良品分摊。
 * 多级 BOM：成品工单领用半成品（1411），半成品由组件工单生产；主料不足时按优先级自动领用替代料并留痕。
 */
@Service
@RequiredArgsConstructor
public class ProductionService {
    public static final Map<String, String> LINE_BY_SERIES = Map.of(
            "SHADED_POLE", "L1", "CROSSFLOW", "L2", "BLDC", "L3", "STEPPER", "L4", "SERVO", "L5", "GEAR", "L6");

    private final WorkOrderRepository workOrders;
    private final WoReportRepository reports;
    private final MasterDataService md;
    private final InventoryService inventory;
    private final DocNoService docNo;
    private final GlService gl;
    private final BomService bomService;
    private final JdbcTemplate jdbc;

    @Transactional
    public WorkOrder create(Long productId, BigDecimal qty, LocalDate planStart, LocalDate planEnd) {
        Product p = md.product(productId);
        if (qty == null || qty.signum() <= 0) {
            throw new BizException("计划数量必须大于 0");
        }
        if (md.bomOf(productId).isEmpty()) {
            throw new BizException("产品没有 BOM，不能创建工单：" + p.getCode());
        }
        WorkOrder wo = new WorkOrder();
        wo.setWoNo(docNo.next("WO", planStart));
        wo.setProductId(productId);
        wo.setLineCode(LINE_BY_SERIES.getOrDefault(p.getSeries(), "L1"));
        wo.setPlannedQty(qty);
        wo.setStatus("PLANNED");
        wo.setPlanStart(planStart);
        wo.setPlanEnd(planEnd);
        return workOrders.save(wo);
    }

    /** 半成品（组件）工单，parentWoId 记录为哪张上级工单备料 */
    @Transactional
    public WorkOrder createSemi(Long materialId, BigDecimal qty, LocalDate planStart, LocalDate planEnd, Long parentWoId) {
        Material m = md.material(materialId);
        if (!"MAKE".equals(m.getSupplyType())) {
            throw new BizException("外购物料不能创建生产工单：" + m.getCode());
        }
        if (qty == null || qty.signum() <= 0) {
            throw new BizException("计划数量必须大于 0");
        }
        if (bomService.componentsOf(InventoryService.MATERIAL, materialId).isEmpty()) {
            throw new BizException("半成品没有 BOM：" + m.getCode());
        }
        WorkOrder wo = new WorkOrder();
        wo.setWoNo(docNo.next("WO", planStart));
        wo.setItemType(InventoryService.MATERIAL);
        wo.setMaterialId(materialId);
        wo.setParentWoId(parentWoId);
        wo.setLineCode(m.getLineCode() == null ? "L7" : m.getLineCode());
        wo.setPlannedQty(qty);
        wo.setStatus("PLANNED");
        wo.setPlanStart(planStart);
        wo.setPlanEnd(planEnd);
        return workOrders.save(wo);
    }

    @Transactional
    public WorkOrder release(Long woId) {
        WorkOrder wo = wo(woId);
        requireStatus(wo, "PLANNED");
        wo.setStatus("RELEASED");
        return wo;
    }

    @Transactional
    public WoReport report(Long woId, LocalDate date, BigDecimal good, BigDecimal scrap, String defectCode, String operator) {
        WorkOrder wo = wo(woId);
        requireStatus(wo, "RELEASED", "IN_PROGRESS");
        good = Money.nz(good);
        scrap = Money.nz(scrap);
        BigDecimal total = good.add(scrap);
        if (good.signum() < 0 || scrap.signum() < 0 || total.signum() == 0) {
            throw new BizException("报工数量无效");
        }
        BigDecimal remaining = wo.getPlannedQty().subtract(wo.getGoodQty()).subtract(wo.getScrapQty());
        if (total.compareTo(remaining) > 0) {
            throw new BizException("报工数量超过工单剩余数量 " + remaining.stripTrailingZeros().toPlainString());
        }
        boolean semi = InventoryService.MATERIAL.equals(wo.getItemType());
        String itemType = semi ? InventoryService.MATERIAL : InventoryService.PRODUCT;
        long itemId = semi ? wo.getMaterialId() : wo.getProductId();
        String code;
        BigDecimal unitConversion;
        if (semi) {
            Material m = md.material(itemId);
            code = m.getCode();
            unitConversion = m.getConversionCost() == null ? BigDecimal.ZERO : m.getConversionCost();
        } else {
            Product p = md.product(itemId);
            code = p.getCode();
            unitConversion = p.getConversionCost();
        }

        // 1. 倒冲领料：BOM 用量 × (良品 + 报废)；主料不足时按优先级用替代料补足
        Map<String, BigDecimal> costByAccount = new TreeMap<>();
        for (BomService.Component c : bomService.componentsOf(itemType, itemId)) {
            BigDecimal need = c.qtyPer().multiply(total).setScale(3, RoundingMode.UP);
            for (SubstituteAllocator.Take t : allocate(c.materialId(), need, date)) {
                BigDecimal unitCost = inventory.issue(new InventoryService.Movement(InventoryService.MATERIAL,
                        t.materialId(), t.qty(), date, "WO_ISSUE", "WORK_ORDER", wo.getWoNo()));
                costByAccount.merge(inventory.stockAccount(InventoryService.MATERIAL, t.materialId()),
                        Money.amt(t.qty().multiply(unitCost)), BigDecimal::add);
                if (t.substitute()) {
                    jdbc.update("INSERT INTO mes_wo_substitution (wo_id, material_id, substitute_id, primary_qty, substitute_qty, txn_date) VALUES (?,?,?,?,?,?)",
                            woId, c.materialId(), t.materialId(), t.coversPrimary(), t.qty(), date);
                }
            }
        }
        // 先分别取整到分再相加，保证凭证借贷平衡
        BigDecimal materialCost = costByAccount.values().stream().reduce(BigDecimal.ZERO, BigDecimal::add);
        BigDecimal conversion = Money.amt(unitConversion.multiply(total));
        BigDecimal produced = materialCost.add(conversion);
        String outputAccount = semi ? GlService.SEMI : GlService.FG;
        List<Line> v = new ArrayList<>();
        v.add(Line.dr(GlService.WIP, produced, "生产成本"));
        costByAccount.forEach((acc, amt) -> v.add(Line.cr(acc, amt, GlService.SEMI.equals(acc) ? "领用半成品" : "生产领料")));
        v.add(Line.cr(GlService.OVERHEAD, conversion, "分配加工费"));
        v.add(good.signum() > 0 ? Line.dr(outputAccount, produced, semi ? "半成品完工入库" : "完工入库")
                : Line.dr(GlService.NON_OPERATING, produced, "整批报废损失"));
        v.add(Line.cr(GlService.WIP, produced, "结转生产成本"));
        gl.post(date, "WORK_ORDER", wo.getWoNo(), (semi ? "组件报工 " : "生产报工 ") + code, v, null);

        // 2. 良品完工入库（成品 → 成品总仓，半成品 → 半成品仓），成本含报废分摊
        if (good.signum() > 0) {
            inventory.receive(new InventoryService.Movement(itemType, itemId, good, date,
                    "WO_COMPLETE", "WORK_ORDER", wo.getWoNo()), produced.divide(good, 4, RoundingMode.HALF_UP));
        }

        WoReport r = new WoReport();
        r.setWoId(woId);
        r.setReportDate(date);
        r.setGoodQty(good);
        r.setScrapQty(scrap);
        r.setDefectCode(scrap.signum() > 0 ? defectCode : null);
        r.setOperator(operator);
        reports.save(r);

        wo.setGoodQty(wo.getGoodQty().add(good));
        wo.setScrapQty(wo.getScrapQty().add(scrap));
        wo.setMaterialCost(Money.amt(wo.getMaterialCost().add(materialCost)));
        wo.setConversionCost(Money.amt(wo.getConversionCost().add(conversion)));
        if (wo.getActualStart() == null) {
            wo.setActualStart(date);
        }
        if (wo.getGoodQty().add(wo.getScrapQty()).compareTo(wo.getPlannedQty()) >= 0) {
            wo.setStatus("COMPLETED");
            wo.setActualEnd(date);
        } else {
            wo.setStatus("IN_PROGRESS");
        }
        return r;
    }

    /** 强制完工（短关闭），剩余数量不再生产 */
    @Transactional
    public WorkOrder close(Long woId, LocalDate date) {
        WorkOrder wo = wo(woId);
        requireStatus(wo, "RELEASED", "IN_PROGRESS");
        wo.setStatus("COMPLETED");
        wo.setActualEnd(date);
        return wo;
    }

    private List<SubstituteAllocator.Take> allocate(long materialId, BigDecimal need, LocalDate date) {
        BigDecimal primary = inventory.available(InventoryService.MATERIAL, materialId);
        if (primary.compareTo(need) >= 0) {
            return List.of(new SubstituteAllocator.Take(materialId, need, need, false));
        }
        List<SubstituteAllocator.Option> options = bomService.substitutesOf(materialId, date).stream()
                .map(s -> new SubstituteAllocator.Option(s.substituteId(), s.ratio(), inventory.available(InventoryService.MATERIAL, s.substituteId())))
                .toList();
        try {
            return SubstituteAllocator.allocate(materialId, need, primary, options);
        } catch (IllegalStateException e) {
            throw new BizException("物料 " + md.material(materialId).getCode() + " 库存不足（现存 "
                    + primary.stripTrailingZeros().toPlainString() + "，需要 " + need.stripTrailingZeros().toPlainString() + "）：" + e.getMessage());
        }
    }

    private WorkOrder wo(Long id) {
        return workOrders.findById(id).orElseThrow(() -> new NotFoundException("生产工单", id));
    }

    private static void requireStatus(WorkOrder wo, String... allowed) {
        for (String s : allowed) {
            if (s.equals(wo.getStatus())) {
                return;
            }
        }
        throw new BizException("工单 " + wo.getWoNo() + " 当前状态为 " + wo.getStatus() + "，不能执行此操作");
    }
}
