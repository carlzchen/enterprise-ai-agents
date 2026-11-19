package com.minierp.common;

public class NotFoundException extends RuntimeException {
    public NotFoundException(String what, Object id) {
        super(what + "不存在：" + id);
    }
}
