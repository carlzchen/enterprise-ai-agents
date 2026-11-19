-- 第五批：售后服务 / IT 服务台 / 安全环保 / 能源管理

-- ========== 科目 ==========
INSERT INTO gl_account (code, name, category, direction) VALUES
 ('2801', '预计负债-产品质量保证', 'LIABILITY', 'CREDIT'),
 ('6051', '其他业务收入', 'REVENUE', 'CREDIT'),
 ('6402', '其他业务成本', 'EXPENSE', 'DEBIT');

-- ========== 售后服务 ==========
CREATE TABLE svc_ticket (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  ticket_no       VARCHAR(30)   NOT NULL UNIQUE,
  customer_id     BIGINT        NOT NULL,
  product_id      BIGINT,
  sn              VARCHAR(30)   COMMENT '伺服整机序列号（单台追溯到工单、测试记录、发货单）',
  delivery_id     BIGINT,
  channel         VARCHAR(10)   NOT NULL COMMENT 'PHONE/EMAIL/WECHAT/PORTAL',
  type            VARCHAR(10)   NOT NULL COMMENT 'FAULT故障/CONSULT技术咨询/INSTALL安装调试',
  priority        VARCHAR(2)    NOT NULL COMMENT 'P1 客户产线停机 / P2 / P3',
  symptom         VARCHAR(200),
  fault_code      VARCHAR(20),
  warranty_status VARCHAR(4)    NOT NULL COMMENT 'IN保内/OUT保外/NA不适用',
  warranty_end    DATE,
  engineer_emp_id BIGINT,
  status          VARCHAR(10)   NOT NULL COMMENT 'NEW/ASSIGNED/RESOLVED/CLOSED（RMA 先换后修，开单即解决）',
  created_at      DATETIME      NOT NULL,
  responded_at    DATETIME,
  resolved_at     DATETIME,
  closed_at       DATETIME,
  response_due    DATETIME      NOT NULL,
  resolve_due     DATETIME      NOT NULL,
  sla_breached    TINYINT(1)    NOT NULL DEFAULT 0,
  resolution      VARCHAR(10)   COMMENT 'REMOTE远程指导/ONSITE上门/RMA返修/NO_FAULT无故障',
  resolution_note VARCHAR(300),
  service_fee     DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '保外付费维修收费（不含税）',
  satisfaction    TINYINT       COMMENT '1-5',
  INDEX idx_st_cust (customer_id),
  INDEX idx_st_sn (sn),
  INDEX idx_st_created (created_at)
) COMMENT '售后服务工单';

CREATE TABLE svc_rma (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  rma_no        VARCHAR(30)   NOT NULL UNIQUE,
  source        VARCHAR(10)   NOT NULL COMMENT 'TICKET 单台 / COMPLAINT 批量客诉',
  ticket_id     BIGINT,
  complaint_id  BIGINT,
  customer_id   BIGINT        NOT NULL,
  product_id    BIGINT        NOT NULL,
  sn            VARCHAR(30),
  qty           DECIMAL(18,3) NOT NULL,
  in_warranty   TINYINT(1)    NOT NULL,
  status        VARCHAR(10)   NOT NULL COMMENT 'OPEN已批准待退回/RECEIVED已收货/CLOSED已处理',
  created_date  DATE          NOT NULL,
  received_date DATE,
  closed_date   DATE,
  disposition   VARCHAR(10)   COMMENT 'REPLACE换新/REPAIR维修/NO_FAULT无故障退回',
  finding       VARCHAR(300),
  cost          DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '换新 / 维修耗用成本',
  claim_amount  DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '客户质量索赔（停线损失、挑选返工费用），银行支付',
  INDEX idx_rma_status (status),
  INDEX idx_rma_complaint (complaint_id)
) COMMENT '退货授权（RMA）';

