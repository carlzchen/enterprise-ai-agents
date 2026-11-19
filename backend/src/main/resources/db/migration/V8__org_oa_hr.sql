-- 第一批：组织与统一身份 / OA 流程引擎 / HR（考勤、计件、薪资）

INSERT INTO gl_account (code, name, category, direction) VALUES
 ('2211', '应付职工薪酬', 'LIABILITY', 'CREDIT'),
 ('222103', '应交税费-应交个人所得税', 'LIABILITY', 'CREDIT'),
 ('224101', '其他应付款-社保公积金', 'LIABILITY', 'CREDIT');

-- ========== 组织与统一身份 ==========
CREATE TABLE org_dept (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  code           VARCHAR(20)  NOT NULL UNIQUE,
  name           VARCHAR(50)  NOT NULL,
  parent_id      BIGINT       COMMENT '上级部门',
  manager_emp_id BIGINT       COMMENT '部门负责人',
  cost_type      VARCHAR(12)  NOT NULL COMMENT '人工成本归集：PRODUCTION制造费用/SALES销售费用/ADMIN管理费用/RND研发费用',
  sort_no        INT          NOT NULL DEFAULT 0,
  status         VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE'
) COMMENT '部门';

CREATE TABLE org_position (
  id      BIGINT AUTO_INCREMENT PRIMARY KEY,
  code    VARCHAR(20) NOT NULL UNIQUE,
  name    VARCHAR(50) NOT NULL,
  grade   VARCHAR(10) NOT NULL COMMENT '职级：O1-O3 操作 / P1-P6 专业 / M1-M4 管理',
  is_manager TINYINT(1) NOT NULL DEFAULT 0
) COMMENT '岗位';

CREATE TABLE hr_employee (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  emp_no          VARCHAR(20)   NOT NULL UNIQUE COMMENT '工号',
  name            VARCHAR(50)   NOT NULL,
  gender          CHAR(1)       NOT NULL COMMENT 'M/F',
  birth_date      DATE,
  dept_id         BIGINT        NOT NULL,
  position_id     BIGINT        NOT NULL,
  manager_id      BIGINT        COMMENT '直属上级（为空时取部门负责人）',
  line_code       VARCHAR(20)   COMMENT '一线员工所属产线',
  team            VARCHAR(10)   COMMENT '班组 A/B/C',
  employment_type VARCHAR(12)   NOT NULL COMMENT 'FULL_TIME正式/DISPATCH劳务派遣/INTERN实习',
  wage_type       VARCHAR(10)   NOT NULL COMMENT 'MONTHLY月薪/PIECE计件',
  base_salary     DECIMAL(18,2) NOT NULL COMMENT '月薪员工为月基本工资；计件员工为保底工资',
  social_base     DECIMAL(18,2) NOT NULL COMMENT '社保公积金缴费基数',
  hire_date       DATE          NOT NULL,
  leave_date      DATE,
  leave_reason    VARCHAR(100),
  status          VARCHAR(12)   NOT NULL COMMENT 'PROBATION试用/ACTIVE在职/LEFT离职',
  user_id         BIGINT        COMMENT '系统账号（统一身份）',
  INDEX idx_emp_dept (dept_id),
  INDEX idx_emp_line (line_code, team),
  INDEX idx_emp_status (status)
) COMMENT '员工档案（统一身份：系统账号挂在员工上，离职即停用账号）';

CREATE TABLE hr_employee_change (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  employee_id  BIGINT       NOT NULL,
  change_type  VARCHAR(12)  NOT NULL COMMENT 'HIRE入职/REGULAR转正/TRANSFER调岗/SALARY调薪/LEAVE离职',
  change_date  DATE         NOT NULL,
  from_dept_id BIGINT,
  to_dept_id   BIGINT,
  from_salary  DECIMAL(18,2),
  to_salary    DECIMAL(18,2),
  remark       VARCHAR(200),
  wf_instance_id BIGINT,
  INDEX idx_chg_emp (employee_id),
  INDEX idx_chg_date (change_date)
) COMMENT '员工异动';

ALTER TABLE sys_user ADD COLUMN employee_id BIGINT COMMENT '对应员工（统一身份）';

