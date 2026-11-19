package com.minierp.plm;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/** 新产品开发 NPI：概念 → 设计 → 样机 → 试产 → 量产，每个阶段门评审 GO / HOLD / KILL */
@Service
@RequiredArgsConstructor
public class ProjectService {
    public static final List<String> STAGES = List.of("CONCEPT", "DESIGN", "PROTOTYPE", "PILOT", "MP");

    private final PlmProjectRepository projects;
    private final ProjectGateRepository gates;
    private final DocNoService docNo;

    @Transactional
    public PlmProject create(PlmProject p, LocalDate date) {
        if (p.getBudget() == null || p.getBudget().signum() <= 0) {
            throw new BizException("研发预算必须大于 0");
        }
        if (p.getTargetMpDate() == null || p.getStartDate() == null || p.getTargetMpDate().isBefore(p.getStartDate())) {
            throw new BizException("计划量产日期不能早于立项日期");
        }
        p.setId(null);
        p.setProjectNo(docNo.next("NPI", date));
        p.setStage("CONCEPT");
        p.setStatus("ACTIVE");
        p.setSpent(BigDecimal.ZERO);
        return projects.save(p);
    }

    /** 阶段门：GO 进入下一阶段（量产阶段 GO 即结项）；HOLD 暂停；KILL 终止；暂停中的项目 GO 为恢复 */
    @Transactional
    public PlmProject gate(Long id, String decision, String comment, String reviewer, LocalDate date) {
        PlmProject p = project(id);
        if (!List.of("ACTIVE", "ON_HOLD").contains(p.getStatus())) {
            throw new BizException("项目已" + ("CLOSED".equals(p.getStatus()) ? "结项" : "终止"));
        }
        if (!"GO".equals(decision) && (comment == null || comment.isBlank())) {
            throw new BizException("暂停或终止必须填写评审意见");
        }
        ProjectGate g = new ProjectGate();
        g.setProjectId(id);
        g.setFromStage(p.getStage());
        g.setDecision(decision);
        g.setComment(comment);
        g.setReviewer(reviewer);
        g.setGateDate(date);
        switch (decision) {
            case "GO" -> {
                if ("ON_HOLD".equals(p.getStatus())) {
                    p.setStatus("ACTIVE");
                    g.setToStage(p.getStage());
                } else if ("MP".equals(p.getStage())) {
                    p.setStatus("CLOSED");
                    g.setToStage("MP");
                } else {
                    String next = STAGES.get(STAGES.indexOf(p.getStage()) + 1);
                    p.setStage(next);
                    g.setToStage(next);
                }
            }
            case "HOLD" -> p.setStatus("ON_HOLD");
            case "KILL" -> p.setStatus("KILLED");
            default -> throw new BizException("未知评审结论：" + decision);
        }
        gates.save(g);
        return p;
    }

    @Transactional
    public void addSpent(Long id, BigDecimal amount) {
        PlmProject p = project(id);
        p.setSpent(p.getSpent().add(amount));
    }

    public List<ProjectGate> gates(Long id) {
        project(id);
        return gates.findByProjectIdOrderById(id);
    }

    private PlmProject project(Long id) {
        return projects.findById(id).orElseThrow(() -> new NotFoundException("研发项目", id));
    }
}