CREATE TABLE svc_warranty_ledger (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  entry_date DATE          NOT NULL,
  period     VARCHAR(7)    NOT NULL,
  type       VARCHAR(10)   NOT NULL COMMENT 'OPENING期初/ACCRUAL计提/TOP_UP余额不足补提/CLAIM索赔耗用/TRUE_UP年末调整',
  amount     DECIMAL(18,2) NOT NULL COMMENT '增加预计负债为正，耗用为负',
  rate       DECIMAL(7,5),
  ref_no     VARCHAR(30),
  note       VARCHAR(200),
  INDEX idx_wl_period (period)
) COMMENT '质量保证金（预计负债）明细账';

-- ========== IT 服务台 ==========
CREATE TABLE it_ci (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  ci_no         VARCHAR(20)  NOT NULL UNIQUE,
  type          VARCHAR(10)  NOT NULL COMMENT 'LAPTOP/DESKTOP/TERMINAL/SERVER/NETWORK/PRINTER',
  name          VARCHAR(100) NOT NULL,
  model         VARCHAR(60),
  status        VARCHAR(10)  NOT NULL COMMENT 'IN_USE/STOCK/REPAIR/RETIRED',
  owner_emp_id  BIGINT,
  dept_id       BIGINT,
  location      VARCHAR(50),
  purchase_date DATE         NOT NULL,
  criticality   VARCHAR(6)   NOT NULL DEFAULT 'LOW',
  fa_asset_id   BIGINT       COMMENT '对应固定资产卡片（达到资本化标准的）',
  INDEX idx_ci_owner (owner_emp_id),
  INDEX idx_ci_type (type, status)
) COMMENT 'IT 配置项（CMDB）';

CREATE TABLE it_license (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(60)   NOT NULL UNIQUE,
  vendor     VARCHAR(60)   NOT NULL,
  seats      INT           NOT NULL,
  unit_cost  DECIMAL(18,2) NOT NULL COMMENT '每席位年费',
  scope      VARCHAR(20)   NOT NULL COMMENT 'ALL 全员 / 部门编码',
  renew_date DATE          NOT NULL
) COMMENT '软件许可';

CREATE TABLE it_license_assign (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  license_id    BIGINT NOT NULL,
  emp_id        BIGINT NOT NULL,
  assigned_date DATE   NOT NULL,
  revoked_date  DATE,
  INDEX idx_la (license_id, revoked_date),
  INDEX idx_la_emp (emp_id)
) COMMENT '许可分配';

CREATE TABLE it_ticket (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  ticket_no        VARCHAR(30)  NOT NULL UNIQUE,
  type             VARCHAR(10)  NOT NULL COMMENT 'INCIDENT故障/REQUEST服务请求/CHANGE变更',
  category         VARCHAR(20)  NOT NULL,
  title            VARCHAR(200) NOT NULL,
  description      VARCHAR(1000),
  requester_emp_id BIGINT       NOT NULL,
  assignee_emp_id  BIGINT,
  ci_id            BIGINT,
  impact           VARCHAR(6)   NOT NULL,
  urgency          VARCHAR(6)   NOT NULL,
  priority         VARCHAR(2)   NOT NULL,
  status           VARCHAR(12)  NOT NULL COMMENT 'NEW/IN_PROGRESS/RESOLVED/CLOSED；变更：APPROVING/APPROVED/REJECTED/CLOSED',
  created_at       DATETIME     NOT NULL,
  responded_at     DATETIME,
  resolved_at      DATETIME,
  closed_at        DATETIME,
  response_due     DATETIME     NOT NULL,
  resolve_due      DATETIME     NOT NULL,
  sla_breached     TINYINT(1)   NOT NULL DEFAULT 0,
  resolution       VARCHAR(500),
  root_cause       VARCHAR(500),
  related_id       BIGINT       COMMENT '关联工单（故障 → 变更）',
  risk             VARCHAR(6)   COMMENT '变更风险 LOW/MEDIUM/HIGH',
  planned_start    DATETIME,
  wf_instance_id   BIGINT,
  satisfaction     TINYINT,
  INDEX idx_it_created (created_at),
  INDEX idx_it_status (status),
  INDEX idx_it_requester (requester_emp_id)
) COMMENT 'IT 服务台工单';

