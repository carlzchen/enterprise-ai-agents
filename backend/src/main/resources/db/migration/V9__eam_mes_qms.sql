-- 第二批：EAM 设备管理 / MES 深化（工艺路线、序列号与出厂测试、IPQC、安灯、班次 OEE）/ QMS 深化（8D、SPC、FMEA、量具、OQC）

-- ========== EAM ==========
CREATE TABLE eam_equipment (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  code          VARCHAR(30)   NOT NULL UNIQUE,
  name          VARCHAR(100)  NOT NULL,
  category      VARCHAR(20)   NOT NULL COMMENT 'WINDING绕线/PRESS冲压/ASSEMBLY装配线/TEST测试台/MAGNETIZE充磁/BALANCE动平衡/VARNISH浸漆/MOLDING注塑/UTILITY动力',
  line_code     VARCHAR(20)   COMMENT '所属产线，公用设备为空',
  model         VARCHAR(50),
  vendor        VARCHAR(100),
  purchase_date DATE          NOT NULL,
  purchase_cost DECIMAL(18,2) NOT NULL,
  criticality   CHAR(1)       NOT NULL COMMENT 'A 关键 / B 重要 / C 一般',
  loss_factor   DECIMAL(4,2)  NOT NULL COMMENT '停机时损失的产线产能比例（瓶颈设备 = 1）',
  mtbf_days     INT           NOT NULL COMMENT '设计平均故障间隔（天）',
  status        VARCHAR(12)   NOT NULL DEFAULT 'RUNNING' COMMENT 'RUNNING运行/DOWN故障/MAINT保养中/SCRAPPED报废'
) COMMENT '设备台账';

CREATE TABLE eam_pm_plan (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  equipment_id  BIGINT       NOT NULL,
  name          VARCHAR(100) NOT NULL,
  interval_days INT          NOT NULL,
  est_minutes   INT          NOT NULL COMMENT '计划停机分钟',
  checklist     VARCHAR(500),
  last_done     DATE,
  next_due      DATE         NOT NULL,
  INDEX idx_pm_due (next_due)
) COMMENT '预防性保养计划';

CREATE TABLE eam_work_order (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  wo_no             VARCHAR(30)   NOT NULL UNIQUE,
  equipment_id      BIGINT        NOT NULL,
  type              VARCHAR(12)   NOT NULL COMMENT 'BREAKDOWN故障维修/PM预防保养',
  status            VARCHAR(12)   NOT NULL COMMENT 'OPEN待维修/IN_PROGRESS维修中/DONE已完成',
  fault_code        VARCHAR(20),
  fault_desc        VARCHAR(200),
  cause             VARCHAR(200),
  action            VARCHAR(200),
  reported_at       DATETIME      NOT NULL,
  started_at        DATETIME,
  finished_at       DATETIME,
  downtime_min      INT           NOT NULL DEFAULT 0,
  labor_hours       DECIMAL(6,2)  NOT NULL DEFAULT 0,
  spare_cost        DECIMAL(18,2) NOT NULL DEFAULT 0,
  reporter          VARCHAR(50),
  technician_emp_id BIGINT,
  pm_plan_id        BIGINT,
  INDEX idx_ewo_eq (equipment_id),
  INDEX idx_ewo_date (reported_at)
) COMMENT '设备维修 / 保养工单';

CREATE TABLE eam_spare_usage (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  work_order_id BIGINT        NOT NULL,
  material_id   BIGINT        NOT NULL,
  qty           DECIMAL(18,3) NOT NULL,
  unit_cost     DECIMAL(18,4) NOT NULL,
  amount        DECIMAL(18,2) NOT NULL,
  INDEX idx_esu_wo (work_order_id)
) COMMENT '维修备件领用（计入制造费用）';

-- ========== MES ==========
CREATE TABLE mes_routing (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_key     VARCHAR(40)  NOT NULL COMMENT '适用对象：SERIES:SERVO / SEMI:SA-ST 等',
  op_seq       INT          NOT NULL,
  op_code      VARCHAR(20)  NOT NULL,
  op_name      VARCHAR(50)  NOT NULL,
  line_code    VARCHAR(20)  NOT NULL,
  std_seconds  DECIMAL(8,1) NOT NULL COMMENT '标准工时（秒/台）',
  is_key       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '关键工序',
  inspect_type VARCHAR(10)  COMMENT 'FIRST首检/PATROL巡检',
  UNIQUE KEY uk_routing (item_key, op_seq)
) COMMENT '工艺路线（标准工时用于劳动效率）';

