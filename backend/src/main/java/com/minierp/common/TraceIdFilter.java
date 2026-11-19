package com.minierp.common;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.slf4j.MDC;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.UUID;
import java.util.regex.Pattern;

/**
 * 请求追踪号 + 访问日志。
 * 追踪号优先取请求头 X-Trace-Id（前端 / 网关 / 其他系统传入，便于跨系统串联），否则生成；
 * 写入 MDC 使该请求的每一行日志都带 traceId，并通过响应头返回，报错时前端展示给用户，运维按它检索日志。
 */
@Slf4j
@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public class TraceIdFilter extends OncePerRequestFilter {
    public static final String HEADER = "X-Trace-Id";
    public static final String MDC_KEY = "traceId";
    /** JwtAuthFilter 认证成功后写入的用户名（请求结束时 SecurityContext 已被清理，不能再从那里取） */
    public static final String USER_ATTR = "minierp.user";
    private static final Pattern SAFE = Pattern.compile("[A-Za-z0-9-]{8,64}");
    private static final long SLOW_MS = 1000;

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain) throws ServletException, IOException {
        String incoming = req.getHeader(HEADER);
        String traceId = incoming != null && SAFE.matcher(incoming).matches() ? incoming : UUID.randomUUID().toString().replace("-", "").substring(0, 16);
        MDC.put(MDC_KEY, traceId);
        res.setHeader(HEADER, traceId);
        long t0 = System.currentTimeMillis();
        try {
            chain.doFilter(req, res);
        } finally {
            long ms = System.currentTimeMillis() - t0;
            if (req.getRequestURI().startsWith("/api/")) {
                Object u = req.getAttribute(USER_ATTR);
                String user = u == null ? "-" : u.toString();
                String line = String.format("%s %s%s -> %d %dms user=%s", req.getMethod(), req.getRequestURI(),
                        req.getQueryString() == null ? "" : "?" + req.getQueryString(), res.getStatus(), ms, user);
                if (res.getStatus() >= 500) {
                    log.error("[access] {}", line);
                } else if (ms >= SLOW_MS) {
                    log.warn("[access][slow] {}", line);
                } else {
                    log.info("[access] {}", line);
                }
            }
            MDC.remove(MDC_KEY);
        }
    }
}
