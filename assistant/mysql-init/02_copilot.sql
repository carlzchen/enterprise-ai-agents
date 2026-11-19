-- 经营与人力数据分析 Copilot 的数据库准备：
--   1. copilot_ro 账号：只能 SELECT 白名单里的视图和表（模型生成的 SQL 只用这个账号执行）
--   2. 审计、反馈等表建在 assistant 库，由服务启动时自动创建（见 app/copilot/audit.py）
-- 白名单要和 app/copilot/semantic/views.yaml 保持一致（tests/test_copilot.py 有一致性检查）；v_employee_pii 的授权在 03_employee_pii.sql 里
CREATE USER IF NOT EXISTS 'copilot_ro'@'%' IDENTIFIED BY 'copilot_ro';
GRANT SELECT ON minierp.v_employee        TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_employee_change TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_dept            TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_attendance      TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_leave           TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_payslip         TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_payroll         TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.hr_piece_rate     TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.mes_line          TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_wo_report       TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_work_order      TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_line_day        TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_sales_fact      TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_sales_order     TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_sales_invoice   TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_receipt         TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.v_price_variance  TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.md_product        TO 'copilot_ro'@'%';
GRANT SELECT ON minierp.md_customer       TO 'copilot_ro'@'%';
FLUSH PRIVILEGES;
