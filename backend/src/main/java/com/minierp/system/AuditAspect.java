package com.minierp.system;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;

@Aspect
@Component
@RequiredArgsConstructor
public class AuditAspect {
    private final AuditLogRepository repo;
    private final ObjectMapper mapper;

    @Around("@annotation(audited)")
    public Object around(ProceedingJoinPoint pjp, Audited audited) throws Throwable {
        AuditLog log = new AuditLog();
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        log.setUsername(auth == null ? null : auth.getName());
        log.setAction(audited.value());
        log.setDetail(truncate(safeJson(pjp.getArgs()), 1000));
        try {
            Object result = pjp.proceed();
            log.setSuccess(true);
            return result;
        } catch (Throwable t) {
            log.setSuccess(false);
            log.setErrorMsg(truncate(t.getMessage(), 500));
            throw t;
        } finally {
            repo.save(log);
        }
    }

    private String safeJson(Object[] args) {
        try {
            return mapper.writeValueAsString(args);
        } catch (Exception e) {
            return "<unserializable>";
        }
    }

    private static String truncate(String s, int max) {
        return s == null || s.length() <= max ? s : s.substring(0, max);
    }
}
