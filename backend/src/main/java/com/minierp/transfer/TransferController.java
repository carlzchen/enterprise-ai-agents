package com.minierp.transfer;

import com.minierp.system.Audited;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/transfers")
@RequiredArgsConstructor
public class TransferController {
    private final TransferService transfers;

    public record DateReq(LocalDate date) {
    }

    private static LocalDate dateOf(DateReq r) {
        return r == null || r.date() == null ? LocalDate.now() : r.date();
    }

    @PostMapping
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE')")
    @Audited("transfer.create")
    public Map<String, Object> create(@RequestBody @Valid TransferService.CreateReq req, Authentication auth) {
        return transfers.detail(transfers.create(req, auth.getName()));
    }

    @GetMapping("/{id}")
    public Map<String, Object> detail(@PathVariable long id) {
        return transfers.detail(id);
    }

    @PostMapping("/{id}/ship")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE')")
    @Audited("transfer.ship")
    public Map<String, Object> ship(@PathVariable long id, @RequestBody(required = false) DateReq req) {
        transfers.ship(id, dateOf(req));
        return transfers.detail(id);
    }

    @PostMapping("/{id}/receive")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE')")
    @Audited("transfer.receive")
    public Map<String, Object> receive(@PathVariable long id, @RequestBody(required = false) DateReq req) {
        transfers.receive(id, dateOf(req));
        return transfers.detail(id);
    }

    @PostMapping("/{id}/cancel")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE')")
    @Audited("transfer.cancel")
    public void cancel(@PathVariable long id) {
        transfers.cancel(id);
    }

    @GetMapping("/suggestions")
    public List<Map<String, Object>> suggestions(@RequestParam(required = false) LocalDate date) {
        return transfers.suggestions(date == null ? LocalDate.now() : date);
    }

    @PostMapping("/from-suggestions")
    @PreAuthorize("hasAnyRole('ADMIN','WAREHOUSE')")
    @Audited("transfer.fromSuggestions")
    public List<Long> fromSuggestions(@RequestBody(required = false) DateReq req, Authentication auth) {
        return transfers.createFromSuggestions(dateOf(req), auth.getName());
    }
}