-- ========== 安全环保 ==========
CREATE TABLE ehs_hazard (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  hazard_no      VARCHAR(30)  NOT NULL UNIQUE,
  source         VARCHAR(10)  NOT NULL COMMENT 'INSPECTION周检/REPORT员工随手拍/INCIDENT事故整改/AUDIT审核',
  dept_id        BIGINT       NOT NULL,
  location       VARCHAR(60),
  category       VARCHAR(10)  NOT NULL COMMENT '电气/机械/消防/化学品/叉车/作业环境/职业健康',
  description    VARCHAR(300) NOT NULL,
  level          VARCHAR(8)   NOT NULL COMMENT 'GENERAL一般/MAJOR重大',
  found_date     DATE         NOT NULL,
  found_by       BIGINT,
  owner_emp_id   BIGINT       NOT NULL,
  deadline       DATE         NOT NULL,
  rectified_date DATE,
  verified_date  DATE,
  action         VARCHAR(300),
  status         VARCHAR(10)  NOT NULL COMMENT 'OPEN/RECTIFIED/CLOSED',
  INDEX idx_hz_status (status, deadline),
  INDEX idx_hz_found (found_date)
) COMMENT '隐患排查治理';

CREATE TABLE ehs_incident (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  incident_no       VARCHAR(30)  NOT NULL UNIQUE,
  occurred_at       DATETIME     NOT NULL,
  dept_id           BIGINT       NOT NULL,
  location          VARCHAR(60),
  type              VARCHAR(10)  NOT NULL COMMENT 'NEAR_MISS未遂/FIRST_AID急救/RECORDABLE可记录/LOST_TIME损工',
  employee_id       BIGINT,
  description       VARCHAR(300) NOT NULL,
  injury            VARCHAR(100),
  lost_days         INT          NOT NULL DEFAULT 0,
  root_cause        VARCHAR(300),
  corrective_action VARCHAR(300),
  status            VARCHAR(8)   NOT NULL COMMENT 'OPEN/CLOSED',
  closed_date       DATE,
  INDEX idx_inc_date (occurred_at)
) COMMENT '安全事故 / 未遂事件';

CREATE TABLE ehs_permit (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  permit_no        VARCHAR(30)  NOT NULL UNIQUE,
  type             VARCHAR(10)  NOT NULL COMMENT 'HOT_WORK动火/HEIGHT高处/CONFINED受限空间/ELECTRICAL临时用电',
  level            VARCHAR(2)   NOT NULL DEFAULT '2' COMMENT '动火等级 1 一级（高风险区域）/ 2 二级',
  dept_id          BIGINT       NOT NULL,
  location         VARCHAR(60)  NOT NULL,
  work_desc        VARCHAR(200) NOT NULL,
  applicant_emp_id BIGINT       NOT NULL,
  guardian_emp_id  BIGINT       COMMENT '监护人',
  planned_start    DATETIME     NOT NULL,
  planned_end      DATETIME     NOT NULL,
  status           VARCHAR(10)  NOT NULL COMMENT 'APPROVING/APPROVED/REJECTED/CLOSED',
  wf_instance_id   BIGINT,
  closed_at        DATETIME,
  INDEX idx_pm_start (planned_start)
) COMMENT '危险作业许可（作业票）';

CREATE TABLE ehs_waste (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  waste_code  VARCHAR(10)   NOT NULL COMMENT '国家危险废物名录类别 HW08 / HW12 / HW49',
  waste_name  VARCHAR(60)   NOT NULL,
  move_type   VARCHAR(10)   NOT NULL COMMENT 'GENERATE产生入库/TRANSFER转移处置',
  move_date   DATE          NOT NULL,
  qty_ton     DECIMAL(10,3) NOT NULL,
  dept_id     BIGINT,
  manifest_no VARCHAR(30)   COMMENT '转移联单号',
  receiver    VARCHAR(60),
  cost        DECIMAL(18,2) NOT NULL DEFAULT 0,
  INDEX idx_w (waste_code, move_date)
) COMMENT '危险废物台账';

