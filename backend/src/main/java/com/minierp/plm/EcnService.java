package com.minierp.plm;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.Money;
import com.minierp.common.NotFoundException;
import com.minierp.masterdata.BomItem;
import com.minierp.masterdata.MasterDataService;
import com.minierp.masterdata.Product;
import com.minierp.masterdata.ProductRepository;
import com.minierp.system.Roles;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.*;
import java.util.stream.Collectors;

/**
 * 工程变更 ECN：草稿 → 会签(研发/生产/质量/采购) → 批准 → 发布。
 * 发布时为每个受影响产品生成新 BOM 版本，旧版本作废，并同步生产用 BOM(md_bom)。
 * BOM 只能通过 ECN 修改，保证每一次变更都有审批与追溯。
 */
@Service
@RequiredArgsConstructor
public class EcnService {
    public static final List<String> REVIEW_ROLES = List.of(Roles.ENGINEER, Roles.PRODUCTION, Roles.QUALITY, Roles.PURCHASE);
    private static final Set<String> REASONS = Set.of("COST_DOWN", "QUALITY", "CUSTOMER", "SUPPLY", "DESIGN");

    private final EcnRepository ecns;
    private final EcnLineRepository ecnLines;
    private final EcnReviewRepository reviews;
    private final BomRevisionRepository revisions;
    private final BomRevisionLineRepository revisionLines;
    private final ProductRepository products;
    private final MasterDataService md;
    private final DocNoService docNo;
    private final JdbcTemplate jdbc;
    private final com.minierp.kb.KbService kb;

    public record LineReq(@NotNull Long productId, @NotBlank String action, @NotNull Long materialId, BigDecimal newQty) {
    }

    public record CreateReq(@NotBlank String title, @NotBlank String reason, String description,
                            @NotEmpty List<@Valid LineReq> lines) {
    }

    // ---------- BOM 版本 ----------

    /** 产品尚无版本记录时，以当前生产 BOM 建立初始 A 版 */
    @Transactional
    public BomRevision ensureInitialRevision(Long productId, LocalDate date) {
        return revisions.findByProductIdAndStatus(productId, "RELEASED").orElseGet(() -> {
            Product p = md.product(productId);
            BomRevision r = new BomRevision();
            r.setProductId(productId);
            r.setRevision(p.getRevision());
            r.setStatus("RELEASED");
            r.setEffectiveDate(date);
            revisions.save(r);
            for (BomItem b : md.bomOf(productId)) {
                saveLine(r.getId(), b.getMaterialId(), b.getQtyPer());
            }
            return r;
        });
    }

    // ---------- ECN 生命周期 ----------

    @Transactional
    public Ecn create(CreateReq req, String requester, LocalDate date) {
        if (!REASONS.contains(req.reason())) {
            throw new BizException("未知变更原因：" + req.reason());
        }
        // 先校验变更在当前 BOM 上可执行
        byProduct(req.lines().stream().map(this::toLine).toList())
                .forEach((pid, lines) -> applyChanges(currentBom(pid), lines));
        Ecn e = new Ecn();
        e.setEcnNo(docNo.next("ECN", date));
        e.setTitle(req.title());
        e.setReason(req.reason());
        e.setDescription(req.description());
        e.setStatus("DRAFT");
        e.setRequestedBy(requester);
        e.setRequestDate(date);
        ecns.save(e);
        for (LineReq l : req.lines()) {
            EcnLine line = toLine(l);
            line.setEcnId(e.getId());
            ecnLines.save(line);
        }
        return e;
    }

    @Transactional
    public Ecn submit(Long ecnId) {
        Ecn e = ecn(ecnId);
        requireStatus(e, "DRAFT");
        for (String role : REVIEW_ROLES) {
            EcnReview r = new EcnReview();
            r.setEcnId(ecnId);
            r.setReviewRole(role);
            r.setDecision("PENDING");
            reviews.save(r);
        }
        e.setStatus("REVIEW");
        return e;
    }