-- ========== OA 流程引擎 ==========
CREATE TABLE wf_definition (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  code       VARCHAR(30)  NOT NULL COMMENT 'LEAVE请假/EXPENSE报销/RESIGN离职/TRAVEL出差/SEAL用印/PAYROLL工资发放/GENERAL通用',
  name       VARCHAR(50)  NOT NULL,
  version    INT          NOT NULL,
  definition TEXT         NOT NULL COMMENT '节点 JSON：审批人规则、会签/或签、进入条件',
  form_schema TEXT        COMMENT '表单字段 JSON（前端渲染）',
  enabled    TINYINT(1)   NOT NULL DEFAULT 1,
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_wf_def (code, version)
) COMMENT '流程定义（版本化，运行中的实例按发起时版本执行）';

CREATE TABLE wf_instance (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  instance_no      VARCHAR(30)   NOT NULL UNIQUE,
  def_code         VARCHAR(30)   NOT NULL,
  def_version      INT           NOT NULL,
  title            VARCHAR(200)  NOT NULL,
  biz_type         VARCHAR(30)   COMMENT '关联业务单据类型',
  biz_id           BIGINT        COMMENT '关联业务单据 id',
  form_data        TEXT          COMMENT '表单 JSON',
  amount           DECIMAL(18,2) COMMENT '金额类条件字段',
  days             DECIMAL(8,2)  COMMENT '天数类条件字段',
  route            TEXT          NOT NULL COMMENT '发起时解析出的审批路线 JSON',
  current_node     INT           NOT NULL DEFAULT 0,
  applicant_emp_id BIGINT        NOT NULL,
  status           VARCHAR(12)   NOT NULL COMMENT 'RUNNING审批中/APPROVED通过/REJECTED驳回/WITHDRAWN撤回',
  started_at       DATETIME      NOT NULL,
  finished_at      DATETIME,
  INDEX idx_wfi_applicant (applicant_emp_id),
  INDEX idx_wfi_status (status),
  INDEX idx_wfi_biz (biz_type, biz_id),
  INDEX idx_wfi_started (started_at)
) COMMENT '流程实例';

CREATE TABLE wf_task (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  instance_id     BIGINT       NOT NULL,
  node_index      INT          NOT NULL,
  node_name       VARCHAR(50)  NOT NULL,
  sign_mode       VARCHAR(4)   NOT NULL COMMENT 'OR或签/AND会签',
  assignee_emp_id BIGINT       NOT NULL,
  status          VARCHAR(12)  NOT NULL COMMENT 'PENDING待办/APPROVED同意/REJECTED驳回/TRANSFERRED已转交/CANCELLED已取消/SKIPPED自动跳过',
  comment         VARCHAR(500),
  added_by_emp_id BIGINT       COMMENT '加签人（加签产生的任务）',
  created_at      DATETIME     NOT NULL,
  acted_at        DATETIME,
  INDEX idx_wft_assignee (assignee_emp_id, status),
  INDEX idx_wft_instance (instance_id)
) COMMENT '审批任务';

CREATE TABLE oa_notice (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  title        VARCHAR(200) NOT NULL,
  content      TEXT         NOT NULL,
  category     VARCHAR(20)  NOT NULL COMMENT '公告/制度/通知',
  dept_id      BIGINT       COMMENT '为空表示全员',
  publisher    VARCHAR(50)  NOT NULL,
  publish_date DATE         NOT NULL,
  pinned       TINYINT(1)   NOT NULL DEFAULT 0
) COMMENT '公告';

ALTER TABLE fin_expense ADD COLUMN employee_id BIGINT COMMENT '报销人', ADD COLUMN wf_instance_id BIGINT COMMENT '审批流程';

-- ========== HR：考勤、请假、计件、薪资 ==========
CREATE TABLE hr_attendance (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  employee_id    BIGINT       NOT NULL,
  work_date      DATE         NOT NULL,
  status         VARCHAR(10)  NOT NULL COMMENT 'NORMAL正常/LATE迟到/ABSENT旷工/LEAVE请假',
  clock_in       TIME,
  clock_out      TIME,
  work_hours     DECIMAL(5,2) NOT NULL DEFAULT 0,
  overtime_hours DECIMAL(5,2) NOT NULL DEFAULT 0,
  leave_type     VARCHAR(10)  COMMENT 'ANNUAL年假/SICK病假/PERSONAL事假',
  UNIQUE KEY uk_att (employee_id, work_date),
  INDEX idx_att_date (work_date)
) COMMENT '考勤日记录';

