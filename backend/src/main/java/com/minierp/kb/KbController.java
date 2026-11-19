package com.minierp.kb;

import com.minierp.org.OrgService;
import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Tag(name = "kb", description = "知识库：空间、页面树、版本对比、全文检索、RAG 同步导出")
@RestController
@RequestMapping("/api/kb")
@RequiredArgsConstructor
public class KbController {
    private final KbService kb;
    private final OrgService orgs;

    public record PageReq(String spaceKey, Long parentId, String title, String body, String labels, Boolean publish, String changeNote) {
    }

    @GetMapping("/spaces")
    public List<Map<String, Object>> spaces() {
        return kb.spaces();
    }

    @GetMapping("/spaces/{key}/tree")
    public List<Map<String, Object>> tree(@PathVariable String key) {
        return kb.tree(key);
    }

    @GetMapping("/pages/{id}")
    public Map<String, Object> page(@PathVariable long id) {
        return kb.page(id);
    }

    @PostMapping("/pages")
    @Audited("kb.createPage")
    public Map<String, Object> create(@RequestBody PageReq r, Authentication a) {
        return kb.page(kb.save(null, r.spaceKey(), r.parentId(), r.title(), r.body(), r.labels(), !Boolean.FALSE.equals(r.publish()),
                orgs.employeeOfUser(a.getName()), r.changeNote(), "MANUAL", null, LocalDateTime.now()));
    }

    @PutMapping("/pages/{id}")
    @Audited("kb.savePage")
    public Map<String, Object> save(@PathVariable long id, @RequestBody PageReq r, Authentication a) {
        return kb.page(kb.save(id, null, null, r.title(), r.body(), r.labels(), !Boolean.FALSE.equals(r.publish()),
                orgs.employeeOfUser(a.getName()), r.changeNote(), null, null, LocalDateTime.now()));
    }

    @DeleteMapping("/pages/{id}")
    @Audited("kb.archivePage")
    public void archive(@PathVariable long id) {
        kb.archive(id);
    }

    @Operation(summary = "版本对比（按行）")
    @GetMapping("/pages/{id}/diff")
    public Map<String, Object> diff(@PathVariable long id, @RequestParam int from, @RequestParam int to) {
        return kb.diff(id, from, to);
    }

    @Operation(summary = "全文检索（中文 ngram）")
    @GetMapping("/search")
    public List<Map<String, Object>> search(@RequestParam String q, @RequestParam(required = false) String space, @RequestParam(defaultValue = "20") int limit) {
        return kb.search(q, space, limit);
    }

    @Operation(summary = "已发布页面导出（技术助手 RAG 增量同步用）")
    @GetMapping("/export")
    public List<Map<String, Object>> export(@RequestParam(required = false) LocalDate since) {
        return kb.exportPublished(since);
    }
}
