-- SRM 供应商管理 / CRM 客户关系 / WMS 库位管理 / APS 高级排产 / GL 总账凭证

-- ========== SRM ==========
ALTER TABLE md_supplier ADD COLUMN qualification VARCHAR(20) NOT NULL DEFAULT 'QUALIFIED'
  COMMENT '准入状态：QUALIFIED合格/OBSERVATION观察/DISQUALIFIED淘汰' AFTER payment_terms_days;

CREATE TABLE srm_supplier_score (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  supplier_id    BIGINT       NOT NULL,
  score_month    DATE         NOT NULL COMMENT '考核月份(每月1日)',
  lots           INT          NOT NULL COMMENT '到货批次',
  pass_rate      DECIMAL(6,4) NOT NULL COMMENT 'IQC 批合格率',
  on_time_rate   DECIMAL(6,4) NOT NULL COMMENT '准时交付率（到货日 ≤ 预计到货日 + 2 天）',
  price_index    DECIMAL(8,4) NOT NULL COMMENT '价格指数 = 实际采购价 / 当月参考价',
  quality_score  DECIMAL(6,2) NOT NULL COMMENT '质量得分(满分50)',
  delivery_score DECIMAL(6,2) NOT NULL COMMENT '交付得分(满分30)',
  price_score    DECIMAL(6,2) NOT NULL COMMENT '价格得分(满分20)',
  total_score    DECIMAL(6,2) NOT NULL,
  grade          VARCHAR(2)   NOT NULL COMMENT 'A≥90 / B≥80 / C≥70 / D<70',
  UNIQUE KEY uk_score (supplier_id, score_month)
) COMMENT '供应商月度绩效';

CREATE TABLE srm_rfq (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  rfq_no              VARCHAR(30)   NOT NULL UNIQUE COMMENT '询价单号',
  material_id         BIGINT        NOT NULL,
  qty                 DECIMAL(18,3) NOT NULL,
  need_date           DATE          NOT NULL,
  status              VARCHAR(20)   NOT NULL COMMENT 'OPEN询价中/AWARDED已定标/CANCELLED已取消',
  created_by          VARCHAR(50),
  created_date        DATE          NOT NULL,
  awarded_supplier_id BIGINT,
  awarded_price       DECIMAL(18,4),
  award_reason        VARCHAR(300),
  po_id               BIGINT        COMMENT '定标后生成的采购单',
  INDEX idx_rfq_status (status)
) COMMENT '询价单';

CREATE TABLE srm_quote (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  rfq_id      BIGINT        NOT NULL,
  supplier_id BIGINT        NOT NULL,
  unit_price  DECIMAL(18,4) NOT NULL,
  lead_days   INT           NOT NULL COMMENT '承诺交期(天)',
  quote_date  DATE          NOT NULL,
  remark      VARCHAR(300),
  UNIQUE KEY uk_quote (rfq_id, supplier_id)
) COMMENT '供应商报价';

-- ========== CRM ==========
CREATE TABLE crm_contact (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT      NOT NULL,
  name        VARCHAR(50) NOT NULL,
  title       VARCHAR(50) COMMENT '职务',
  phone       VARCHAR(30),
  email       VARCHAR(100),
  is_primary  TINYINT(1)  NOT NULL DEFAULT 0,
  INDEX idx_contact_customer (customer_id)
) COMMENT '客户联系人';

CREATE TABLE crm_opportunity (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  opp_no         VARCHAR(30)   NOT NULL UNIQUE COMMENT '商机编号',
  customer_id    BIGINT        NOT NULL,
  title          VARCHAR(200)  NOT NULL,
  series         VARCHAR(20)   NOT NULL COMMENT '意向产品系列',
  stage          VARCHAR(20)   NOT NULL COMMENT 'LEAD线索/QUALIFIED已确认/PROPOSAL方案报价/NEGOTIATION商务谈判/WON赢单/LOST丢单',
  amount         DECIMAL(18,2) NOT NULL COMMENT '预计年采购额(人民币)',
  probability    INT           NOT NULL COMMENT '赢单概率%',
  expected_close DATE          NOT NULL,
  owner          VARCHAR(50)   NOT NULL,
  lost_reason    VARCHAR(300),
  created_date   DATE          NOT NULL,
  closed_date    DATE,
  INDEX idx_opp_stage (stage),
  INDEX idx_opp_customer (customer_id)
) COMMENT '销售商机';

