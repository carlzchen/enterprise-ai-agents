package com.minierp.trace;

import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/trace")
@RequiredArgsConstructor
public class TraceController {
    private final TraceService trace;

    @GetMapping("/delivery/{deliveryNo}")
    public Map<String, Object> delivery(@PathVariable String deliveryNo) {
        return trace.backwardFromDelivery(deliveryNo);
    }

    @GetMapping("/complaint/{id}")
    public Map<String, Object> complaint(@PathVariable Long id) {
        return trace.backwardFromComplaint(id);
    }

    @GetMapping("/lot/{lotNo}")
    public Map<String, Object> lot(@PathVariable String lotNo) {
        return trace.forwardFromLot(lotNo);
    }
}
