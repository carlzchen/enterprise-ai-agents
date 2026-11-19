package com.minierp.common;

/** 业务规则校验失败（返回 400，消息直接展示给用户） */
public class BizException extends RuntimeException {
    public BizException(String message) {
        super(message);
    }
}