CREATE TABLE crm_activity (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  opportunity_id BIGINT,
  customer_id    BIGINT       NOT NULL,
  activity_date  DATE         NOT NULL,
  type           VARCHAR(20)  NOT NULL COMMENT 'VISIT拜访/CALL电话/SAMPLE送样/MEETING技术交流/QUOTE报价',
  subject        VARCHAR(300) NOT NULL,
  owner          VARCHAR(50)  NOT NULL,
  INDEX idx_act_opp (opportunity_id),
  INDEX idx_act_date (activity_date)
) COMMENT '客户跟进记录';

-- ========== WMS ==========
CREATE TABLE wms_location (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  warehouse_id BIGINT      NOT NULL,
  code         VARCHAR(20) NOT NULL UNIQUE COMMENT '库位编码，如 R-MAG-01',
  zone         VARCHAR(20) NOT NULL COMMENT '库区：原材料按物料类别，成品按产品系列',
  enabled      TINYINT(1)  NOT NULL DEFAULT 1
) COMMENT '库位';

CREATE TABLE wms_bin_stock (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  location_id BIGINT        NOT NULL,
  item_type   VARCHAR(10)   NOT NULL,
  item_id     BIGINT        NOT NULL,
  qty         DECIMAL(18,3) NOT NULL DEFAULT 0,
  UNIQUE KEY uk_bin (location_id, item_type, item_id),
  INDEX idx_bin_item (item_type, item_id)
) COMMENT '库位库存（汇总后等于 inv_stock 现存量）';

CREATE TABLE wms_task (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_type        VARCHAR(10)   NOT NULL COMMENT 'PUTAWAY上架/PICK拣货/MOVE移库',
  item_type        VARCHAR(10)   NOT NULL,
  item_id          BIGINT        NOT NULL,
  from_location_id BIGINT,
  to_location_id   BIGINT,
  qty              DECIMAL(18,3) NOT NULL,
  ref_no           VARCHAR(30),
  task_date        DATE          NOT NULL,
  INDEX idx_task_date (task_date),
  INDEX idx_task_item (item_type, item_id)
) COMMENT '仓库作业记录';

-- ========== APS ==========
CREATE TABLE mes_line (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  line_code      VARCHAR(20) NOT NULL UNIQUE,
  name           VARCHAR(50) NOT NULL,
  series         VARCHAR(20) NOT NULL COMMENT '主要生产的产品系列',
  daily_capacity INT         NOT NULL COMMENT '日产能(台/天)'
) COMMENT '产线';