    /** 会签：会签人不能是申请人；任一方否决即否决，四方全部同意即批准 */
    @Transactional
    public Ecn review(Long ecnId, String role, String reviewer, boolean approve, String comment, LocalDate date) {
        Ecn e = ecn(ecnId);
        requireStatus(e, "REVIEW");
        if (reviewer.equals(e.getRequestedBy())) {
            throw new BizException("申请人不能会签自己的 ECN");
        }
        EcnReview r = reviews.findByEcnIdAndReviewRole(ecnId, role)
                .orElseThrow(() -> new BizException("角色 " + role + " 不在该 ECN 的会签名单中"));
        if (!"PENDING".equals(r.getDecision())) {
            throw new BizException(role + " 已会签");
        }
        if (!approve && (comment == null || comment.isBlank())) {
            throw new BizException("否决时必须填写意见");
        }
        r.setDecision(approve ? "APPROVE" : "REJECT");
        r.setReviewer(reviewer);
        r.setComment(comment);
        r.setReviewDate(date);
        reviews.flush();
        List<EcnReview> all = reviews.findByEcnId(ecnId);
        if (all.stream().anyMatch(x -> "REJECT".equals(x.getDecision()))) {
            e.setStatus("REJECTED");
        } else if (all.stream().allMatch(x -> "APPROVE".equals(x.getDecision()))) {
            e.setStatus("APPROVED");
        }
        return e;
    }

    @Transactional
    public Ecn release(Long ecnId, LocalDate date) {
        Ecn e = ecn(ecnId);
        requireStatus(e, "APPROVED");
        Map<String, Object> impact = impact(ecnId, date);
        Map<Long, List<EcnLine>> byProduct = byProduct(ecnLines.findByEcnId(ecnId));
        for (var entry : byProduct.entrySet()) {
            Long pid = entry.getKey();
            BomRevision current = ensureInitialRevision(pid, date);
            Map<Long, BigDecimal> newBom = applyChanges(currentBom(pid), entry.getValue());
            String next = nextRevision(current.getRevision());
            current.setStatus("OBSOLETE");
            current.setObsoleteDate(date);
            BomRevision r = new BomRevision();
            r.setProductId(pid);
            r.setRevision(next);
            r.setStatus("RELEASED");
            r.setEffectiveDate(date);
            r.setEcnId(ecnId);
            revisions.save(r);
            newBom.forEach((mid, q) -> saveLine(r.getId(), mid, q));
            md.replaceBom(pid, newBom.entrySet().stream()
                    .map(x -> new MasterDataService.BomLine(x.getKey(), x.getValue())).toList());
            Product p = md.product(pid);
            p.setRevision(next);
            products.save(p);
        }
        e.setStatus("RELEASED");
        e.setEffectiveDate(date);
        e.setUnitCostDelta((BigDecimal) impact.get("avgUnitCostDelta"));
        e.setAnnualImpact((BigDecimal) impact.get("annualImpact"));
        ecns.saveAndFlush(e);
        kb.publishFromEcn(e.getId(), date.atTime(17, 0)); // 设计变更说明自动沉淀到知识库
        return e;
    }

    // ---------- 影响分析 ----------

