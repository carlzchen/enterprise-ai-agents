package com.minierp.aps;

import com.minierp.system.Audited;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.util.Map;

@RestController
@RequestMapping("/api/aps")
@RequiredArgsConstructor
public class ApsController {
    private final ApsService aps;

    @PostMapping("/run")
    @PreAuthorize("hasAnyRole('ADMIN','PRODUCTION')")
    @Audited("aps.run")
    public Map<String, Object> run(@RequestParam(required = false) LocalDate date, Authentication auth) {
        return aps.run(date == null ? LocalDate.now() : date, auth.getName());
    }

    @GetMapping("/latest")
    public Map<String, Object> latest() {
        return aps.latest();
    }
}