CREATE TABLE mes_serial (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  sn            VARCHAR(30) NOT NULL UNIQUE COMMENT '整机序列号',
  product_id    BIGINT      NOT NULL,
  wo_no         VARCHAR(30) NOT NULL COMMENT '生产工单（成品批次）',
  produced_date DATE        NOT NULL,
  result        VARCHAR(6)  NOT NULL COMMENT 'PASS/FAIL',
  delivery_id   BIGINT      COMMENT '出货发货单',
  INDEX idx_sn_wo (wo_no),
  INDEX idx_sn_prod (product_id, delivery_id),
  INDEX idx_sn_delivery (delivery_id)
) COMMENT '伺服电机整机序列号（单台追溯）';

CREATE TABLE mes_test_record (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  sn              VARCHAR(30)   NOT NULL,
  product_id      BIGINT        NOT NULL,
  line_code       VARCHAR(20)   NOT NULL,
  test_date       DATE          NOT NULL,
  back_emf        DECIMAL(8,3)  NOT NULL COMMENT '反电势常数 V/krpm',
  no_load_current DECIMAL(8,4)  NOT NULL COMMENT '空载电流 A',
  encoder_offset  DECIMAL(8,4)  NOT NULL COMMENT '编码器零位偏差 °',
  insulation_mohm DECIMAL(10,1) NOT NULL COMMENT '绝缘电阻 MΩ',
  vibration       DECIMAL(6,3)  NOT NULL COMMENT '振动速度 mm/s',
  result          VARCHAR(6)    NOT NULL,
  fail_item       VARCHAR(20),
  INDEX idx_test_prod_date (product_id, test_date),
  INDEX idx_test_sn (sn)
) COMMENT '出厂测试数据（测试台采集，SPC 数据源）';

CREATE TABLE mes_ipqc (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  wo_no        VARCHAR(30)  NOT NULL,
  line_code    VARCHAR(20)  NOT NULL,
  type         VARCHAR(10)  NOT NULL COMMENT 'FIRST首检/PATROL巡检',
  inspect_time DATETIME     NOT NULL,
  inspector    VARCHAR(50)  NOT NULL,
  result       VARCHAR(6)   NOT NULL,
  remark       VARCHAR(200),
  INDEX idx_ipqc_wo (wo_no),
  INDEX idx_ipqc_time (inspect_time)
) COMMENT '过程检验（首检 / 巡检）';

CREATE TABLE mes_andon (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  line_code    VARCHAR(20)  NOT NULL,
  type         VARCHAR(12)  NOT NULL COMMENT 'MATERIAL缺料/EQUIPMENT设备/QUALITY质量',
  description  VARCHAR(200) NOT NULL,
  ref_no       VARCHAR(30)  COMMENT '关联单据：工单 / 维修单',
  raised_at    DATETIME     NOT NULL,
  responded_at DATETIME,
  resolved_at  DATETIME,
  status       VARCHAR(10)  NOT NULL COMMENT 'OPEN/RESOLVED',
  raised_by    VARCHAR(50),
  INDEX idx_andon_time (raised_at),
  INDEX idx_andon_status (status)
) COMMENT '安灯异常呼叫';

CREATE TABLE mes_line_day (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  line_code    VARCHAR(20)   NOT NULL,
  work_date    DATE          NOT NULL,
  planned_min  INT           NOT NULL,
  loss_min     INT           NOT NULL COMMENT '设备停机造成的产能损失（分钟当量）',
  capacity     INT           NOT NULL,
  total_qty    DECIMAL(18,3) NOT NULL,
  good_qty     DECIMAL(18,3) NOT NULL,
  std_hours    DECIMAL(10,2) NOT NULL COMMENT '产出折算标准工时',
  actual_hours DECIMAL(10,2) NOT NULL COMMENT '产线员工出勤工时（含加班）',
  availability DECIMAL(6,4)  NOT NULL,
  performance  DECIMAL(6,4)  NOT NULL,
  quality      DECIMAL(6,4)  NOT NULL,
  oee          DECIMAL(6,4)  NOT NULL,
  UNIQUE KEY uk_line_day (line_code, work_date)
) COMMENT '产线班次结算：OEE 与劳动效率';

