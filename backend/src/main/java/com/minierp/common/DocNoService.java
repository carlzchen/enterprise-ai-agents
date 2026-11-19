package com.minierp.common;

import lombok.RequiredArgsConstructor;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;

/** 生成单据号：前缀 + yyMMdd + 4 位当日流水，例如 SO2609280001 */
@Service
@RequiredArgsConstructor
public class DocNoService {
    private static final DateTimeFormatter FMT = DateTimeFormatter.ofPattern("yyMMdd");
    private final JdbcTemplate jdbc;

    @Transactional
    public String next(String prefix, LocalDate date) {
        String key = prefix + date.format(FMT);
        jdbc.update("INSERT INTO sys_doc_seq (seq_key, current_val) VALUES (?, LAST_INSERT_ID(1)) "
                + "ON DUPLICATE KEY UPDATE current_val = LAST_INSERT_ID(current_val + 1)", key);
        Long val = jdbc.queryForObject("SELECT LAST_INSERT_ID()", Long.class);
        return key + String.format("%04d", val);
    }
}