CREATE TABLE hr_leave (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  employee_id    BIGINT       NOT NULL,
  leave_type     VARCHAR(10)  NOT NULL,
  start_date     DATE         NOT NULL,
  end_date       DATE         NOT NULL,
  days           DECIMAL(5,1) NOT NULL,
  reason         VARCHAR(200),
  status         VARCHAR(12)  NOT NULL COMMENT 'PENDING审批中/APPROVED/REJECTED/WITHDRAWN',
  wf_instance_id BIGINT,
  INDEX idx_leave_emp (employee_id),
  INDEX idx_leave_date (start_date)
) COMMENT '请假单（审批通过后写入考勤）';

CREATE TABLE hr_piece_rate (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  line_code      VARCHAR(20)   NOT NULL,
  rate           DECIMAL(10,4) NOT NULL COMMENT '每台良品计件单价（元）',
  effective_from DATE          NOT NULL,
  remark         VARCHAR(100),
  UNIQUE KEY uk_piece (line_code, effective_from)
) COMMENT '计件单价（按产线，只计良品，报废不计）';

CREATE TABLE hr_payroll (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  period          CHAR(7)       NOT NULL UNIQUE COMMENT 'YYYY-MM',
  status          VARCHAR(12)   NOT NULL COMMENT 'DRAFT草稿/PENDING审批中/APPROVED已审批入账/PAID已发放',
  headcount       INT           NOT NULL,
  gross_total     DECIMAL(18,2) NOT NULL,
  net_total       DECIMAL(18,2) NOT NULL,
  tax_total       DECIMAL(18,2) NOT NULL,
  social_employee DECIMAL(18,2) NOT NULL,
  social_employer DECIMAL(18,2) NOT NULL,
  employer_cost   DECIMAL(18,2) NOT NULL COMMENT '企业人工成本 = 应发 + 单位社保公积金',
  wf_instance_id  BIGINT,
  calc_date       DATE          NOT NULL,
  paid_date       DATE
) COMMENT '月度工资表';

CREATE TABLE hr_payslip (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  payroll_id        BIGINT        NOT NULL,
  employee_id       BIGINT        NOT NULL,
  dept_id           BIGINT        NOT NULL,
  cost_type         VARCHAR(12)   NOT NULL,
  wage_type         VARCHAR(10)   NOT NULL,
  scheduled_days    INT           NOT NULL COMMENT '应出勤天数（在职期间工作日）',
  attend_days       DECIMAL(5,1)  NOT NULL,
  leave_days        DECIMAL(5,1)  NOT NULL,
  absent_days       DECIMAL(5,1)  NOT NULL,
  overtime_hours    DECIMAL(6,2)  NOT NULL,
  base_pay          DECIMAL(18,2) NOT NULL,
  piece_qty         DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '分摊到个人的良品数',
  piece_pay         DECIMAL(18,2) NOT NULL DEFAULT 0,
  guarantee_topup   DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '计件不足保底工资的补差',
  overtime_pay      DECIMAL(18,2) NOT NULL,
  deduction         DECIMAL(18,2) NOT NULL COMMENT '缺勤扣款',
  gross_pay         DECIMAL(18,2) NOT NULL,
  social_employee   DECIMAL(18,2) NOT NULL COMMENT '个人社保 + 公积金',
  income_tax        DECIMAL(18,2) NOT NULL COMMENT '个税（累计预扣法）',
  net_pay           DECIMAL(18,2) NOT NULL,
  social_employer   DECIMAL(18,2) NOT NULL COMMENT '单位社保 + 公积金',
  ytd_taxable       DECIMAL(18,2) NOT NULL COMMENT '本年累计应纳税所得额（含本月）',
  ytd_tax           DECIMAL(18,2) NOT NULL COMMENT '本年累计已预扣个税（含本月）',
  UNIQUE KEY uk_slip (payroll_id, employee_id),
  INDEX idx_slip_emp (employee_id)
) COMMENT '工资条';

-- ========== 视图 ==========
CREATE VIEW v_dept AS
SELECT d.*, p.name AS parent_name, e.name AS manager_name,
       (SELECT COUNT(*) FROM hr_employee x WHERE x.dept_id = d.id AND x.status <> 'LEFT') AS headcount
FROM org_dept d LEFT JOIN org_dept p ON p.id = d.parent_id LEFT JOIN hr_employee e ON e.id = d.manager_emp_id;

