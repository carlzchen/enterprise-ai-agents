package com.minierp.quality;

import com.minierp.system.Audited;
import jakarta.validation.constraints.NotNull;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;

@RestController
@RequestMapping("/api/quality")
@RequiredArgsConstructor
public class QualityController {
    private final QualityService quality;

    public record InspectReq(@NotNull Long grLineId, int sampleQty, int defectQty, String defectDesc, String inspector) {
    }

    public record UpdateComplaintReq(String status, String rootCause, String correctiveAction) {
    }

    @PostMapping("/iqc")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY')")
    @Audited("quality.inspect")
    public IqcInspection inspect(@RequestBody @jakarta.validation.Valid InspectReq req) {
        return quality.inspect(req.grLineId(), LocalDate.now(), req.sampleQty(), req.defectQty(), req.defectDesc(), req.inspector());
    }

    @PostMapping("/complaints")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY','SALES')")
    @Audited("quality.createComplaint")
    public Complaint create(@RequestBody Complaint c) {
        if (c.getReceivedDate() == null) {
            c.setReceivedDate(LocalDate.now());
        }
        return quality.createComplaint(c);
    }

    @PutMapping("/complaints/{id}")
    @PreAuthorize("hasAnyRole('ADMIN','QUALITY')")
    @Audited("quality.updateComplaint")
    public Complaint update(@PathVariable Long id, @RequestBody UpdateComplaintReq req) {
        return quality.updateComplaint(id, req.status(), req.rootCause(), req.correctiveAction(), LocalDate.now());
    }
}