-- ========== 能源 ==========
CREATE TABLE en_meter (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  code      VARCHAR(20) NOT NULL UNIQUE,
  name      VARCHAR(60) NOT NULL,
  type      VARCHAR(10) NOT NULL COMMENT 'LINE产线/UTILITY公用动力/OFFICE办公研发/DORM宿舍',
  line_code VARCHAR(20),
  dept_id   BIGINT
) COMMENT '电表';

CREATE TABLE en_reading (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  meter_id   BIGINT        NOT NULL,
  read_date  DATE          NOT NULL,
  sharp_kwh  DECIMAL(12,2) NOT NULL,
  peak_kwh   DECIMAL(12,2) NOT NULL,
  flat_kwh   DECIMAL(12,2) NOT NULL,
  valley_kwh DECIMAL(12,2) NOT NULL,
  total_kwh  DECIMAL(12,2) NOT NULL,
  UNIQUE KEY uk_reading (meter_id, read_date),
  INDEX idx_rd_date (read_date)
) COMMENT '电表日读数（分时段）';

CREATE TABLE en_bill (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  period          VARCHAR(7)    NOT NULL UNIQUE,
  total_kwh       DECIMAL(14,2) NOT NULL,
  sharp_kwh       DECIMAL(14,2) NOT NULL,
  peak_kwh        DECIMAL(14,2) NOT NULL,
  flat_kwh        DECIMAL(14,2) NOT NULL,
  valley_kwh      DECIMAL(14,2) NOT NULL,
  flat_price      DECIMAL(8,4)  NOT NULL,
  energy_charge   DECIMAL(18,2) NOT NULL,
  capacity_charge DECIMAL(18,2) NOT NULL COMMENT '基本电费（按变压器容量）',
  water_m3        DECIMAL(12,2) NOT NULL,
  water_charge    DECIMAL(18,2) NOT NULL,
  total_amount    DECIMAL(18,2) NOT NULL,
  avg_price       DECIMAL(8,4)  NOT NULL COMMENT '综合电价（电费 ÷ 电量）',
  carbon_t        DECIMAL(12,3) NOT NULL,
  good_qty        DECIMAL(18,3) NOT NULL COMMENT '当月成品产出（良品）',
  kwh_per_unit    DECIMAL(10,4) NOT NULL,
  expense_id      BIGINT        COMMENT '付款走费用报销流程（超预算需总经理审批）'
) COMMENT '月度电费水费账单';

-- ========== 视图 ==========
CREATE VIEW v_svc_ticket AS
SELECT t.*, c.name AS customer_name, c.code AS customer_code, p.code AS product_code, p.name AS product_name, p.series,
       e.name AS engineer_name, DATE(t.created_at) AS created_date,
       ROUND(TIMESTAMPDIFF(MINUTE, t.created_at, t.responded_at) / 60, 1) AS response_hours,
       ROUND(TIMESTAMPDIFF(MINUTE, t.created_at, t.resolved_at) / 60, 1) AS resolve_hours
FROM svc_ticket t JOIN md_customer c ON c.id = t.customer_id LEFT JOIN md_product p ON p.id = t.product_id
LEFT JOIN hr_employee e ON e.id = t.engineer_emp_id;

CREATE VIEW v_svc_rma AS
SELECT r.*, c.name AS customer_name, p.code AS product_code, p.name AS product_name, p.series, t.ticket_no, q.complaint_no
FROM svc_rma r JOIN md_customer c ON c.id = r.customer_id JOIN md_product p ON p.id = r.product_id
LEFT JOIN svc_ticket t ON t.id = r.ticket_id LEFT JOIN qms_complaint q ON q.id = r.complaint_id;

CREATE VIEW v_warranty_ledger AS
SELECT l.*, SUM(l.amount) OVER (ORDER BY l.entry_date, l.id) AS balance FROM svc_warranty_ledger l;

CREATE VIEW v_it_ticket AS
SELECT t.*, r.name AS requester_name, d.name AS requester_dept, a.name AS assignee_name, ci.ci_no, ci.name AS ci_name,
       DATE(t.created_at) AS created_date,
       ROUND(TIMESTAMPDIFF(MINUTE, t.created_at, t.resolved_at) / 60, 1) AS resolve_hours
