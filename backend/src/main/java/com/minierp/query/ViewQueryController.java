package com.minierp.query;

import com.minierp.common.PageResult;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/views")
@RequiredArgsConstructor
public class ViewQueryController {
    private final ViewQueryService views;

    @GetMapping
    public java.util.List<Map<String, Object>> catalog(Authentication auth) {
        return views.catalog(CurrentRole.of(auth));
    }

    @GetMapping("/{key}")
    public PageResult<Map<String, Object>> page(@PathVariable String key, @RequestParam Map<String, String> params,
                                                Authentication auth) {
        return views.page(key, CurrentRole.of(auth), params);
    }
}
