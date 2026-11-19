package com.minierp.kb;

import com.minierp.common.BizException;
import com.minierp.common.NotFoundException;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Date;
import java.sql.Timestamp;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.*;

/**
 * 知识库：空间 / 页面树 / 版本历史与对比 / 中文全文检索（MySQL ngram）。
 * 经验自动沉淀：8D 结案 → 质量经验页；ECN 发布 → 设计变更说明页；设备同类故障 → 维修案例页（按版本追加）；
 * 工艺路线 → 作业指导书；已发布页面通过导出接口同步到技术助手（RAG）。
 */
@Service
@RequiredArgsConstructor
public class KbService {
    private final JdbcTemplate jdbc;

    public List<Map<String, Object>> spaces() {
        return jdbc.queryForList("""
                SELECT s.*, (SELECT COUNT(*) FROM kb_page p WHERE p.space_id = s.id AND p.status = 'PUBLISHED') AS page_count,
                       (SELECT MAX(p.updated_at) FROM kb_page p WHERE p.space_id = s.id) AS last_updated FROM kb_space s ORDER BY s.id""");
    }

    public List<Map<String, Object>> tree(String spaceKey) {
        List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT id, parent_id, title, status, version, source_type, updated_at, author_name FROM v_kb_page
                WHERE space_key = ? AND status <> 'ARCHIVED' ORDER BY title""", spaceKey);
        Map<Object, Map<String, Object>> byId = new LinkedHashMap<>();
        rows.forEach(r -> {
            Map<String, Object> m = new LinkedHashMap<>(r);
            m.put("children", new ArrayList<Map<String, Object>>());
            byId.put(r.get("id"), m);
        });
        List<Map<String, Object>> roots = new ArrayList<>();
        for (Map<String, Object> m : byId.values()) {
            Map<String, Object> p = m.get("parent_id") == null ? null : byId.get(m.get("parent_id"));
            if (p == null) {
                roots.add(m);
            } else {
                @SuppressWarnings("unchecked") List<Map<String, Object>> kids = (List<Map<String, Object>>) p.get("children");
                kids.add(m);
            }
        }
        return roots;
    }

    @Transactional
    public Map<String, Object> page(long id) {
        jdbc.update("UPDATE kb_page SET view_count = view_count + 1 WHERE id = ?", id);
        Map<String, Object> p = new LinkedHashMap<>(jdbc.queryForList("SELECT * FROM v_kb_page WHERE id = ?", id).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("知识页面", id)));
        p.put("body", jdbc.queryForObject("SELECT body FROM kb_page WHERE id = ?", String.class, id));
        p.put("versions", jdbc.queryForList("""
                SELECT v.version, v.title, v.saved_at, v.change_note, e.name AS author_name FROM kb_page_version v
                LEFT JOIN hr_employee e ON e.id = v.author_emp_id WHERE v.page_id = ? ORDER BY v.version DESC""", id));
        p.put("breadcrumb", breadcrumb(id));
        return p;
    }

    /** 新建或保存页面：保存即产生新版本（旧版本进入历史）；publish=false 保存为草稿 */
    @Transactional
    public long save(Long id, String spaceKey, Long parentId, String title, String body, String labels, boolean publish, Long authorEmpId,
                     String changeNote, String sourceType, String sourceRef, LocalDateTime at) {
        if (title == null || title.isBlank() || body == null) {
            throw new BizException("标题和内容必填");
        }
        if (id == null) {
            Long space = jdbc.queryForList("SELECT id FROM kb_space WHERE space_key = ?", Long.class, spaceKey).stream().findFirst()
                    .orElseThrow(() -> new NotFoundException("知识空间", spaceKey));
            jdbc.update("""
                    INSERT INTO kb_page (space_id, parent_id, title, body, version, status, labels, source_type, source_ref, author_emp_id, created_at, updated_at)
                    VALUES (?,?,?,?,1,?,?,?,?,?,?,?)""", space, parentId, title, body, publish ? "PUBLISHED" : "DRAFT", labels,
                    sourceType == null ? "MANUAL" : sourceType, sourceRef, authorEmpId, Timestamp.valueOf(at), Timestamp.valueOf(at));
            long newId = jdbc.queryForObject("SELECT MAX(id) FROM kb_page WHERE space_id = ?", Long.class, space);
            jdbc.update("INSERT INTO kb_page_version (page_id, version, title, body, author_emp_id, saved_at, change_note) VALUES (?,1,?,?,?,?,?)",
                    newId, title, body, authorEmpId, Timestamp.valueOf(at), changeNote == null ? "创建" : changeNote);
            return newId;
        }
        Map<String, Object> old = jdbc.queryForList("SELECT * FROM kb_page WHERE id = ? FOR UPDATE", id).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("知识页面", id));
        if (title.equals(old.get("title")) && body.equals(old.get("body"))) {
            if (publish && !"PUBLISHED".equals(old.get("status"))) {
                jdbc.update("UPDATE kb_page SET status = 'PUBLISHED', updated_at = ? WHERE id = ?", Timestamp.valueOf(at), id);
            }
            return id;
        }
        int v = ((Number) old.get("version")).intValue() + 1;
        jdbc.update("UPDATE kb_page SET title = ?, body = ?, version = ?, status = ?, labels = COALESCE(?, labels), updated_at = ? WHERE id = ?",
                title, body, v, publish ? "PUBLISHED" : old.get("status"), labels, Timestamp.valueOf(at), id);
        jdbc.update("INSERT INTO kb_page_version (page_id, version, title, body, author_emp_id, saved_at, change_note) VALUES (?,?,?,?,?,?,?)",
                id, v, title, body, authorEmpId, Timestamp.valueOf(at), changeNote);
        return id;
    }

    @Transactional
    public void archive(long id) {
        if (jdbc.update("UPDATE kb_page SET status = 'ARCHIVED' WHERE id = ?", id) == 0) {
            throw new NotFoundException("知识页面", id);
        }
    }

    public Map<String, Object> diff(long pageId, int v1, int v2) {
        String a = version(pageId, v1), b = version(pageId, v2);
        List<TextDiff.Line> lines = TextDiff.diff(a, b);
        return Map.of("pageId", pageId, "from", v1, "to", v2, "lines", lines,
                "added", lines.stream().filter(l -> "ADD".equals(l.op())).count(), "deleted", lines.stream().filter(l -> "DEL".equals(l.op())).count());
    }

    /** 全文检索（ngram 分词，自然语言模式按相关度排序），返回命中片段 */
    public List<Map<String, Object>> search(String q, String spaceKey, int limit) {
        if (q == null || q.trim().length() < 2) {
            throw new BizException("关键词至少 2 个字");
        }
        String kw = q.trim();
        List<Map<String, Object>> rows = jdbc.queryForList("""
                SELECT p.id, p.title, s.space_key, s.name AS space_name, p.updated_at, p.source_type, p.body,
                       MATCH(p.title, p.body) AGAINST (? IN NATURAL LANGUAGE MODE) AS score
                FROM kb_page p JOIN kb_space s ON s.id = p.space_id
                WHERE p.status = 'PUBLISHED' AND MATCH(p.title, p.body) AGAINST (? IN NATURAL LANGUAGE MODE) AND (? IS NULL OR s.space_key = ?)
                ORDER BY score DESC LIMIT ?""", kw, kw, spaceKey, spaceKey, Math.min(limit, 50));
        for (Map<String, Object> r : rows) {
            String body = (String) r.remove("body");
            int i = body.indexOf(kw);
            int s = Math.max(0, (i < 0 ? 0 : i) - 40);
            r.put("snippet", body.substring(s, Math.min(body.length(), s + 160)).replaceAll("[#|*`>\\-]{2,}", " ").replace("\n", " "));
        }
        return rows;
    }

    /** 给技术助手（RAG）增量同步用：已发布页面及版本号 */
    public List<Map<String, Object>> exportPublished(LocalDate updatedSince) {
        return jdbc.queryForList("""
                SELECT p.id, p.title, p.version, p.body, p.labels, p.source_type, s.space_key, s.name AS space_name, p.updated_at
                FROM kb_page p JOIN kb_space s ON s.id = p.space_id WHERE p.status = 'PUBLISHED' AND (? IS NULL OR p.updated_at >= ?) ORDER BY p.id""",
                updatedSince == null ? null : Date.valueOf(updatedSince), updatedSince == null ? null : Date.valueOf(updatedSince));
    }

    // ---------- 经验自动沉淀 ----------

    /** 8D 结案 → 质量经验页（同一客诉只生成一次） */
    @Transactional
    public void publishFrom8d(long complaintId, LocalDateTime at) {
        List<Map<String, Object>> r = jdbc.queryForList("SELECT * FROM v_8d WHERE complaint_id = ? AND status = 'CLOSED'", complaintId);
        if (r.isEmpty() || existing("8D", String.valueOf(r.get(0).get("report_no"))) != null) {
            return;
        }
        Map<String, Object> d = r.get(0);
        String body = """
                # %s 经验教训

                | 项目 | 内容 |
                |---|---|
                | 8D 编号 | %s |
                | 客诉 | %s（%s） |
                | 产品 | %s |
                | 不良类型 | %s，严重度 %s |
                | 周期 | %s ~ %s（%s 天） |

                ## 问题描述（D2）
                %s

                ## 临时措施（D3）
                %s

                ## 根本原因（D4）
                %s

                ## 永久措施（D5）
                %s

                ## 效果验证（D6）
                %s

                ## 预防再发（D7）
                %s
                """.formatted(d.get("product_code") + " " + d.get("defect_type"), d.get("report_no"), d.get("complaint_no"), d.get("customer_name"),
                d.get("product_code"), d.get("defect_type"), d.get("severity"), d.get("opened_date"), d.get("closed_date"), d.get("age_days"),
                d.get("d2_problem"), d.get("d3_containment"), d.get("d4_root_cause"), d.get("d5_actions"), d.get("d6_verification"), d.get("d7_prevention"));
        save(null, "QUAL", folder("QUAL", "8D 经验教训", at), "8D 经验：" + d.get("product_code") + " " + defectName((String) d.get("defect_type"))
                        + "（" + d.get("report_no") + "）", body, "8D," + d.get("defect_type"), true, null, "8D 结案自动沉淀", "8D",
                String.valueOf(d.get("report_no")), at);
    }

    /** ECN 发布 → 设计变更说明页 */
    @Transactional
    public void publishFromEcn(long ecnId, LocalDateTime at) {
        Map<String, Object> e = jdbc.queryForList("SELECT * FROM v_ecn WHERE id = ?", ecnId).stream().findFirst().orElse(null);
        if (e == null || existing("ECN", String.valueOf(e.get("ecn_no"))) != null) {
            return;
        }
        List<Map<String, Object>> lines = jdbc.queryForList("""
                SELECT DISTINCT l.action, m.code AS material_code, m.name AS material_name FROM plm_ecn_line l JOIN md_material m ON m.id = l.material_id
                WHERE l.ecn_id = ?""", ecnId);
        StringBuilder sb = new StringBuilder("# " + e.get("title") + "\n\n")
                .append("| 项目 | 内容 |\n|---|---|\n")
                .append("| ECN | ").append(e.get("ecn_no")).append(" |\n")
                .append("| 原因 | ").append(e.get("reason")).append(" |\n")
                .append("| 生效日期 | ").append(e.get("effective_date")).append(" |\n")
                .append("| 影响产品数 | ").append(e.get("product_count")).append(" |\n")
                .append("| 单台材料成本变化 | ").append(e.get("unit_cost_delta")).append(" 元 |\n")
                .append("| 年度成本影响 | ").append(e.get("annual_impact")).append(" 元 |\n\n")
                .append("## 变更说明\n").append(e.get("description") == null ? "" : e.get("description")).append("\n\n## 物料变更\n");
        lines.forEach(l -> sb.append("- ").append(l.get("action")).append("：").append(l.get("material_code")).append(" ").append(l.get("material_name")).append("\n"));
        save(null, "RND", folder("RND", "设计变更（ECN）", at), "设计变更：" + e.get("title"), sb.toString(), "ECN," + e.get("reason"), true,
                null, "ECN 发布自动沉淀", "ECN", String.valueOf(e.get("ecn_no")), at);
    }

    /** 设备维修案例：每台设备一页，按月追加新案例（产生新版本） */
    @Transactional
    public int publishEquipmentCases(LocalDate from, LocalDate to, LocalDateTime at) {
        List<Map<String, Object>> eqs = jdbc.queryForList("""
                SELECT equipment_id, equipment_code, equipment_name, COUNT(*) AS n FROM v_eam_work_order
                WHERE type = 'BREAKDOWN' AND DATE(reported_at) BETWEEN ? AND ? GROUP BY equipment_id, equipment_code, equipment_name HAVING n >= 2""",
                Date.valueOf(from), Date.valueOf(to));
        for (Map<String, Object> e : eqs) {
            String code = (String) e.get("equipment_code");
            StringBuilder cases = new StringBuilder("\n## " + from.toString().substring(0, 7) + "（" + e.get("n") + " 次故障）\n\n| 日期 | 现象 | 原因 | 处理 | 停机(分) |\n|---|---|---|---|---|\n");
            jdbc.queryForList("""
                    SELECT DATE(reported_at) AS d, fault_desc, cause, action, downtime_min FROM eam_work_order
                    WHERE equipment_id = ? AND type = 'BREAKDOWN' AND DATE(reported_at) BETWEEN ? AND ? ORDER BY reported_at""",
                    e.get("equipment_id"), Date.valueOf(from), Date.valueOf(to)).forEach(w -> cases.append("| ").append(w.get("d")).append(" | ")
                    .append(w.get("fault_desc")).append(" | ").append(w.get("cause")).append(" | ").append(w.get("action")).append(" | ")
                    .append(w.get("downtime_min")).append(" |\n"));
            Long pageId = existing("EAM", code);
            if (pageId == null) {
                save(null, "EQP", folder("EQP", "设备故障案例", at), "故障案例：" + e.get("equipment_name") + "（" + code + "）",
                        "# " + e.get("equipment_name") + " 故障案例\n\n同类故障反复出现时，优先查看历史处理方法并评估是否需要缩短保养周期。\n" + cases,
                        "设备,维修", true, null, "设备故障自动沉淀", "EAM", code, at);
            } else {
                String body = jdbc.queryForObject("SELECT body FROM kb_page WHERE id = ?", String.class, pageId);
                String title = jdbc.queryForObject("SELECT title FROM kb_page WHERE id = ?", String.class, pageId);
                save(pageId, null, null, title, body + cases, null, true, null, "追加 " + from.toString().substring(0, 7) + " 故障案例", null, null, at);
            }
        }
        return eqs.size();
    }

    // ---------- 内部 ----------

    private Long existing(String sourceType, String ref) {
        return jdbc.queryForList("SELECT id FROM kb_page WHERE source_type = ? AND source_ref = ?", Long.class, sourceType, ref).stream().findFirst().orElse(null);
    }

    /** 空间下的目录页（不存在则创建） */
    public Long folder(String spaceKey, String title, LocalDateTime at) {
        Long id = jdbc.queryForList("SELECT p.id FROM kb_page p JOIN kb_space s ON s.id = p.space_id WHERE s.space_key = ? AND p.title = ? AND p.parent_id IS NULL",
                Long.class, spaceKey, title).stream().findFirst().orElse(null);
        return id != null ? id : save(null, spaceKey, null, title, "# " + title + "\n\n本目录下的页面由系统自动沉淀或人工整理。", null, true, null, "创建目录", "MANUAL", null, at);
    }

    private String version(long pageId, int v) {
        return jdbc.queryForList("SELECT body FROM kb_page_version WHERE page_id = ? AND version = ?", String.class, pageId, v).stream().findFirst()
                .orElseThrow(() -> new NotFoundException("页面版本", pageId + " v" + v));
    }

    private List<Map<String, Object>> breadcrumb(long id) {
        LinkedList<Map<String, Object>> out = new LinkedList<>();
        Long cur = id;
        for (int i = 0; i < 10 && cur != null; i++) {
            Map<String, Object> p = jdbc.queryForMap("SELECT id, title, parent_id FROM kb_page WHERE id = ?", cur);
            out.addFirst(Map.of("id", p.get("id"), "title", p.get("title")));
            cur = p.get("parent_id") == null ? null : ((Number) p.get("parent_id")).longValue();
        }
        return out;
    }

    private static String defectName(String code) {
        return switch (code) {
            case "NOISE" -> "异响";
            case "ENCODER" -> "编码器故障";
            case "OVERHEAT" -> "过热";
            case "NO_START" -> "不启动";
            case "SPEED" -> "转速异常";
            default -> "外观不良";
        };
    }
}