CREATE VIEW v_employee AS
SELECT e.id, e.emp_no, e.name, e.gender, e.dept_id, d.name AS dept_name, e.position_id, p.name AS position_name, p.grade,
       e.manager_id, COALESCE(m.name, dm.name) AS manager_name, e.line_code, e.team, e.employment_type, e.wage_type,
       e.base_salary, e.social_base, e.hire_date, e.leave_date, e.leave_reason, e.status, e.user_id, u.username,
       TIMESTAMPDIFF(MONTH, e.hire_date, COALESCE(e.leave_date, CURDATE())) AS tenure_months
FROM hr_employee e JOIN org_dept d ON d.id = e.dept_id JOIN org_position p ON p.id = e.position_id
LEFT JOIN hr_employee m ON m.id = e.manager_id LEFT JOIN hr_employee dm ON dm.id = d.manager_emp_id
LEFT JOIN sys_user u ON u.id = e.user_id;

CREATE VIEW v_employee_change AS
SELECT c.*, e.emp_no, e.name, fd.name AS from_dept, td.name AS to_dept
FROM hr_employee_change c JOIN hr_employee e ON e.id = c.employee_id
LEFT JOIN org_dept fd ON fd.id = c.from_dept_id LEFT JOIN org_dept td ON td.id = c.to_dept_id;

CREATE VIEW v_wf_instance AS
SELECT i.id, i.instance_no, i.def_code, d.name AS def_name, i.title, i.biz_type, i.biz_id, i.amount, i.days,
       i.status, i.started_at, i.finished_at, DATE(i.started_at) AS start_date, i.applicant_emp_id, e.name AS applicant_name,
       dp.name AS applicant_dept, i.current_node,
       TIMESTAMPDIFF(HOUR, i.started_at, i.finished_at) AS duration_hours,
       (SELECT GROUP_CONCAT(DISTINCT pe.name) FROM wf_task t JOIN hr_employee pe ON pe.id = t.assignee_emp_id
         WHERE t.instance_id = i.id AND t.status = 'PENDING') AS pending_with
FROM wf_instance i JOIN hr_employee e ON e.id = i.applicant_emp_id JOIN org_dept dp ON dp.id = e.dept_id
JOIN wf_definition d ON d.code = i.def_code AND d.version = i.def_version;

CREATE VIEW v_wf_task AS
SELECT t.*, i.instance_no, i.def_code, i.title, i.status AS instance_status, i.applicant_emp_id, ap.name AS applicant_name,
       a.name AS assignee_name, DATE(t.created_at) AS created_date
FROM wf_task t JOIN wf_instance i ON i.id = t.instance_id JOIN hr_employee ap ON ap.id = i.applicant_emp_id
JOIN hr_employee a ON a.id = t.assignee_emp_id;

CREATE VIEW v_attendance AS
SELECT a.*, e.emp_no, e.name, e.dept_id, d.name AS dept_name, e.line_code, e.team
FROM hr_attendance a JOIN hr_employee e ON e.id = a.employee_id JOIN org_dept d ON d.id = e.dept_id;

CREATE VIEW v_leave AS
SELECT l.*, e.emp_no, e.name, d.name AS dept_name
FROM hr_leave l JOIN hr_employee e ON e.id = l.employee_id JOIN org_dept d ON d.id = e.dept_id;

CREATE VIEW v_payroll AS SELECT p.*, (SELECT COUNT(*) FROM hr_payslip s WHERE s.payroll_id = p.id) AS slip_count FROM hr_payroll p;

CREATE VIEW v_payslip AS
SELECT s.*, p.period, e.emp_no, e.name, d.name AS dept_name, e.line_code, e.team
FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id JOIN hr_employee e ON e.id = s.employee_id JOIN org_dept d ON d.id = s.dept_id;

DROP VIEW IF EXISTS v_expense;
CREATE VIEW v_expense AS
SELECT x.*, i.instance_no, i.status AS wf_status,
       (SELECT GROUP_CONCAT(DISTINCT pe.name) FROM wf_task t JOIN hr_employee pe ON pe.id = t.assignee_emp_id
         WHERE t.instance_id = i.id AND t.status = 'PENDING') AS pending_with
FROM fin_expense x LEFT JOIN wf_instance i ON i.id = x.wf_instance_id;
