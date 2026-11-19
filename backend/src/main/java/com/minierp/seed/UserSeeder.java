package com.minierp.seed;

import com.minierp.system.User;
import com.minierp.system.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.CommandLineRunner;
import org.springframework.core.annotation.Order;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/** 首次启动创建各角色演示账号（账号清单见 README，仅用于本地演示） */
@Slf4j
@Component
@Order(1)
@RequiredArgsConstructor
public class UserSeeder implements CommandLineRunner {
    static final String DEMO_PASSWORD = "Demo@2026";

    private final UserRepository users;
    private final PasswordEncoder encoder;

    @Override
    public void run(String... args) {
        if (users.count() > 0) {
            return;
        }
        String[][] demo = {
                {"admin", "梁启航", "ADMIN", "信息部"},
                {"gm", "林建国", "MANAGEMENT", "总经办"},
                {"hr01", "何雅琴", "HR", "人力资源部"},
                {"cfo", "陈思敏", "MANAGEMENT", "总经办"},
                {"fin01", "刘婷", "FINANCE", "财务部"},
                {"sales01", "王浩然", "SALES", "销售部"},
                {"buyer01", "周文斌", "PURCHASE", "采购部"},
                {"wh01", "赵国强", "WAREHOUSE", "物流部"},
                {"prod01", "孙宏伟", "PRODUCTION", "生产部"},
                {"qa01", "吴志刚", "QUALITY", "质量部"},
                {"eng01", "林志远", "ENGINEER", "研发中心"},
                {"eng02", "陈家豪", "ENGINEER", "研发中心"},
        };
        String hash = encoder.encode(DEMO_PASSWORD);
        for (String[] d : demo) {
            User u = new User();
            u.setUsername(d[0]);
            u.setDisplayName(d[1]);
            u.setRole(d[2]);
            u.setDept(d[3]);
            u.setPasswordHash(hash);
            users.save(u);
        }
        log.info("Created {} demo users", demo.length);
    }
}