    /**
     * 对每个受影响产品：单台材料成本变化（按当月参考价）、近 12 个月销量折算的年度影响、
     * 在制工单（发布后将按新 BOM 倒冲）、被移除物料若无其他产品使用则计为呆滞风险。
     */
    public Map<String, Object> impact(Long ecnId, LocalDate date) {
        boolean released = "RELEASED".equals(ecn(ecnId).getStatus());
        Map<Long, List<EcnLine>> byProduct = byProduct(ecnLines.findByEcnId(ecnId));
        List<Map<String, Object>> rows = new ArrayList<>();
        BigDecimal annual = BigDecimal.ZERO, deltaSum = BigDecimal.ZERO;
        Set<Long> removedMaterials = new HashSet<>();
        for (var entry : byProduct.entrySet()) {
            Long pid = entry.getKey();
            Product p = md.product(pid);
            // 已发布的 ECN：对比其发布前后的两个 BOM 版本；未发布：在当前 BOM 上模拟变更
            Map<Long, BigDecimal> before, after;
            String revBefore = p.getRevision(), revAfter = nextRevision(p.getRevision());
            if (released) {
                Long revId = jdbc.queryForObject("SELECT id FROM plm_bom_revision WHERE product_id = ? AND ecn_id = ?", Long.class, pid, ecnId);
                Long prevId = jdbc.queryForObject("SELECT MAX(id) FROM plm_bom_revision WHERE product_id = ? AND id < ?", Long.class, pid, revId);
                before = revisionBom(prevId);
                after = revisionBom(revId);
                revBefore = jdbc.queryForObject("SELECT revision FROM plm_bom_revision WHERE id = ?", String.class, prevId);
                revAfter = jdbc.queryForObject("SELECT revision FROM plm_bom_revision WHERE id = ?", String.class, revId);
            } else {
                before = currentBom(pid);
                after = applyChanges(before, entry.getValue());
            }
            BigDecimal delta = unitCost(after, date).subtract(unitCost(before, date));
            BigDecimal volume = jdbc.queryForObject("""
                    SELECT COALESCE(SUM(dl.qty), 0) FROM sal_delivery_line dl JOIN sal_delivery d ON d.id = dl.delivery_id
                    WHERE dl.product_id = ? AND d.delivery_date > ? AND d.delivery_date <= ?""",
                    BigDecimal.class, pid, date.minusYears(1), date);
            Map<String, Object> wip = jdbc.queryForMap("""
                    SELECT COUNT(*) AS cnt, COALESCE(SUM(planned_qty - good_qty - scrap_qty), 0) AS qty
                    FROM mes_work_order WHERE product_id = ? AND status IN ('PLANNED','RELEASED','IN_PROGRESS')""", pid);
            if (!released) {
                before.keySet().stream().filter(m -> !after.containsKey(m)).forEach(removedMaterials::add);
            }
            BigDecimal productAnnual = Money.amt(delta.multiply(volume));
            annual = annual.add(productAnnual);
            deltaSum = deltaSum.add(delta);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("productId", pid);
            row.put("productCode", p.getCode());
            row.put("productName", p.getName());
            row.put("currentRevision", revBefore);
            row.put("nextRevision", revAfter);
            row.put("unitCostBefore", Money.price(unitCost(before, date)));
            row.put("unitCostAfter", Money.price(unitCost(after, date)));
            row.put("unitCostDelta", Money.price(delta));
            row.put("annualVolume", volume);
            row.put("annualImpact", productAnnual);
            row.put("openWorkOrders", wip.get("cnt"));
            row.put("openWorkOrderQty", wip.get("qty"));
            rows.add(row);
        }
        List<Map<String, Object>> obsolete = new ArrayList<>();
        BigDecimal obsoleteValue = BigDecimal.ZERO;
        for (Long mid : removedMaterials) {
            Integer stillUsed = jdbc.queryForObject(
                    "SELECT COUNT(*) FROM md_bom WHERE material_id = ? AND product_id NOT IN (" + placeholders(byProduct.size()) + ")",
                    Integer.class, concat(mid, byProduct.keySet()));
            if (stillUsed != null && stillUsed > 0) {
                continue;
            }
            Map<String, Object> s = jdbc.queryForMap("""
                    SELECT m.code, m.name, COALESCE(st.qty, 0) AS qty, COALESCE(ROUND(st.qty * st.avg_cost, 2), 0) AS value
                    FROM md_material m LEFT JOIN inv_stock st ON st.item_type = 'MATERIAL' AND st.item_id = m.id WHERE m.id = ?""", mid);
            obsoleteValue = obsoleteValue.add((BigDecimal) s.get("value"));
            obsolete.add(s);
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("products", rows);
        result.put("avgUnitCostDelta", rows.isEmpty() ? BigDecimal.ZERO
                : deltaSum.divide(BigDecimal.valueOf(rows.size()), 4, RoundingMode.HALF_UP));
        result.put("annualImpact", Money.amt(annual));
        result.put("obsoleteMaterials", obsolete);
        result.put("obsoleteValue", Money.amt(obsoleteValue));
        return result;
    }

    public Map<String, Object> detail(Long id) {
        List<Map<String, Object>> head = jdbc.queryForList("SELECT * FROM v_ecn WHERE id = ?", id);
        if (head.isEmpty()) {
            throw new NotFoundException("ECN", id);
        }
        Map<String, Object> r = new HashMap<>(head.get(0));
        r.put("lines", jdbc.queryForList("SELECT * FROM v_ecn_line WHERE ecn_id = ? ORDER BY product_code, id", id));
        r.put("reviews", jdbc.queryForList("SELECT * FROM plm_ecn_review WHERE ecn_id = ? ORDER BY FIELD(review_role,'ENGINEER','PRODUCTION','QUALITY','PURCHASE')", id));
        return r;
    }

    /** BOM 版本详情，附与上一版本的差异 */
    public Map<String, Object> revisionDetail(Long id) {
        List<Map<String, Object>> head = jdbc.queryForList("SELECT * FROM v_bom_revision WHERE id = ?", id);
        if (head.isEmpty()) {
            throw new NotFoundException("BOM 版本", id);
        }
        Map<String, Object> r = new HashMap<>(head.get(0));
        List<Map<String, Object>> lines = jdbc.queryForList("SELECT * FROM v_bom_revision_line WHERE revision_id = ? ORDER BY material_code", id);
        List<Map<String, Object>> prevHead = jdbc.queryForList(
                "SELECT id, revision FROM plm_bom_revision WHERE product_id = ? AND id < ? ORDER BY id DESC LIMIT 1",
                r.get("product_id"), id);
        Map<Object, BigDecimal> prev = new HashMap<>();
        if (!prevHead.isEmpty()) {
            r.put("previousRevision", prevHead.get(0).get("revision"));
            jdbc.queryForList("SELECT * FROM v_bom_revision_line WHERE revision_id = ?", prevHead.get(0).get("id"))
                    .forEach(l -> prev.put(l.get("material_code"), (BigDecimal) l.get("qty_per")));
            List<Map<String, Object>> removed = jdbc.queryForList(
                    "SELECT * FROM v_bom_revision_line WHERE revision_id = ?", prevHead.get(0).get("id")).stream()
                    .filter(l -> lines.stream().noneMatch(x -> x.get("material_code").equals(l.get("material_code"))))
                    .map(l -> {
                        Map<String, Object> m = new HashMap<>(l);
                        m.put("change", "REMOVED");
                        m.put("previous_qty", l.get("qty_per"));
                        m.put("qty_per", null);
                        return m;
                    }).toList();
            r.put("removed", removed);
        }
        for (Map<String, Object> l : lines) {
            BigDecimal before = prev.get(l.get("material_code"));
            l.put("previous_qty", before);
            l.put("change", prevHead.isEmpty() ? null : before == null ? "ADDED"
                    : before.compareTo((BigDecimal) l.get("qty_per")) != 0 ? "CHANGED" : null);
        }
        r.put("lines", lines);
        return r;
    }

    // ---------- 内部 ----------

    private Map<Long, BigDecimal> currentBom(Long productId) {
        return md.bomOf(productId).stream().collect(Collectors.toMap(BomItem::getMaterialId, BomItem::getQtyPer,
                (a, b) -> a, LinkedHashMap::new));
    }

    private Map<Long, BigDecimal> revisionBom(Long revisionId) {
        Map<Long, BigDecimal> m = new LinkedHashMap<>();
        revisionLines.findByRevisionId(revisionId).forEach(l -> m.put(l.getMaterialId(), l.getQtyPer()));
        return m;
    }

    private Map<Long, BigDecimal> applyChanges(Map<Long, BigDecimal> bom, List<EcnLine> lines) {
        for (EcnLine l : lines) {
            md.material(l.getMaterialId());
        }
        try {
            return BomChangeCalculator.apply(bom, lines.stream()
                    .map(l -> new BomChangeCalculator.Change(l.getAction(), l.getMaterialId(), l.getNewQty())).toList());
        } catch (IllegalArgumentException e) {
            String code = lines.isEmpty() ? "" : md.product(lines.get(0).getProductId()).getCode() + "：";
            throw new BizException(code + e.getMessage());
        }
    }

    private BigDecimal unitCost(Map<Long, BigDecimal> bom, LocalDate date) {
        return bom.entrySet().stream().map(e -> e.getValue().multiply(md.materialPrice(e.getKey(), date)))
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    private EcnLine toLine(LineReq l) {
        EcnLine line = new EcnLine();
        line.setProductId(l.productId());
        line.setAction(l.action());
        line.setMaterialId(l.materialId());
        line.setNewQty(l.newQty());
        return line;
    }

    private static Map<Long, List<EcnLine>> byProduct(List<EcnLine> lines) {
        return lines.stream().collect(Collectors.groupingBy(EcnLine::getProductId, LinkedHashMap::new, Collectors.toList()));
    }

    private void saveLine(Long revisionId, Long materialId, BigDecimal qty) {
        BomRevisionLine l = new BomRevisionLine();
        l.setRevisionId(revisionId);
        l.setMaterialId(materialId);
        l.setQtyPer(qty);
        revisionLines.save(l);
    }

    static String nextRevision(String rev) {
        return BomChangeCalculator.nextRevision(rev);
    }

    private static BigDecimal positive(BigDecimal q) {
        if (q == null || q.signum() <= 0) {
            throw new BizException("新用量必须大于 0");
        }
        return q;
    }

    private static String placeholders(int n) {
        return String.join(",", Collections.nCopies(n, "?"));
    }

    private static Object[] concat(Long first, Collection<Long> rest) {
        List<Object> all = new ArrayList<>();
        all.add(first);
        all.addAll(rest);
        return all.toArray();
    }

    private Ecn ecn(Long id) {
        return ecns.findById(id).orElseThrow(() -> new NotFoundException("ECN", id));
    }

    private static void requireStatus(Ecn e, String status) {
        if (!status.equals(e.getStatus())) {
            throw new BizException("ECN " + e.getEcnNo() + " 当前状态为 " + e.getStatus() + "，不能执行此操作");
        }
    }
}