-- ========== QMS ==========
CREATE TABLE qms_8d (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  report_no      VARCHAR(30)   NOT NULL UNIQUE,
  complaint_id   BIGINT        NOT NULL,
  current_step   INT           NOT NULL COMMENT '当前步骤 1~8',
  status         VARCHAR(10)   NOT NULL COMMENT 'OPEN/CLOSED',
  d1_team        VARCHAR(300),
  d2_problem     VARCHAR(500),
  d3_containment VARCHAR(500),
  d4_root_cause  VARCHAR(1000),
  d5_actions     VARCHAR(1000),
  d6_verification VARCHAR(500),
  d7_prevention  VARCHAR(500),
  d8_closure     VARCHAR(300),
  opened_date    DATE          NOT NULL,
  due_date       DATE          NOT NULL,
  closed_date    DATE,
  owner          VARCHAR(50),
  UNIQUE KEY uk_8d_complaint (complaint_id)
) COMMENT '8D 问题解决报告';

CREATE TABLE qms_fmea (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  series             VARCHAR(20)  NOT NULL,
  process_step       VARCHAR(50)  NOT NULL,
  failure_mode       VARCHAR(100) NOT NULL,
  effect             VARCHAR(100) NOT NULL,
  cause              VARCHAR(100) NOT NULL,
  current_control    VARCHAR(100),
  severity           INT          NOT NULL,
  occurrence         INT          NOT NULL,
  detection          INT          NOT NULL,
  action             VARCHAR(200),
  action_owner       VARCHAR(50),
  revised_occurrence INT,
  revised_detection  INT,
  updated_date       DATE         NOT NULL
) COMMENT '过程 FMEA';

CREATE TABLE qms_gauge (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  code              VARCHAR(30)  NOT NULL UNIQUE,
  name              VARCHAR(100) NOT NULL,
  type              VARCHAR(30)  NOT NULL,
  line_code         VARCHAR(20),
  range_spec        VARCHAR(50),
  cal_interval_days INT          NOT NULL,
  last_cal_date     DATE,
  next_due          DATE         NOT NULL,
  status            VARCHAR(10)  NOT NULL COMMENT 'OK/LOCKED(校准不合格停用)/SCRAPPED'
) COMMENT '量具与测试设备';

CREATE TABLE qms_calibration (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  gauge_id       BIGINT       NOT NULL,
  cal_date       DATE         NOT NULL,
  due_date       DATE         NOT NULL COMMENT '本次应校准日（用于计算按期率）',
  result         VARCHAR(10)  NOT NULL COMMENT 'PASS/ADJUSTED/FAIL',
  deviation      VARCHAR(50),
  calibrated_by  VARCHAR(50)  NOT NULL,
  certificate_no VARCHAR(30),
  INDEX idx_cal_gauge (gauge_id)
) COMMENT '量具校准记录';

CREATE TABLE qms_oqc (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  delivery_id  BIGINT       NOT NULL,
  product_id   BIGINT       NOT NULL,
  inspect_date DATE         NOT NULL,
  lot_size     INT          NOT NULL,
  aql          DECIMAL(4,2) NOT NULL,
  code_letter  CHAR(1)      NOT NULL,
  sample_size  INT          NOT NULL,
  ac           INT          NOT NULL,
  re           INT          NOT NULL,
  defects      INT          NOT NULL,
  result       VARCHAR(10)  NOT NULL COMMENT 'PASS/REWORK（判退后返工复检放行）',
  inspector    VARCHAR(50)  NOT NULL,
  INDEX idx_oqc_delivery (delivery_id),
  INDEX idx_oqc_date (inspect_date)
) COMMENT '出货检验（GB/T 2828.1 一般检验水平 II，正常检验一次抽样）';

-- ========== 视图 ==========
CREATE VIEW v_equipment AS
SELECT q.*,
  (SELECT COUNT(*) FROM eam_work_order w WHERE w.equipment_id = q.id AND w.type = 'BREAKDOWN') AS breakdowns,
  (SELECT COALESCE(SUM(w.downtime_min), 0) FROM eam_work_order w WHERE w.equipment_id = q.id AND w.type = 'BREAKDOWN') AS breakdown_min,
  (SELECT COALESCE(SUM(w.spare_cost), 0) FROM eam_work_order w WHERE w.equipment_id = q.id) AS spare_cost,
  (SELECT MIN(p.next_due) FROM eam_pm_plan p WHERE p.equipment_id = q.id) AS next_pm
FROM eam_equipment q;

CREATE VIEW v_eam_work_order AS
SELECT w.*, q.code AS equipment_code, q.name AS equipment_name, q.line_code, q.category, e.name AS technician
FROM eam_work_order w JOIN eam_equipment q ON q.id = w.equipment_id LEFT JOIN hr_employee e ON e.id = w.technician_emp_id;