CREATE TABLE aps_plan (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_no       VARCHAR(30) NOT NULL UNIQUE,
  plan_date     DATE        NOT NULL COMMENT '排产基准日',
  created_by    VARCHAR(50),
  order_count   INT         NOT NULL,
  late_count    INT         NOT NULL,
  created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '排产方案';

CREATE TABLE aps_plan_item (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  plan_id       BIGINT        NOT NULL,
  wo_id         BIGINT        NOT NULL,
  line_code     VARCHAR(20)   NOT NULL,
  seq           INT           NOT NULL COMMENT '产线内顺序',
  qty           DECIMAL(18,3) NOT NULL COMMENT '待生产数量',
  start_date    DATE          NOT NULL,
  end_date      DATE          NOT NULL,
  due_date      DATE          NOT NULL,
  late_days     INT           NOT NULL,
  INDEX idx_api_plan (plan_id)
) COMMENT '排产结果';

-- ========== GL 总账 ==========
CREATE TABLE gl_account (
  code      VARCHAR(10) PRIMARY KEY COMMENT '科目编码',
  name      VARCHAR(50) NOT NULL,
  category  VARCHAR(20) NOT NULL COMMENT 'ASSET资产/LIABILITY负债/EQUITY权益/COST成本/REVENUE收入/EXPENSE费用',
  direction VARCHAR(6)  NOT NULL COMMENT '余额方向 DEBIT/CREDIT'
) COMMENT '会计科目';

CREATE TABLE gl_voucher (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  voucher_no   VARCHAR(30)  NOT NULL UNIQUE COMMENT '凭证号',
  voucher_date DATE         NOT NULL,
  source_type  VARCHAR(20)  NOT NULL COMMENT '来源：SALES_INVOICE/RECEIPT/DELIVERY/GOODS_RECEIPT/PURCHASE_INVOICE/PAYMENT/WORK_ORDER/EXPENSE/ADJUST/MANUAL',
  source_no    VARCHAR(30)  COMMENT '来源单据号',
  summary      VARCHAR(200) NOT NULL,
  created_by   VARCHAR(50),
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_voucher_date (voucher_date),
  INDEX idx_voucher_source (source_type, source_no)
) COMMENT '记账凭证';

CREATE TABLE gl_entry (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  voucher_id   BIGINT        NOT NULL,
  line_no      INT           NOT NULL,
  account_code VARCHAR(10)   NOT NULL,
  debit        DECIMAL(18,2) NOT NULL DEFAULT 0,
  credit       DECIMAL(18,2) NOT NULL DEFAULT 0,
  summary      VARCHAR(200),
  INDEX idx_entry_voucher (voucher_id),
  INDEX idx_entry_account (account_code)
) COMMENT '凭证分录';

INSERT INTO gl_account (code, name, category, direction) VALUES
 ('1002', '银行存款', 'ASSET', 'DEBIT'),
 ('1121', '应收票据', 'ASSET', 'DEBIT'),
 ('1122', '应收账款', 'ASSET', 'DEBIT'),
 ('1403', '原材料', 'ASSET', 'DEBIT'),
 ('1405', '库存商品', 'ASSET', 'DEBIT'),
 ('1901', '待处理财产损溢', 'ASSET', 'DEBIT'),
 ('2202', '应付账款', 'LIABILITY', 'CREDIT'),
 ('222101', '应交税费-进项税额', 'LIABILITY', 'CREDIT'),
 ('222102', '应交税费-销项税额', 'LIABILITY', 'CREDIT'),
 ('2241', '其他应付款', 'LIABILITY', 'CREDIT'),
 ('4001', '实收资本', 'EQUITY', 'CREDIT'),
 ('5001', '生产成本', 'COST', 'DEBIT'),
 ('5101', '制造费用', 'COST', 'DEBIT'),
 ('6001', '主营业务收入', 'REVENUE', 'CREDIT'),
 ('6401', '主营业务成本', 'EXPENSE', 'DEBIT'),
 ('6601', '销售费用', 'EXPENSE', 'DEBIT'),
 ('6602', '管理费用', 'EXPENSE', 'DEBIT'),
 ('6603', '研发费用', 'EXPENSE', 'DEBIT'),
 ('6711', '营业外支出', 'EXPENSE', 'DEBIT');

-- ========== 视图 ==========
CREATE VIEW v_supplier_score AS
SELECT sc.*, s.code AS supplier_code, s.name AS supplier_name, s.category, s.qualification
FROM srm_supplier_score sc JOIN md_supplier s ON s.id = sc.supplier_id;

CREATE VIEW v_rfq AS
SELECT r.*, m.code AS material_code, m.name AS material_name, m.uom, s.name AS awarded_supplier_name,
       (SELECT COUNT(*) FROM srm_quote q WHERE q.rfq_id = r.id) AS quote_count,
       (SELECT MIN(q.unit_price) FROM srm_quote q WHERE q.rfq_id = r.id) AS best_price
FROM srm_rfq r JOIN md_material m ON m.id = r.material_id LEFT JOIN md_supplier s ON s.id = r.awarded_supplier_id;

CREATE VIEW v_quote AS
SELECT q.*, s.code AS supplier_code, s.name AS supplier_name, s.qualification
FROM srm_quote q JOIN md_supplier s ON s.id = q.supplier_id;

CREATE VIEW v_opportunity AS
SELECT o.*, c.name AS customer_name, c.industry, ROUND(o.amount * o.probability / 100, 2) AS weighted_amount,
       (SELECT MAX(a.activity_date) FROM crm_activity a WHERE a.opportunity_id = o.id) AS last_activity
FROM crm_opportunity o JOIN md_customer c ON c.id = o.customer_id;

CREATE VIEW v_activity AS
SELECT a.*, c.name AS customer_name, o.opp_no, o.title AS opp_title
FROM crm_activity a JOIN md_customer c ON c.id = a.customer_id LEFT JOIN crm_opportunity o ON o.id = a.opportunity_id;

CREATE VIEW v_contact AS
SELECT ct.*, c.name AS customer_name, c.industry FROM crm_contact ct JOIN md_customer c ON c.id = ct.customer_id;

CREATE VIEW v_bin_stock AS
SELECT b.id, l.code AS location_code, l.zone, w.name AS warehouse_name, b.item_type, b.item_id,
       COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name, b.qty
FROM wms_bin_stock b JOIN wms_location l ON l.id = b.location_id JOIN md_warehouse w ON w.id = l.warehouse_id
LEFT JOIN md_product p ON b.item_type = 'PRODUCT' AND p.id = b.item_id
LEFT JOIN md_material m ON b.item_type = 'MATERIAL' AND m.id = b.item_id
WHERE b.qty <> 0;

CREATE VIEW v_wms_task AS
SELECT t.*, fl.code AS from_location, tl.code AS to_location,
       COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name
FROM wms_task t LEFT JOIN wms_location fl ON fl.id = t.from_location_id LEFT JOIN wms_location tl ON tl.id = t.to_location_id
LEFT JOIN md_product p ON t.item_type = 'PRODUCT' AND p.id = t.item_id
LEFT JOIN md_material m ON t.item_type = 'MATERIAL' AND m.id = t.item_id;

CREATE VIEW v_location AS
SELECT l.*, w.name AS warehouse_name,
       (SELECT COUNT(*) FROM wms_bin_stock b WHERE b.location_id = l.id AND b.qty > 0) AS sku_count,
       (SELECT COALESCE(SUM(b.qty), 0) FROM wms_bin_stock b WHERE b.location_id = l.id) AS total_qty
FROM wms_location l JOIN md_warehouse w ON w.id = l.warehouse_id;

CREATE VIEW v_voucher AS
SELECT v.*, SUM(e.debit) AS total_debit, SUM(e.credit) AS total_credit, COUNT(e.id) AS line_count
FROM gl_voucher v JOIN gl_entry e ON e.voucher_id = v.id GROUP BY v.id;

CREATE VIEW v_gl_entry AS
SELECT e.*, v.voucher_no, v.voucher_date, v.source_type, v.source_no, a.name AS account_name, a.category
FROM gl_entry e JOIN gl_voucher v ON v.id = e.voucher_id JOIN gl_account a ON a.code = e.account_code;

-- 产线与初始库位
INSERT INTO mes_line (line_code, name, series, daily_capacity) VALUES
 ('L1', '罩极电机线', 'SHADED_POLE', 9000), ('L2', '贯流风机线', 'CROSSFLOW', 2500), ('L3', '无刷电机线', 'BLDC', 1600),
 ('L4', '步进电机线', 'STEPPER', 1500), ('L5', '伺服电机线', 'SERVO', 260), ('L6', '减速电机线', 'GEAR', 1600);
