package com.minierp.plm;

import com.minierp.query.CurrentRole;
import com.minierp.system.Audited;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.core.io.Resource;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/plm")
@RequiredArgsConstructor
public class PlmController {
    private final EcnService ecns;
    private final DocumentService documents;
    private final ProjectService projects;

    public record ReviewReq(boolean approve, String comment, String reviewRole) {
    }

    public record DateReq(LocalDate date) {
    }

    public record GateReq(String decision, String comment) {
    }

    // ---------- ECN ----------

    @PostMapping("/ecns")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER')")
    @Audited("plm.createEcn")
    public Ecn create(@RequestBody @Valid EcnService.CreateReq req, Authentication auth) {
        return ecns.create(req, auth.getName(), LocalDate.now());
    }

    @GetMapping("/ecns/{id}")
    public Map<String, Object> detail(@PathVariable Long id) {
        return ecns.detail(id);
    }

    @GetMapping("/ecns/{id}/impact")
    public Map<String, Object> impact(@PathVariable Long id) {
        return ecns.impact(id, LocalDate.now());
    }

    @PostMapping("/ecns/{id}/submit")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER')")
    @Audited("plm.submitEcn")
    public Ecn submit(@PathVariable Long id) {
        return ecns.submit(id);
    }

    /** 会签：以当前用户角色会签；管理员可代任意角色会签（需指定 reviewRole） */
    @PostMapping("/ecns/{id}/review")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER','PRODUCTION','QUALITY','PURCHASE')")
    @Audited("plm.reviewEcn")
    public Ecn review(@PathVariable Long id, @RequestBody ReviewReq req, Authentication auth) {
        String role = CurrentRole.of(auth);
        if ("ADMIN".equals(role)) {
            role = req.reviewRole();
        }
        return ecns.review(id, role, auth.getName(), req.approve(), req.comment(), LocalDate.now());
    }

    @PostMapping("/ecns/{id}/release")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER')")
    @Audited("plm.releaseEcn")
    public Ecn release(@PathVariable Long id) {
        return ecns.release(id, LocalDate.now());
    }

    @GetMapping("/bom-revisions/{id}")
    public Map<String, Object> revision(@PathVariable Long id) {
        return ecns.revisionDetail(id);
    }

    // ---------- 文档 ----------

    @PostMapping(value = "/documents", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER','QUALITY','PRODUCTION')")
    @Audited("plm.uploadDocument")
    public PlmDocument upload(@RequestParam("file") MultipartFile file,
                              @RequestParam(required = false) String docNo,
                              @RequestParam(required = false) String title,
                              @RequestParam(defaultValue = "DATASHEET") String docType,
                              @RequestParam(required = false) Long productId,
                              @RequestParam(required = false) String series,
                              Authentication auth) throws IOException {
        return documents.store(new DocumentService.Meta(docNo, title, docType, productId, series),
                file.getOriginalFilename(), file.getBytes(), file.getContentType(), auth.getName(), LocalDate.now());
    }

    @GetMapping("/documents/{id}/download")
    public ResponseEntity<Resource> download(@PathVariable Long id) {
        PlmDocument d = documents.get(id);
        return ResponseEntity.ok()
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        ContentDisposition.attachment().filename(d.getFileName(), StandardCharsets.UTF_8).build().toString())
                .contentType(d.getContentType() == null ? MediaType.APPLICATION_OCTET_STREAM : MediaType.parseMediaType(d.getContentType()))
                .body(documents.file(d));
    }

    // ---------- NPI 项目 ----------

    @PostMapping("/projects")
    @PreAuthorize("hasAnyRole('ADMIN','ENGINEER')")
    @Audited("plm.createProject")
    public PlmProject createProject(@RequestBody PlmProject p) {
        return projects.create(p, LocalDate.now());
    }

    @GetMapping("/projects/{id}/gates")
    public List<ProjectGate> gates(@PathVariable Long id) {
        return projects.gates(id);
    }

    @PostMapping("/projects/{id}/gate")
    @PreAuthorize("hasAnyRole('ADMIN','MANAGEMENT')")
    @Audited("plm.projectGate")
    public PlmProject gate(@PathVariable Long id, @RequestBody GateReq req, Authentication auth) {
        return projects.gate(id, req.decision(), req.comment(), auth.getName(), LocalDate.now());
    }
}
