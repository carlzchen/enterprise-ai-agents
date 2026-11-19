-- 在 MiniERP 的 MySQL 上为技术助手准备：
--   1. assistant 库：父文档（Parent Document Retriever）存储
--   2. assistant 账号：assistant 库读写 + minierp 库只读（查询产品参数 / BOM 版本 / ECN）
CREATE DATABASE IF NOT EXISTS assistant DEFAULT CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'assistant'@'%' IDENTIFIED BY 'assistant';
GRANT ALL PRIVILEGES ON assistant.* TO 'assistant'@'%';
GRANT SELECT ON minierp.* TO 'assistant'@'%';
FLUSH PRIVILEGES;