CREATE VIEW v_pm_plan AS
SELECT p.*, q.code AS equipment_code, q.name AS equipment_name, q.line_code, DATEDIFF(p.next_due, CURDATE()) AS days_to_due
FROM eam_pm_plan p JOIN eam_equipment q ON q.id = p.equipment_id;

CREATE VIEW v_serial AS
SELECT s.*, p.code AS product_code, d.delivery_no, c.name AS customer_name, t.back_emf, t.no_load_current, t.encoder_offset,
       t.insulation_mohm, t.vibration, t.fail_item
FROM mes_serial s JOIN md_product p ON p.id = s.product_id LEFT JOIN sal_delivery d ON d.id = s.delivery_id
LEFT JOIN md_customer c ON c.id = d.customer_id LEFT JOIN mes_test_record t ON t.sn = s.sn;

CREATE VIEW v_ipqc AS SELECT i.* FROM mes_ipqc i;

CREATE VIEW v_andon AS
SELECT a.*, TIMESTAMPDIFF(MINUTE, a.raised_at, a.responded_at) AS response_min, TIMESTAMPDIFF(MINUTE, a.raised_at, a.resolved_at) AS resolve_min,
       DATE(a.raised_at) AS raised_date
FROM mes_andon a;

CREATE VIEW v_line_day AS SELECT d.*, ROUND(d.std_hours / NULLIF(d.actual_hours, 0), 4) AS labor_efficiency FROM mes_line_day d;

CREATE VIEW v_8d AS
SELECT r.*, c.complaint_no, c.defect_type, c.severity, c.status AS complaint_status, cu.name AS customer_name, p.code AS product_code,
       DATEDIFF(COALESCE(r.closed_date, CURDATE()), r.opened_date) AS age_days
FROM qms_8d r JOIN qms_complaint c ON c.id = r.complaint_id JOIN md_customer cu ON cu.id = c.customer_id JOIN md_product p ON p.id = c.product_id;

CREATE VIEW v_fmea AS
SELECT f.*, f.severity * f.occurrence * f.detection AS rpn,
       CASE WHEN f.revised_occurrence IS NULL THEN NULL ELSE f.severity * f.revised_occurrence * COALESCE(f.revised_detection, f.detection) END AS revised_rpn
FROM qms_fmea f;

CREATE VIEW v_gauge AS
SELECT g.*, DATEDIFF(g.next_due, CURDATE()) AS days_to_due,
       (SELECT COUNT(*) FROM qms_calibration c WHERE c.gauge_id = g.id) AS cal_count
FROM qms_gauge g;

CREATE VIEW v_calibration AS
SELECT c.*, g.code AS gauge_code, g.name AS gauge_name, g.type, DATEDIFF(c.cal_date, c.due_date) AS late_days
FROM qms_calibration c JOIN qms_gauge g ON g.id = c.gauge_id;

CREATE VIEW v_oqc AS
SELECT o.*, d.delivery_no, cu.name AS customer_name, p.code AS product_code
FROM qms_oqc o JOIN sal_delivery d ON d.id = o.delivery_id JOIN md_customer cu ON cu.id = d.customer_id JOIN md_product p ON p.id = o.product_id;

-- 产线班次：日产能 = 单班产能 × 班次（APS 排产与 OEE 理论产能共用）
ALTER TABLE mes_line ADD COLUMN shifts INT NOT NULL DEFAULT 1 COMMENT '每日班次';
UPDATE mes_line SET shifts = CASE line_code WHEN 'L1' THEN 3 WHEN 'L2' THEN 2 WHEN 'L3' THEN 3 WHEN 'L4' THEN 2 WHEN 'L5' THEN 2
                                            WHEN 'L6' THEN 3 WHEN 'L7' THEN 2 ELSE 1 END;
-- 组件线按实际节拍校正单班产能（原值偏高）
UPDATE mes_line SET daily_capacity = 520 WHERE line_code = 'L7';
UPDATE mes_line SET daily_capacity = 640 WHERE line_code = 'L8';

CREATE TABLE mes_test_spec (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id     BIGINT       NOT NULL,
  characteristic VARCHAR(30)  NOT NULL COMMENT 'back_emf / no_load_current / encoder_offset / insulation_mohm / vibration',
  name           VARCHAR(30)  NOT NULL,
  unit           VARCHAR(10)  NOT NULL,
  nominal        DECIMAL(10,4) NOT NULL,
  lsl            DECIMAL(10,4) COMMENT '规格下限（单侧规格为空）',
  usl            DECIMAL(10,4) COMMENT '规格上限（单侧规格为空）',
  UNIQUE KEY uk_spec (product_id, characteristic)
) COMMENT '出厂测试规格（SPC 规格限）';
