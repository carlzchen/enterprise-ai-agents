package com.minierp.security;

import io.jsonwebtoken.JwtException;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class JwtServiceTest {
    private final JwtService jwt = new JwtService("test-secret-test-secret-test-secret-test-secret-0123", 1);

    @Test
    void issuedTokenCarriesUserAndRole() {
        var claims = jwt.parse(jwt.issue("fin01", "FINANCE"));
        assertThat(claims.getSubject()).isEqualTo("fin01");
        assertThat(claims.get("role", String.class)).isEqualTo("FINANCE");
    }

    @Test
    void tamperedTokenIsRejected() {
        String token = jwt.issue("sales01", "SALES");
        String[] parts = token.split("\\.");
        String forged = parts[0] + "." + parts[1].replace(parts[1].charAt(5), parts[1].charAt(5) == 'A' ? 'B' : 'A') + "." + parts[2];
        assertThatThrownBy(() -> jwt.parse(forged)).isInstanceOf(JwtException.class);
    }

    @Test
    void tokenFromAnotherSecretIsRejected() {
        String other = new JwtService("another-secret-another-secret-another-secret-0123456", 1).issue("admin", "ADMIN");
        assertThatThrownBy(() -> jwt.parse(other)).isInstanceOf(JwtException.class);
    }
}
