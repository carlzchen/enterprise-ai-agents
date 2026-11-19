package com.minierp.quality;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import com.minierp.inventory.InventoryService;
import com.minierp.gl.GlService;
import com.minierp.gl.VoucherValidator.Line;
import java.util.List;
import com.minierp.masterdata.MasterDataService;
import com.minierp.purchase.GoodsReceipt;
import com.minierp.purchase.GoodsReceiptLine;
import com.minierp.purchase.GoodsReceiptLineRepository;
import com.minierp.purchase.GoodsReceiptRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;

@Service
@RequiredArgsConstructor
public class QualityService {
    /** 允收水准：抽样不良率不超过 2% 判合格 */
    public static final BigDecimal AQL = new BigDecimal("0.02");

    private final IqcInspectionRepository iqcs;
    private final ComplaintRepository complaints;
    private final GoodsReceiptRepository receipts;
    private final GoodsReceiptLineRepository receiptLines;
    private final InventoryService inventory;
    private final MasterDataService md;
    private final DocNoService docNo;
    private final GlService gl;
    private final com.minierp.qms.QmsService qms;
    private final com.minierp.pm.PmService pm;
    private final com.minierp.kb.KbService kb;

    /** 来料检验：合格整批入库，不合格整批判退；到货单所有行检完即完成 */
    @Transactional
    public IqcInspection inspect(Long grLineId, LocalDate date, int sampleQty, int defectQty, String desc, String inspector) {
        GoodsReceiptLine line = receiptLines.findById(grLineId).orElseThrow(() -> new NotFoundException("到货行", grLineId));
        if (line.isInspected()) {
            throw new BizException("该到货行已检验");
        }
        if (sampleQty <= 0 || defectQty < 0 || defectQty > sampleQty) {
            throw new BizException("抽样数/不良数无效");
        }
        GoodsReceipt gr = receipts.findById(line.getGrId()).orElseThrow();
        boolean pass = BigDecimal.valueOf(defectQty)
                .divide(BigDecimal.valueOf(sampleQty), 4, RoundingMode.HALF_UP).compareTo(AQL) <= 0;

        IqcInspection q = new IqcInspection();
        q.setIqcNo(docNo.next("QC", date));
        q.setGrId(gr.getId());
        q.setGrLineId(line.getId());
        q.setMaterialId(line.getMaterialId());
        q.setSupplierId(gr.getSupplierId());
        q.setInspectDate(date);
        q.setLotQty(line.getQty());
        q.setSampleQty(sampleQty);
        q.setDefectQty(defectQty);
        q.setResult(pass ? "PASS" : "FAIL");
        q.setDefectDesc(desc);
        q.setInspector(inspector);
        iqcs.save(q);

        line.setInspected(true);
        if (pass) {
            line.setAcceptedQty(line.getQty());
            inventory.receive(new InventoryService.Movement(InventoryService.MATERIAL, line.getMaterialId(),
                    line.getQty(), date, "PO_RECEIPT", "GOODS_RECEIPT", gr.getGrNo()), line.getUnitPrice());
            BigDecimal value = line.getQty().multiply(line.getUnitPrice());
            gl.post(date, "GOODS_RECEIPT", gr.getGrNo(), "采购入库（暂估应付）", List.of(
                    Line.dr(GlService.RAW, value, "原材料入库"), Line.cr(GlService.AP_ESTIMATE, value, "暂估应付")), null);
        } else {
            line.setRejectedQty(line.getQty());
        }
        receiptLines.flush();
        boolean allDone = receiptLines.findByGrId(gr.getId()).stream().allMatch(GoodsReceiptLine::isInspected);
        if (allDone) {
            gr.setStatus("COMPLETED");
        }
        return q;
    }

    @Transactional
    public Complaint createComplaint(Complaint c) {
        md.customer(c.getCustomerId());
        md.product(c.getProductId());
        c.setId(null);
        c.setComplaintNo(docNo.next("CC", c.getReceivedDate()));
        c.setStatus("OPEN");
        return complaints.save(c);
    }

    /** 状态流转：OPEN → ANALYZING → CLOSED；关闭时必须填写根因与纠正措施 */
    @Transactional
    public Complaint updateComplaint(Long id, String status, String rootCause, String action, LocalDate date) {
        Complaint c = complaints.findById(id).orElseThrow(() -> new NotFoundException("客诉", id));
        if (rootCause != null) {
            c.setRootCause(rootCause);
        }
        if (action != null) {
            c.setCorrectiveAction(action);
        }
        if (status != null && !status.equals(c.getStatus())) {
            boolean ok = ("OPEN".equals(c.getStatus()) && "ANALYZING".equals(status))
                    || ("ANALYZING".equals(c.getStatus()) && "CLOSED".equals(status));
            if (!ok) {
                throw new BizException("不允许从 " + c.getStatus() + " 变更为 " + status);
            }
            if ("CLOSED".equals(status) && (isBlank(c.getRootCause()) || isBlank(c.getCorrectiveAction()))) {
                throw new BizException("关闭客诉前必须填写根本原因与纠正措施");
            }
            c.setStatus(status);
            if ("ANALYZING".equals(status)) {
                qms.open8d(id, date, c.getOwner());
                if (!"LOW".equals(c.getSeverity())) {
                    pm.bugFromComplaint(id, date.atTime(10, 0)); // 质量问题自动转研发缺陷
                }
            }
            if ("CLOSED".equals(status)) {
                c.setClosedDate(date);
                qms.close8d(id, c.getRootCause(), c.getCorrectiveAction(), date);
                pm.closeBugsOfComplaint(id, date.atTime(16, 0));
                kb.publishFrom8d(id, date.atTime(17, 0)); // 8D 经验教训自动沉淀到知识库
            }
        }
        return c;
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }
}