FROM it_ticket t JOIN hr_employee r ON r.id = t.requester_emp_id LEFT JOIN org_dept d ON d.id = r.dept_id
LEFT JOIN hr_employee a ON a.id = t.assignee_emp_id LEFT JOIN it_ci ci ON ci.id = t.ci_id;

CREATE VIEW v_it_ci AS
SELECT ci.*, e.name AS owner_name, d.name AS dept_name, TIMESTAMPDIFF(MONTH, ci.purchase_date, CURDATE()) AS age_months,
       fa.asset_no
FROM it_ci ci LEFT JOIN hr_employee e ON e.id = ci.owner_emp_id LEFT JOIN org_dept d ON d.id = ci.dept_id
LEFT JOIN fa_asset fa ON fa.id = ci.fa_asset_id;

CREATE VIEW v_it_license AS
SELECT l.*, (SELECT COUNT(*) FROM it_license_assign a WHERE a.license_id = l.id AND a.revoked_date IS NULL) AS used,
       l.seats - (SELECT COUNT(*) FROM it_license_assign a WHERE a.license_id = l.id AND a.revoked_date IS NULL) AS available,
       l.seats * l.unit_cost AS annual_cost
FROM it_license l;

CREATE VIEW v_ehs_hazard AS
SELECT h.*, d.name AS dept_name, o.name AS owner_name, f.name AS found_by_name,
       CASE WHEN h.rectified_date IS NULL THEN CURDATE() > h.deadline ELSE h.rectified_date > h.deadline END AS overdue
FROM ehs_hazard h JOIN org_dept d ON d.id = h.dept_id JOIN hr_employee o ON o.id = h.owner_emp_id LEFT JOIN hr_employee f ON f.id = h.found_by;

CREATE VIEW v_ehs_incident AS
SELECT i.*, d.name AS dept_name, e.name AS employee_name, DATE(i.occurred_at) AS occurred_date
FROM ehs_incident i JOIN org_dept d ON d.id = i.dept_id LEFT JOIN hr_employee e ON e.id = i.employee_id;

CREATE VIEW v_ehs_permit AS
SELECT p.*, d.name AS dept_name, a.name AS applicant_name, g.name AS guardian_name, DATE(p.planned_start) AS work_date
FROM ehs_permit p JOIN org_dept d ON d.id = p.dept_id JOIN hr_employee a ON a.id = p.applicant_emp_id LEFT JOIN hr_employee g ON g.id = p.guardian_emp_id;

CREATE VIEW v_ehs_waste AS
SELECT w.*, d.name AS dept_name FROM ehs_waste w LEFT JOIN org_dept d ON d.id = w.dept_id;

CREATE VIEW v_energy_line_day AS
SELECT m.line_code, r.read_date, r.total_kwh, r.peak_kwh + r.sharp_kwh AS peak_kwh, r.valley_kwh, l.good_qty,
       CASE WHEN l.good_qty > 0 THEN ROUND(r.total_kwh / l.good_qty, 4) END AS kwh_per_unit
FROM en_reading r JOIN en_meter m ON m.id = r.meter_id AND m.type = 'LINE'
LEFT JOIN mes_line_day l ON l.line_code = m.line_code AND l.work_date = r.read_date;

CREATE VIEW v_energy_meter_month AS
SELECT m.code, m.name, m.type, m.line_code, DATE_FORMAT(r.read_date, '%Y-%m') AS period, SUM(r.total_kwh) AS total_kwh,
       SUM(r.sharp_kwh) AS sharp_kwh, SUM(r.peak_kwh) AS peak_kwh, SUM(r.flat_kwh) AS flat_kwh, SUM(r.valley_kwh) AS valley_kwh
FROM en_reading r JOIN en_meter m ON m.id = r.meter_id GROUP BY m.code, m.name, m.type, m.line_code, DATE_FORMAT(r.read_date, '%Y-%m');
