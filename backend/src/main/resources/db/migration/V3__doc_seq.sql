-- 单据号序列：按 前缀+日期 计数，使用 LAST_INSERT_ID 技巧保证并发安全
CREATE TABLE sys_doc_seq (
  seq_key     VARCHAR(40) PRIMARY KEY COMMENT '如 SO260928',
  current_val BIGINT      NOT NULL
) COMMENT '单据号序列';
