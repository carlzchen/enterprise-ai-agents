package com.minierp.query;

import com.minierp.common.BizException;
import com.minierp.common.PageResult;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class ViewQueryService {
    private static final int MAX_SIZE = 500;
    private final JdbcTemplate jdbc;

    /** 视图目录：当前角色可访问的视图、可过滤列、关键字列、日期列（供前端 / 其他系统 / MCP 发现数据） */
    public List<Map<String, Object>> catalog(String role) {
        List<Map<String, Object>> out = new ArrayList<>();
        new java.util.TreeMap<>(ViewRegistry.VIEWS).forEach((key, def) -> {
            if (def.roles().isEmpty() || def.roles().contains(role)) {
                Map<String, Object> m = new java.util.LinkedHashMap<>();
                m.put("key", key);
                m.put("description", ViewRegistry.DESCRIPTIONS.getOrDefault(key, ""));
                m.put("filters", new java.util.TreeSet<>(def.filters()));
                m.put("keywordColumns", new java.util.TreeSet<>(def.keywordCols()));
                m.put("dateColumn", def.dateCol());
                m.put("defaultSort", def.defaultSort());
                m.put("sensitiveColumnsHidden", ViewRegistry.canSeeSensitive(role, key) ? List.of() : new java.util.TreeSet<>(def.sensitiveCols()));
                out.add(m);
            }
        });
        return out;
    }

    public PageResult<Map<String, Object>> page(String key, String role, Map<String, String> params) {
        ViewDef def = ViewRegistry.VIEWS.get(key);
        if (def == null) {
            throw new BizException("未知查询：" + key);
        }
        if (!def.roles().isEmpty() && !def.roles().contains(role)) {
            throw new AccessDeniedException(key);
        }
        int page = Math.max(1, parseInt(params.get("page"), 1));
        int size = Math.min(MAX_SIZE, Math.max(1, parseInt(params.get("size"), 20)));

        StringBuilder where = new StringBuilder(" WHERE 1=1");
        List<Object> args = new ArrayList<>();
        for (String col : def.filters()) {
            String v = params.get(col);
            if (v != null && !v.isBlank()) {
                where.append(" AND ").append(col).append(" = ?");
                args.add(v);
            }
        }
        String kw = params.get("kw");
        if (kw != null && !kw.isBlank() && !def.keywordCols().isEmpty()) {
            where.append(" AND (");
            where.append(String.join(" OR ", def.keywordCols().stream().map(c -> c + " LIKE ?").toList()));
            where.append(")");
            def.keywordCols().forEach(c -> args.add("%" + kw.trim() + "%"));
        }
        if (def.dateCol() != null) {
            if (notBlank(params.get("dateFrom"))) {
                where.append(" AND ").append(def.dateCol()).append(" >= ?");
                args.add(params.get("dateFrom"));
            }
            if (notBlank(params.get("dateTo"))) {
                where.append(" AND ").append(def.dateCol()).append(" <= ?");
                args.add(params.get("dateTo"));
            }
        }
        // 排序列只能是过滤列/日期列/默认列之一
        String sort = params.getOrDefault("sort", def.defaultSort());
        if (!(def.filters().contains(sort) || sort.equals(def.dateCol()) || sort.equals(def.defaultSort()) || sort.equals("id"))) {
            sort = def.defaultSort();
        }
        String dir = "asc".equalsIgnoreCase(params.get("order")) ? "ASC" : "DESC";

        Long total = jdbc.queryForObject("SELECT COUNT(*) FROM " + def.view() + where, Long.class, args.toArray());
        List<Object> pageArgs = new ArrayList<>(args);
        pageArgs.add(size);
        pageArgs.add((page - 1) * size);
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT * FROM " + def.view() + where + " ORDER BY " + sort + " " + dir + ", id " + dir + " LIMIT ? OFFSET ?",
                pageArgs.toArray());
        if (!ViewRegistry.canSeeSensitive(role, key) && !def.sensitiveCols().isEmpty()) {
            rows.forEach(r -> def.sensitiveCols().forEach(r::remove));
        }
        rows.forEach(r -> r.remove("password_hash"));
        return new PageResult<>(rows, total == null ? 0 : total, page, size);
    }

    private static boolean notBlank(String s) {
        return s != null && !s.isBlank();
    }

    private static int parseInt(String s, int dflt) {
        try {
            return s == null ? dflt : Integer.parseInt(s);
        } catch (NumberFormatException e) {
            return dflt;
        }
    }
}
