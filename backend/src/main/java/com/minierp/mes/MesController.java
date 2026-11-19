package com.minierp.mes;

import com.minierp.system.Audited;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

@Tag(name = "mes", description = "MES：测试台数据接入、序列号追溯、过程检验、安灯、班次 OEE")
@RestController
@RequestMapping("/api/mes")
@RequiredArgsConstructor
public class MesController {
    private final MesService mes;
    private final JdbcTemplate jdbc;

    public record IngestReq(String woNo, long productId, String lineCode, LocalDate date, List<MesService.TestReq> tests) {
    }

    public record IpqcReq(String woNo, String lineCode, String type, String result, String remark) {
    }

    public record AndonReq(String lineCode, String type, String description, String refNo) {
    }

    @Operation(summary = "测试台数据上传（整机 SN + 测试值）")
    @PostMapping("/tests")
    @PreAuthorize("hasAnyRole('ADMIN','PRODUCTION','QUALITY')")
    @Audited("mes.ingestTests")
    public Map<String, Object> ingest(@RequestBody IngestReq r) {
        return Map.of("count", mes.ingestTests(r.woNo(), r.productId(), r.lineCode(), r.date() == null ? LocalDate.now() : r.date(), r.tests()));
    }

    @Operation(summary = "整机序列号追溯：测试值、工单（批次）、出货与客户、过程检验")
    @GetMapping("/serials/{sn}")
    public Map<String, Object> serial(@PathVariable String sn) {
        return mes.serial(sn);
    }

    @GetMapping("/serials/by-id/{id}")
    public Map<String, Object> serialById(@PathVariable long id) {
        return mes.serial(jdbc.queryForObject("SELECT sn FROM mes_serial WHERE id = ?", String.class, id));
    }

    @PostMapping("/ipqc")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY')")
    @Audited("mes.ipqc")
    public void ipqc(@RequestBody IpqcReq r, Authentication auth) {
        String name = jdbc.queryForObject("SELECT display_name FROM sys_user WHERE username = ?", String.class, auth.getName());
        mes.recordIpqc(r.woNo(), r.lineCode(), r.type(), LocalDateTime.now(), name, r.result(), r.remark());
    }

    @PostMapping("/andons")
    @Audited("mes.raiseAndon")
    public Map<String, Object> andon(@RequestBody AndonReq r, Authentication auth) {
        return Map.of("id", mes.raiseAndon(r.lineCode(), r.type(), r.description(), r.refNo(), LocalDateTime.now(), auth.getName()));
    }

    @PostMapping("/andons/{id}/resolve")
    @Audited("mes.resolveAndon")
    public void resolve(@PathVariable long id) {
        mes.resolveAndonById(id, LocalDateTime.now());
    }

    @PostMapping("/shifts/{date}/close")
    @PreAuthorize("hasAnyRole('ADMIN','PRODUCTION')")
    @Audited("mes.closeShift")
    public void close(@PathVariable LocalDate date) {
        mes.closeShift(date);
    }

    @Operation(summary = "车间看板：各产线产出、OEE、劳动效率、安灯、IPQC")
    @GetMapping("/board")
    public Map<String, Object> board(@RequestParam(required = false) LocalDate date) {
        LocalDate d = date != null ? date : jdbc.queryForObject("SELECT MAX(work_date) FROM mes_line_day", LocalDate.class);
        return mes.board(d == null ? LocalDate.now() : d);
    }
}
