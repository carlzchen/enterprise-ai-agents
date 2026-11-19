package com.minierp.query;

import org.springframework.security.core.Authentication;

public final class CurrentRole {
    private CurrentRole() {
    }

    public static String of(Authentication auth) {
        return auth.getAuthorities().stream().findFirst()
                .map(a -> a.getAuthority().replaceFirst("^ROLE_", "")).orElse("");
    }
}
