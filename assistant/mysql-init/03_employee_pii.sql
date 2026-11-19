-- 员工敏感信息：身份证号、工资卡号、手机号，用来演示 Copilot 的敏感字段打码。
-- 都是按员工 ID 推算出来的模拟数据，不对应任何真实的人。
-- 单独建表，不改 ERP 原有的 hr_employee；脚本可以重复执行（重新生成业务数据或有新员工入职后再跑一遍即可）。
USE minierp;

CREATE TABLE IF NOT EXISTS hr_employee_pii (
  employee_id  BIGINT      NOT NULL PRIMARY KEY,
  id_card      VARCHAR(18) NOT NULL COMMENT '身份证号（模拟）',
  bank_account VARCHAR(19) NOT NULL COMMENT '工资卡号（模拟）',
  phone        VARCHAR(11) NOT NULL COMMENT '手机号（模拟）'
) COMMENT '员工敏感信息（模拟数据）';

INSERT INTO hr_employee_pii (employee_id, id_card, bank_account, phone)
SELECT e.id,
       CONCAT(ELT(1 + e.id % 6, '430102', '440305', '421023', '360102', '450103', '511002'),
              DATE_FORMAT(COALESCE(e.birth_date, '1990-01-01'), '%Y%m%d'), LPAD(e.id % 1000, 3, '0'),
              ELT(1 + e.id % 11, '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'X')),
       CONCAT('622848', LPAD((e.id * 7919 + 1234567) % 10000000000000, 13, '0')),
       CONCAT(ELT(1 + e.id % 5, '138', '139', '150', '186', '177'), LPAD((e.id * 104729) % 100000000, 8, '0'))
FROM hr_employee e
ON DUPLICATE KEY UPDATE id_card = VALUES(id_card), bank_account = VALUES(bank_account), phone = VALUES(phone);

CREATE OR REPLACE VIEW v_employee_pii AS
SELECT e.id AS employee_id, e.emp_no, e.name, e.dept_id, d.name AS dept_name, e.status, p.id_card, p.bank_account, p.phone
FROM hr_employee e
JOIN org_dept d ON d.id = e.dept_id
LEFT JOIN hr_employee_pii p ON p.employee_id = e.id;

GRANT SELECT ON minierp.v_employee_pii TO 'copilot_ro'@'%';
FLUSH PRIVILEGES;
