package com.minierp.common;

import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.ErrorResponse;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.Map;
import java.util.stream.Collectors;

/**
 * 统一错误响应：{message, traceId}。业务异常记 WARN（不带堆栈），系统异常记 ERROR（带堆栈），
 * traceId 与响应头 X-Trace-Id 一致，用户报错时提供它即可在日志中定位。
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    private static Map<String, String> body(String message) {
        String traceId = org.slf4j.MDC.get(TraceIdFilter.MDC_KEY);
        return Map.of("message", message, "traceId", traceId == null ? "-" : traceId);
    }

    @ExceptionHandler(BizException.class)
    public ResponseEntity<Map<String, String>> biz(BizException e) {
        log.warn("业务校验未通过：{}", e.getMessage());
        return ResponseEntity.badRequest().body(body(e.getMessage()));
    }

    @ExceptionHandler(NotFoundException.class)
    public ResponseEntity<Map<String, String>> notFound(NotFoundException e) {
        log.warn("数据不存在：{}", e.getMessage());
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(body(e.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Map<String, String>> invalid(MethodArgumentNotValidException e) {
        String msg = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + " " + f.getDefaultMessage())
                .collect(Collectors.joining("; "));
        log.warn("参数校验失败：{}", msg);
        return ResponseEntity.badRequest().body(body(msg));
    }

    /** 请求体格式错误、参数类型不匹配、缺少必填参数：客户端错误，返回 400 而不是 500 */
    @ExceptionHandler({org.springframework.http.converter.HttpMessageNotReadableException.class,
            org.springframework.web.method.annotation.MethodArgumentTypeMismatchException.class,
            org.springframework.web.bind.MissingServletRequestParameterException.class,
            java.time.format.DateTimeParseException.class, NumberFormatException.class})
    public ResponseEntity<Map<String, String>> badRequest(Exception e) {
        log.warn("请求参数错误：{}", e.getMessage());
        return ResponseEntity.badRequest().body(body("请求参数格式错误：" + (e.getMessage() == null ? "" : e.getMessage().split(":")[0])));
    }

    @ExceptionHandler(AccessDeniedException.class)
    public ResponseEntity<Map<String, String>> denied(AccessDeniedException e) {
        log.warn("越权访问被拒绝：{}", e.getMessage());
        return ResponseEntity.status(HttpStatus.FORBIDDEN).body(body("无权限执行该操作"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<Map<String, String>> other(Exception e) {
        // Spring MVC 自身的 4xx（405 方法不支持、404 路径不存在、400 参数错误等）保留原状态码
        if (e instanceof ErrorResponse er && er.getStatusCode().is4xxClientError()) {
            return ResponseEntity.status(er.getStatusCode()).body(body(er.getBody().getDetail() == null
                    ? er.getStatusCode().toString() : er.getBody().getDetail()));
        }
        log.error("未处理的系统异常", e);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(body("系统错误，请联系管理员并提供追踪号"));
    }
}
