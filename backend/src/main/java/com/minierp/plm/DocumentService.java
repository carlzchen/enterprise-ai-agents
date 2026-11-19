package com.minierp.plm;

import com.minierp.common.BizException;
import com.minierp.common.DocNoService;
import com.minierp.common.NotFoundException;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.util.Set;

/** 技术文档：文件存本地目录（容器中挂载卷），元数据与版本存数据库。同一文档编号上传新文件即升版。 */
@Service
@RequiredArgsConstructor
public class DocumentService {
    private static final Set<String> TYPES = Set.of("DATASHEET", "DRAWING", "SOP", "INSPECTION", "TEST_REPORT");
    private static final long MAX_SIZE = 20L * 1024 * 1024;

    private final PlmDocumentRepository docs;
    private final DocNoService docNo;

    @Value("${minierp.plm.storage}")
    private String storageRoot;

    public record Meta(String docNo, String title, String docType, Long productId, String series) {
    }

    @Transactional
    public PlmDocument store(Meta meta, String originalName, byte[] content, String contentType, String user, LocalDate date) {
        if (!TYPES.contains(meta.docType())) {
            throw new BizException("未知文档类型：" + meta.docType());
        }
        if (content.length == 0 || content.length > MAX_SIZE) {
            throw new BizException("文件为空或超过 20MB");
        }
        String fileName = sanitize(originalName);
        PlmDocument d = new PlmDocument();
        if (meta.docNo() != null && !meta.docNo().isBlank()) {
            PlmDocument current = docs.findByDocNoAndStatus(meta.docNo(), "CURRENT")
                    .orElseThrow(() -> new NotFoundException("文档", meta.docNo()));
            current.setStatus("SUPERSEDED");
            d.setDocNo(current.getDocNo());
            d.setRevision(current.getRevision() + 1);
            d.setTitle(meta.title() != null && !meta.title().isBlank() ? meta.title() : current.getTitle());
            d.setDocType(current.getDocType());
            d.setProductId(current.getProductId());
            d.setSeries(current.getSeries());
        } else {
            if (meta.title() == null || meta.title().isBlank()) {
                throw new BizException("请填写文档标题");
            }
            d.setDocNo(docNo.next("DOC", date));
            d.setRevision(1);
            d.setTitle(meta.title());
            d.setDocType(meta.docType());
            d.setProductId(meta.productId());
            d.setSeries(meta.series());
        }
        String relative = d.getDocNo() + "/v" + d.getRevision() + "/" + fileName;
        Path target = resolve(relative);
        try {
            Files.createDirectories(target.getParent());
            Files.write(target, content);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        d.setStatus("CURRENT");
        d.setFileName(fileName);
        d.setFilePath(relative);
        d.setFileSize((long) content.length);
        d.setContentType(contentType);
        d.setUploadedBy(user);
        d.setUploadDate(date);
        return docs.save(d);
    }

    public PlmDocument get(Long id) {
        return docs.findById(id).orElseThrow(() -> new NotFoundException("文档", id));
    }

    public Resource file(PlmDocument d) {
        Path p = resolve(d.getFilePath());
        if (!Files.exists(p)) {
            throw new NotFoundException("文档文件", d.getFilePath());
        }
        return new FileSystemResource(p);
    }

    /** 解析存储路径并防止目录穿越 */
    private Path resolve(String relative) {
        Path root = Path.of(storageRoot).toAbsolutePath().normalize();
        Path p = root.resolve(relative).normalize();
        if (!p.startsWith(root)) {
            throw new BizException("非法文件路径");
        }
        return p;
    }

    private static String sanitize(String name) {
        String base = name == null ? "file" : Path.of(name.replace('\\', '/')).getFileName().toString();
        base = base.replaceAll("[^\\p{L}\\p{N}._-]", "_");
        return base.isBlank() || base.startsWith(".") ? "file" + base : base;
    }
}
