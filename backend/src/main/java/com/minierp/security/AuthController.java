package com.minierp.security;

import com.minierp.common.BizException;
import com.minierp.system.User;
import com.minierp.system.UserRepository;
import jakarta.validation.constraints.NotBlank;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {
    private final UserRepository users;
    private final PasswordEncoder encoder;
    private final JwtService jwt;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    public record LoginRequest(@NotBlank String username, @NotBlank String password) {
    }

    @PostMapping("/login")
    public Map<String, Object> login(@RequestBody @jakarta.validation.Valid LoginRequest req) {
        User u = users.findByUsername(req.username())
                .filter(User::isEnabled)
                .filter(x -> encoder.matches(req.password(), x.getPasswordHash()))
                .orElseThrow(() -> new BizException("用户名或密码错误"));
        return Map.of("token", jwt.issue(u.getUsername(), u.getRole()), "user", profile(u));
    }

    @GetMapping("/me")
    public Map<String, Object> me(Authentication auth) {
        return profile(users.findByUsername(auth.getName()).orElseThrow());
    }

    private Map<String, Object> profile(User u) {
        Map<String, Object> p = new java.util.LinkedHashMap<>();
        p.put("username", u.getUsername());
        p.put("displayName", u.getDisplayName());
        p.put("role", u.getRole());
        p.put("dept", u.getDept() == null ? "" : u.getDept());
        p.put("employeeId", u.getEmployeeId());
        if (u.getEmployeeId() != null) {
            jdbc.queryForList("SELECT emp_no, dept_name, position_name FROM v_employee WHERE id = ?", u.getEmployeeId()).stream().findFirst()
                    .ifPresent(e -> {
                        p.put("empNo", e.get("emp_no"));
                        p.put("dept", e.get("dept_name"));
                        p.put("position", e.get("position_name"));
                    });
            p.put("pendingApprovals", jdbc.queryForObject("SELECT COUNT(*) FROM wf_task WHERE assignee_emp_id = ? AND status = 'PENDING'",
                    Long.class, u.getEmployeeId()));
        }
        return p;
    }
}
