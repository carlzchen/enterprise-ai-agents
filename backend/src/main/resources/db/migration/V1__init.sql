-- MiniERP 初始表结构：模拟一家微特电机制造企业（罩极/无刷/步进/伺服/贯流/减速电机）
-- 金额单位：人民币元，外币单据同时保存原币与汇率

-- ========== 系统 ==========
CREATE TABLE sys_user (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(50)  NOT NULL UNIQUE COMMENT '登录名',
  password_hash VARCHAR(100) NOT NULL COMMENT 'BCrypt 密码哈希',
  display_name  VARCHAR(50)  NOT NULL COMMENT '姓名',
  role          VARCHAR(20)  NOT NULL COMMENT '角色：ADMIN/SALES/PURCHASE/WAREHOUSE/PRODUCTION/QUALITY/FINANCE/MANAGEMENT',
  dept          VARCHAR(50)  COMMENT '部门',
  enabled       TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '系统用户';

CREATE TABLE sys_audit_log (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  username   VARCHAR(50)  COMMENT '操作人',
  action     VARCHAR(100) NOT NULL COMMENT '操作，如 sales.confirmOrder',
  detail     VARCHAR(1000) COMMENT '参数摘要',
  success    TINYINT(1)   NOT NULL COMMENT '是否成功',
  error_msg  VARCHAR(500) COMMENT '失败原因',
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_audit_time (created_at)
) COMMENT '操作审计日志（只追加）';

-- ========== 主数据 ==========
CREATE TABLE md_product (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  code            VARCHAR(30)  NOT NULL UNIQUE COMMENT '成品型号编码',
  name            VARCHAR(100) NOT NULL COMMENT '成品名称',
  series          VARCHAR(20)  NOT NULL COMMENT '产品系列：SHADED_POLE罩极/BLDC无刷直流/STEPPER步进/SERVO伺服/CROSSFLOW贯流风机/GEAR齿轮减速',
  business_unit   VARCHAR(30)  NOT NULL COMMENT '事业部：家电电机事业部/智能驱控事业部',
  voltage         VARCHAR(30)  COMMENT '额定电压',
  power_w         DECIMAL(10,2) COMMENT '额定功率(W)',
  rated_speed_rpm INT          COMMENT '额定转速(rpm)',
  conversion_cost DECIMAL(18,4) NOT NULL COMMENT '单位加工费(人工+制造费用)，元/台',
  list_price      DECIMAL(18,4) NOT NULL COMMENT '标准售价(人民币不含税)，元/台',
  lot_size        INT          NOT NULL DEFAULT 100 COMMENT '经济生产批量',
  status          VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE',
  created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '成品主数据';

CREATE TABLE md_material (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  code       VARCHAR(30)  NOT NULL UNIQUE COMMENT '物料编码',
  name       VARCHAR(100) NOT NULL COMMENT '物料名称',
  category   VARCHAR(30)  NOT NULL COMMENT '物料类别：COPPER漆包线/STEEL硅钢片/MAGNET磁钢/BEARING轴承/SHAFT转轴/PLASTIC塑胶件/PCB电路板/ENCODER编码器/GEAR齿轮/PACKING包材',
  uom        VARCHAR(10)  NOT NULL COMMENT '计量单位',
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '原材料主数据';

CREATE TABLE md_material_price (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  material_id BIGINT        NOT NULL,
  price_month DATE          NOT NULL COMMENT '月份(每月1日)',
  price       DECIMAL(18,4) NOT NULL COMMENT '当月参考采购单价(不含税)',
  UNIQUE KEY uk_mat_month (material_id, price_month)
) COMMENT '原材料月度参考价（铜价等大宗波动）';

CREATE TABLE md_bom (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id  BIGINT        NOT NULL,
  material_id BIGINT        NOT NULL,
  qty_per     DECIMAL(18,4) NOT NULL COMMENT '单台用量',
  UNIQUE KEY uk_bom (product_id, material_id)
) COMMENT '物料清单 BOM（单层）';

CREATE TABLE md_customer (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  code               VARCHAR(30)  NOT NULL UNIQUE COMMENT '客户编码',
  name               VARCHAR(100) NOT NULL COMMENT '客户名称',
  industry           VARCHAR(30)  NOT NULL COMMENT '行业：家电/机器人/安防/办公自动化/医疗器械/工业自动化',
  region             VARCHAR(30)  NOT NULL COMMENT '区域：华南/华东/华北/华中/西南/亚太/欧洲/北美',
  country            VARCHAR(30)  NOT NULL COMMENT '国家',
  is_domestic        TINYINT(1)   NOT NULL COMMENT '1=内销 0=外销',
  currency           VARCHAR(3)   NOT NULL COMMENT '结算币种 CNY/USD/EUR',
  credit_limit       DECIMAL(18,2) NOT NULL COMMENT '信用额度(人民币)',
  payment_terms_days INT          NOT NULL COMMENT '账期(天)',
  status             VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE',
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '客户';

CREATE TABLE md_supplier (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  code               VARCHAR(30)  NOT NULL UNIQUE COMMENT '供应商编码',
  name               VARCHAR(100) NOT NULL COMMENT '供应商名称',
  category           VARCHAR(30)  NOT NULL COMMENT '主供物料类别，对应 md_material.category',
  region             VARCHAR(30)  NOT NULL COMMENT '所在地区',
  payment_terms_days INT          NOT NULL COMMENT '账期(天)',
  status             VARCHAR(10)  NOT NULL DEFAULT 'ACTIVE',
  created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '供应商';

CREATE TABLE md_warehouse (
  id   BIGINT AUTO_INCREMENT PRIMARY KEY,
  code VARCHAR(20) NOT NULL UNIQUE,
  name VARCHAR(50) NOT NULL,
  type VARCHAR(10) NOT NULL COMMENT 'RAW原材料仓/FG成品仓'
) COMMENT '仓库';

CREATE TABLE fin_fx_rate (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  currency  VARCHAR(3)    NOT NULL,
  rate_month DATE         NOT NULL COMMENT '月份(每月1日)',
  rate      DECIMAL(12,6) NOT NULL COMMENT '1 外币 = rate 人民币',
  UNIQUE KEY uk_fx (currency, rate_month)
) COMMENT '月度记账汇率';

-- ========== 销售 ==========
CREATE TABLE sal_order (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_no      VARCHAR(30)  NOT NULL UNIQUE COMMENT '销售订单号',
  customer_id   BIGINT       NOT NULL,
  order_date    DATE         NOT NULL COMMENT '下单日期',
  required_date DATE         NOT NULL COMMENT '客户要求交期',
  currency      VARCHAR(3)   NOT NULL,
  fx_rate       DECIMAL(12,6) NOT NULL COMMENT '下单时汇率',
  status        VARCHAR(20)  NOT NULL COMMENT 'DRAFT草稿/CONFIRMED已确认/PARTIAL部分发货/SHIPPED全部发货/CANCELLED已取消',
  salesperson   VARCHAR(50)  COMMENT '销售员',
  remark        VARCHAR(500),
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_so_customer (customer_id),
  INDEX idx_so_status (status),
  INDEX idx_so_date (order_date)
) COMMENT '销售订单';

CREATE TABLE sal_order_line (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  order_id    BIGINT        NOT NULL,
  line_no     INT           NOT NULL,
  product_id  BIGINT        NOT NULL,
  qty         DECIMAL(18,3) NOT NULL COMMENT '订购数量',
  unit_price  DECIMAL(18,4) NOT NULL COMMENT '原币不含税单价',
  shipped_qty DECIMAL(18,3) NOT NULL DEFAULT 0 COMMENT '已发货数量',
  INDEX idx_sol_order (order_id),
  INDEX idx_sol_product (product_id)
) COMMENT '销售订单行';

CREATE TABLE sal_delivery (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  delivery_no   VARCHAR(30) NOT NULL UNIQUE COMMENT '发货单号',
  order_id      BIGINT      NOT NULL,
  customer_id   BIGINT      NOT NULL,
  delivery_date DATE        NOT NULL COMMENT '发货日期（收入确认日）',
  status        VARCHAR(20) NOT NULL COMMENT 'SHIPPED已发货/INVOICED已开票',
  created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_dl_order (order_id),
  INDEX idx_dl_date (delivery_date)
) COMMENT '销售发货单';

CREATE TABLE sal_delivery_line (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  delivery_id   BIGINT        NOT NULL,
  order_line_id BIGINT        NOT NULL,
  product_id    BIGINT        NOT NULL,
  qty           DECIMAL(18,3) NOT NULL COMMENT '发货数量',
  unit_price    DECIMAL(18,4) NOT NULL COMMENT '原币不含税单价',
  unit_cost     DECIMAL(18,4) NOT NULL COMMENT '出库单位成本(人民币，移动加权平均)',
  INDEX idx_dll_delivery (delivery_id),
  INDEX idx_dll_product (product_id)
) COMMENT '销售发货单行';

CREATE TABLE sal_invoice (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  invoice_no   VARCHAR(30)   NOT NULL UNIQUE COMMENT '销售发票号',
  customer_id  BIGINT        NOT NULL,
  delivery_id  BIGINT        NOT NULL UNIQUE,
  invoice_date DATE          NOT NULL,
  due_date     DATE          NOT NULL COMMENT '到期日 = 开票日 + 账期',
  currency     VARCHAR(3)    NOT NULL,
  fx_rate      DECIMAL(12,6) NOT NULL,
  amount       DECIMAL(18,2) NOT NULL COMMENT '原币不含税金额',
  tax_rate     DECIMAL(5,4)  NOT NULL COMMENT '增值税率，内销0.13，出口0',
  amount_cny   DECIMAL(18,2) NOT NULL COMMENT '人民币不含税金额',
  total_cny    DECIMAL(18,2) NOT NULL COMMENT '人民币价税合计（应收金额）',
  received_cny DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '已收金额',
  status       VARCHAR(20)   NOT NULL COMMENT 'OPEN未收/PARTIAL部分收款/PAID已结清',
  INDEX idx_si_customer (customer_id),
  INDEX idx_si_status (status),
  INDEX idx_si_due (due_date)
) COMMENT '销售发票（应收账款）';

CREATE TABLE sal_receipt (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  receipt_no   VARCHAR(30)   NOT NULL UNIQUE COMMENT '收款单号',
  customer_id  BIGINT        NOT NULL,
  invoice_id   BIGINT        NOT NULL,
  receipt_date DATE          NOT NULL,
  amount_cny   DECIMAL(18,2) NOT NULL COMMENT '收款金额(人民币)',
  method       VARCHAR(20)   NOT NULL COMMENT 'BANK电汇/ACCEPTANCE承兑汇票',
  INDEX idx_rc_invoice (invoice_id),
  INDEX idx_rc_date (receipt_date)
) COMMENT '销售收款';

-- ========== 采购 ==========
CREATE TABLE pur_order (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  po_no         VARCHAR(30) NOT NULL UNIQUE COMMENT '采购订单号',
  supplier_id   BIGINT      NOT NULL,
  order_date    DATE        NOT NULL,
  expected_date DATE        NOT NULL COMMENT '预计到货日',
  status        VARCHAR(20) NOT NULL COMMENT 'DRAFT/CONFIRMED/PARTIAL部分到货/RECEIVED已到货/CANCELLED',
  buyer         VARCHAR(50),
  created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_po_supplier (supplier_id),
  INDEX idx_po_status (status)
) COMMENT '采购订单';

CREATE TABLE pur_order_line (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  po_id        BIGINT        NOT NULL,
  line_no      INT           NOT NULL,
  material_id  BIGINT        NOT NULL,
  qty          DECIMAL(18,3) NOT NULL,
  unit_price   DECIMAL(18,4) NOT NULL COMMENT '不含税单价',
  received_qty DECIMAL(18,3) NOT NULL DEFAULT 0 COMMENT '已到货数量(含检验不合格)',
  INDEX idx_pol_po (po_id),
  INDEX idx_pol_material (material_id)
) COMMENT '采购订单行';

CREATE TABLE pur_receipt (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  gr_no        VARCHAR(30) NOT NULL UNIQUE COMMENT '到货单号',
  po_id        BIGINT      NOT NULL,
  supplier_id  BIGINT      NOT NULL,
  receipt_date DATE        NOT NULL,
  status       VARCHAR(20) NOT NULL COMMENT 'PENDING_IQC待检/COMPLETED已入库/INVOICED已收票',
  created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_gr_po (po_id)
) COMMENT '采购到货单';

CREATE TABLE pur_receipt_line (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  gr_id        BIGINT        NOT NULL,
  po_line_id   BIGINT        NOT NULL,
  material_id  BIGINT        NOT NULL,
  qty          DECIMAL(18,3) NOT NULL COMMENT '到货数量',
  accepted_qty DECIMAL(18,3) NOT NULL DEFAULT 0 COMMENT '检验合格入库数量',
  rejected_qty DECIMAL(18,3) NOT NULL DEFAULT 0 COMMENT '检验不合格退货数量',
  unit_price   DECIMAL(18,4) NOT NULL,
  inspected    TINYINT(1)    NOT NULL DEFAULT 0,
  INDEX idx_grl_gr (gr_id)
) COMMENT '采购到货单行';

CREATE TABLE pur_invoice (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  invoice_no   VARCHAR(30)   NOT NULL UNIQUE COMMENT '供应商发票号',
  supplier_id  BIGINT        NOT NULL,
  gr_id        BIGINT        NOT NULL UNIQUE,
  invoice_date DATE          NOT NULL,
  due_date     DATE          NOT NULL,
  amount       DECIMAL(18,2) NOT NULL COMMENT '不含税金额（按合格入库数量）',
  tax_amount   DECIMAL(18,2) NOT NULL,
  total        DECIMAL(18,2) NOT NULL COMMENT '价税合计（应付金额）',
  paid         DECIMAL(18,2) NOT NULL DEFAULT 0,
  status       VARCHAR(20)   NOT NULL COMMENT 'OPEN/PARTIAL/PAID',
  INDEX idx_pi_supplier (supplier_id),
  INDEX idx_pi_status (status)
) COMMENT '采购发票（应付账款）';

CREATE TABLE pur_payment (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  payment_no   VARCHAR(30)   NOT NULL UNIQUE,
  supplier_id  BIGINT        NOT NULL,
  invoice_id   BIGINT        NOT NULL,
  payment_date DATE          NOT NULL,
  amount       DECIMAL(18,2) NOT NULL,
  INDEX idx_pp_invoice (invoice_id)
) COMMENT '采购付款';

-- ========== 库存 ==========
CREATE TABLE inv_stock (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  warehouse_id BIGINT        NOT NULL,
  item_type    VARCHAR(10)   NOT NULL COMMENT 'MATERIAL原材料/PRODUCT成品',
  item_id      BIGINT        NOT NULL,
  qty          DECIMAL(18,3) NOT NULL DEFAULT 0 COMMENT '现存量',
  avg_cost     DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '移动加权平均单位成本',
  UNIQUE KEY uk_stock (warehouse_id, item_type, item_id)
) COMMENT '库存现存量';

CREATE TABLE inv_txn (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  txn_date     DATE          NOT NULL COMMENT '业务日期',
  txn_type     VARCHAR(20)   NOT NULL COMMENT 'PO_RECEIPT采购入库/WO_ISSUE生产领料/WO_COMPLETE完工入库/SO_DELIVERY销售出库/ADJUST盘点调整',
  warehouse_id BIGINT        NOT NULL,
  item_type    VARCHAR(10)   NOT NULL,
  item_id      BIGINT        NOT NULL,
  qty          DECIMAL(18,3) NOT NULL COMMENT '数量，入库为正、出库为负',
  unit_cost    DECIMAL(18,4) NOT NULL,
  amount       DECIMAL(18,2) NOT NULL COMMENT '金额，与数量同号',
  ref_type     VARCHAR(20)   COMMENT '来源单据类型',
  ref_no       VARCHAR(30)   COMMENT '来源单据号',
  created_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_txn_item (item_type, item_id),
  INDEX idx_txn_date (txn_date)
) COMMENT '库存流水';

-- ========== 生产 MES ==========
CREATE TABLE mes_work_order (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  wo_no           VARCHAR(30)   NOT NULL UNIQUE COMMENT '生产工单号',
  product_id      BIGINT        NOT NULL,
  line_code       VARCHAR(20)   NOT NULL COMMENT '产线：L1罩极线/L2贯流线/L3无刷线/L4步进线/L5伺服线/L6减速线',
  planned_qty     DECIMAL(18,3) NOT NULL,
  good_qty        DECIMAL(18,3) NOT NULL DEFAULT 0 COMMENT '累计良品数',
  scrap_qty       DECIMAL(18,3) NOT NULL DEFAULT 0 COMMENT '累计报废数',
  status          VARCHAR(20)   NOT NULL COMMENT 'PLANNED计划/RELEASED已下达/IN_PROGRESS生产中/COMPLETED已完工',
  plan_start      DATE          NOT NULL,
  plan_end        DATE          NOT NULL,
  actual_start    DATE,
  actual_end      DATE,
  material_cost   DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '累计耗用材料成本',
  conversion_cost DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '累计加工费',
  created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_wo_status (status),
  INDEX idx_wo_product (product_id)
) COMMENT '生产工单';

CREATE TABLE mes_wo_report (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  wo_id       BIGINT        NOT NULL,
  report_date DATE          NOT NULL,
  good_qty    DECIMAL(18,3) NOT NULL COMMENT '本次良品',
  scrap_qty   DECIMAL(18,3) NOT NULL COMMENT '本次报废',
  defect_code VARCHAR(30)   COMMENT '主要不良代码：WINDING绕线不良/NOISE异响/CURRENT电流超标/SPEED转速偏差/ENCODER编码器异常/APPEARANCE外观',
  operator    VARCHAR(50),
  INDEX idx_wr_wo (wo_id),
  INDEX idx_wr_date (report_date)
) COMMENT '生产报工';

-- ========== 质量 QMS ==========
CREATE TABLE qms_iqc (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  iqc_no       VARCHAR(30)   NOT NULL UNIQUE COMMENT '来料检验单号',
  gr_id        BIGINT        NOT NULL,
  gr_line_id   BIGINT        NOT NULL UNIQUE,
  material_id  BIGINT        NOT NULL,
  supplier_id  BIGINT        NOT NULL,
  inspect_date DATE          NOT NULL,
  lot_qty      DECIMAL(18,3) NOT NULL COMMENT '批量',
  sample_qty   INT           NOT NULL COMMENT '抽样数',
  defect_qty   INT           NOT NULL COMMENT '不良数',
  result       VARCHAR(10)   NOT NULL COMMENT 'PASS合格/FAIL判退',
  defect_desc  VARCHAR(200),
  inspector    VARCHAR(50),
  INDEX idx_iqc_supplier (supplier_id),
  INDEX idx_iqc_date (inspect_date)
) COMMENT '来料检验 IQC';

CREATE TABLE qms_complaint (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  complaint_no      VARCHAR(30)   NOT NULL UNIQUE COMMENT '客诉单号',
  customer_id       BIGINT        NOT NULL,
  product_id        BIGINT        NOT NULL,
  delivery_id       BIGINT        COMMENT '关联发货单',
  received_date     DATE          NOT NULL,
  defect_type       VARCHAR(30)   NOT NULL COMMENT '不良类型：NOISE异响/NO_START不启动/OVERHEAT过热/SPEED转速异常/ENCODER编码器故障/APPEARANCE外观',
  qty_affected      DECIMAL(18,3) NOT NULL COMMENT '涉及数量',
  severity          VARCHAR(10)   NOT NULL COMMENT 'LOW/MEDIUM/HIGH',
  status            VARCHAR(20)   NOT NULL COMMENT 'OPEN待处理/ANALYZING分析中(8D)/CLOSED已关闭',
  root_cause        VARCHAR(1000) COMMENT '根本原因（8D D4）',
  corrective_action VARCHAR(1000) COMMENT '纠正措施（8D D5-D7）',
  closed_date       DATE,
  owner             VARCHAR(50),
  INDEX idx_cc_customer (customer_id),
  INDEX idx_cc_status (status)
) COMMENT '客户投诉（8D）';

-- ========== 财务 ==========
CREATE TABLE fin_expense (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  expense_no   VARCHAR(30)   NOT NULL UNIQUE COMMENT '报销单号',
  expense_date DATE          NOT NULL,
  dept         VARCHAR(30)   NOT NULL COMMENT '部门：销售部/采购部/生产部/质量部/研发中心/财务部/行政部',
  category     VARCHAR(30)   NOT NULL COMMENT '费用类别：差旅费/业务招待费/办公费/运输费/展会费/研发材料费/水电费/维修费',
  amount       DECIMAL(18,2) NOT NULL,
  employee     VARCHAR(50)   NOT NULL,
  description  VARCHAR(300),
  status       VARCHAR(20)   NOT NULL COMMENT 'SUBMITTED已提交/APPROVED已审批/REJECTED已驳回',
  approved_by  VARCHAR(50),
  created_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_exp_date (expense_date),
  INDEX idx_exp_dept (dept)
) COMMENT '费用报销';

INSERT INTO md_warehouse (code, name, type) VALUES ('WH-RAW', '原材料仓', 'RAW'), ('WH-FG', '成品仓', 'FG');
