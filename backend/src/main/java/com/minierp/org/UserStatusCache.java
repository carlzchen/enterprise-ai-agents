package com.minierp.org;

import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 账号启用状态缓存（60 秒）：JwtAuthFilter 每次请求校验账号仍启用，
 * 员工离职停用账号后，已签发的 token 最多 60 秒内失效，而不用等到 12 小时过期。
 */
@Component
@RequiredArgsConstructor
public class UserStatusCache {
    private static final long TTL_MS = 60_000;
    private final JdbcTemplate jdbc;
    private final Map<String, long[]> cache = new ConcurrentHashMap<>(); // username -> {enabled(0/1), loadedAt}

    public boolean enabled(String username) {
        long now = System.currentTimeMillis();
        long[] v = cache.get(username);
        if (v == null || now - v[1] > TTL_MS) {
            Integer e = jdbc.queryForList("SELECT enabled FROM sys_user WHERE username = ?", Integer.class, username).stream().findFirst().orElse(0);
            v = new long[]{e, now};
            cache.put(username, v);
        }
        return v[0] == 1;
    }

    public void evictAll() {
        cache.clear();
    }
}
