-- 第四批：固定资产 / 资金与预算 / 合同管理 / 关务与出口退税（增值税月结）

-- ========== 科目表修正与补充 ==========
-- 按企业会计准则，6603 为财务费用；研发费用使用 6604（报表单列）
UPDATE gl_account SET name = '财务费用' WHERE code = '6603';
INSERT INTO gl_account (code, name, category, direction) VALUES
 ('6604', '研发费用', 'EXPENSE', 'DEBIT'),
 ('6403', '税金及附加', 'EXPENSE', 'DEBIT'),
 ('6115', '资产处置收益', 'REVENUE', 'CREDIT'),
 ('1601', '固定资产', 'ASSET', 'DEBIT'),
 ('1602', '累计折旧', 'ASSET', 'CREDIT'),
 ('1606', '固定资产清理', 'ASSET', 'DEBIT'),
 ('1221', '其他应收款-应收出口退税', 'ASSET', 'DEBIT'),
 ('222104', '应交税费-应交增值税(出口退税)', 'LIABILITY', 'CREDIT'),
 ('222105', '应交税费-未交增值税', 'LIABILITY', 'CREDIT'),
 ('222106', '应交税费-应交增值税(出口抵减内销产品应纳税额)', 'LIABILITY', 'CREDIT'),
 ('222109', '应交税费-应交城建税及教育费附加', 'LIABILITY', 'CREDIT');

-- ========== 固定资产 ==========
CREATE TABLE fa_category (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  code               VARCHAR(20)  NOT NULL UNIQUE,
  name               VARCHAR(50)  NOT NULL,
  useful_life_months INT          NOT NULL,
  residual_rate      DECIMAL(5,4) NOT NULL,
  method             VARCHAR(4)   NOT NULL COMMENT 'SL 年限平均法 / DDB 双倍余额递减法'
) COMMENT '固定资产类别';

CREATE TABLE fa_asset (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  asset_no           VARCHAR(30)   NOT NULL UNIQUE,
  name               VARCHAR(100)  NOT NULL,
  category_id        BIGINT        NOT NULL,
  dept_id            BIGINT        NOT NULL COMMENT '使用部门（决定折旧费用科目）',
  equipment_id       BIGINT        COMMENT '关联设备台账',
  in_service_date    DATE          NOT NULL COMMENT '达到预定可使用状态日期（次月起计提）',
  original_cost      DECIMAL(18,2) NOT NULL,
  residual_rate      DECIMAL(5,4)  NOT NULL,
  useful_life_months INT           NOT NULL,
  method             VARCHAR(4)    NOT NULL,
  accumulated_dep    DECIMAL(18,2) NOT NULL DEFAULT 0,
  last_dep_period    CHAR(7)       COMMENT '最近计提期间',
  status             VARCHAR(10)   NOT NULL COMMENT 'IN_USE在用/DISPOSED已处置',
  disposal_date      DATE,
  disposal_proceeds  DECIMAL(18,2),
  INDEX idx_fa_status (status)
) COMMENT '固定资产卡片';

CREATE TABLE fa_depreciation (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  asset_id         BIGINT        NOT NULL,
  period           CHAR(7)       NOT NULL,
  amount           DECIMAL(18,2) NOT NULL,
  accumulated_after DECIMAL(18,2) NOT NULL,
  expense_account  VARCHAR(10)   NOT NULL,
  UNIQUE KEY uk_dep (asset_id, period),
  INDEX idx_dep_period (period)
) COMMENT '折旧明细';

CREATE TABLE fa_change (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  asset_id    BIGINT        NOT NULL,
  change_type VARCHAR(10)   NOT NULL COMMENT 'OPENING期初/ACQUIRE新增/TRANSFER调拨/DISPOSE处置',
  change_date DATE          NOT NULL,
  amount      DECIMAL(18,2),
  remark      VARCHAR(200),
  wf_instance_id BIGINT,
  INDEX idx_fac_asset (asset_id)
) COMMENT '资产变动';

-- ========== 资金 ==========
CREATE TABLE fin_bank_account (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  code       VARCHAR(20) NOT NULL UNIQUE,
  bank_name  VARCHAR(100) NOT NULL,
  account_no VARCHAR(40) NOT NULL COMMENT '账号（展示时脱敏）',
  currency   VARCHAR(3)  NOT NULL,
  gl_account VARCHAR(10) NOT NULL
) COMMENT '银行账户';

CREATE TABLE fin_bank_statement (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  account_id      BIGINT        NOT NULL,
  txn_date        DATE          NOT NULL,
  amount          DECIMAL(18,2) NOT NULL COMMENT '收入为正、支出为负',
  counterparty    VARCHAR(100),
  memo            VARCHAR(200),
  bank_ref        VARCHAR(40),
  matched_entry_id BIGINT       COMMENT '对账匹配到的总账分录',
  INDEX idx_bs_date (account_id, txn_date)
) COMMENT '银行流水（网银导入）';

ALTER TABLE fin_expense ADD COLUMN paid_date DATE COMMENT '报销付款日期';

CREATE TABLE fin_budget (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  year      INT           NOT NULL,
  month     INT           NOT NULL,
  dept      VARCHAR(30)   NOT NULL COMMENT '与报销部门一致',
  category  VARCHAR(30)   NOT NULL COMMENT '费用类别',
  amount    DECIMAL(18,2) NOT NULL,
  UNIQUE KEY uk_budget (year, month, dept, category)
) COMMENT '费用预算（按月、部门、费用类别）';

-- ========== 合同 ==========
CREATE TABLE ct_contract (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  contract_no       VARCHAR(30)   NOT NULL UNIQUE,
  type              VARCHAR(12)   NOT NULL COMMENT 'SALES销售框架/PURCHASE采购框架/SERVICE服务/NDA保密',
  counterparty_type VARCHAR(10)   NOT NULL COMMENT 'CUSTOMER/SUPPLIER/OTHER',
  counterparty_id   BIGINT,
  counterparty_name VARCHAR(100)  NOT NULL,
  title             VARCHAR(200)  NOT NULL,
  amount            DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '合同金额（框架合同为年度承诺额）',
  currency          VARCHAR(3)    NOT NULL DEFAULT 'CNY',
  payment_terms     VARCHAR(100),
  sign_date         DATE,
  start_date        DATE          NOT NULL,
  end_date          DATE          NOT NULL,
  status            VARCHAR(12)   NOT NULL COMMENT 'DRAFT/APPROVING/ACTIVE/EXPIRED/TERMINATED/REJECTED',
  owner_emp_id      BIGINT        NOT NULL,
  auto_renew        TINYINT(1)    NOT NULL DEFAULT 0,
  wf_instance_id    BIGINT,
  remark            VARCHAR(500),
  INDEX idx_ct_status (status),
  INDEX idx_ct_cp (counterparty_type, counterparty_id)
) COMMENT '合同';

-- ========== 关务与出口退税 ==========
CREATE TABLE cu_declaration (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  decl_no       VARCHAR(30)   NOT NULL UNIQUE COMMENT '报关单号',
  delivery_id   BIGINT        NOT NULL,
  customer_id   BIGINT        NOT NULL,
  port          VARCHAR(30)   NOT NULL,
  trade_mode    VARCHAR(10)   NOT NULL COMMENT '0110 一般贸易',
  currency      VARCHAR(3)    NOT NULL,
  fob_amount    DECIMAL(18,2) NOT NULL,
  fx_rate       DECIMAL(12,6) NOT NULL,
  fob_cny       DECIMAL(18,2) NOT NULL,
  declare_date  DATE          NOT NULL,
  release_date  DATE,
  export_date   DATE          COMMENT '结关出口日期（退税按此期间申报）',
  status        VARCHAR(10)   NOT NULL COMMENT 'DECLARED/RELEASED/EXPORTED',
  refund_period CHAR(7)       COMMENT '已申报退税期间',
  UNIQUE KEY uk_decl_delivery (delivery_id),
  INDEX idx_decl_export (export_date)
) COMMENT '出口报关单';

CREATE TABLE cu_declaration_line (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  decl_id     BIGINT        NOT NULL,
  product_id  BIGINT        NOT NULL,
  hs_code     VARCHAR(12)   NOT NULL,
  qty         DECIMAL(18,3) NOT NULL,
  amount      DECIMAL(18,2) NOT NULL,
  refund_rate DECIMAL(5,4)  NOT NULL,
  INDEX idx_decl_line (decl_id)
) COMMENT '报关单商品';

CREATE TABLE tax_vat_return (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  period           CHAR(7)       NOT NULL UNIQUE,
  output_vat       DECIMAL(18,2) NOT NULL COMMENT '内销销项税额',
  input_vat        DECIMAL(18,2) NOT NULL COMMENT '进项税额',
  prev_credit      DECIMAL(18,2) NOT NULL COMMENT '上期留抵税额',
  export_fob       DECIMAL(18,2) NOT NULL COMMENT '当期出口 FOB 人民币（已结关报关单）',
  non_deductible   DECIMAL(18,2) NOT NULL COMMENT '免抵退税不得免征和抵扣税额',
  payable          DECIMAL(18,2) NOT NULL COMMENT '当期应纳税额（负数为留抵）',
  exempt_refund    DECIMAL(18,2) NOT NULL COMMENT '免抵退税额',
  refund           DECIMAL(18,2) NOT NULL COMMENT '应退税额',
  exempt_offset    DECIMAL(18,2) NOT NULL COMMENT '免抵税额',
  end_credit       DECIMAL(18,2) NOT NULL COMMENT '期末留抵税额',
  surtax           DECIMAL(18,2) NOT NULL COMMENT '城建税及教育费附加（以应纳税额 + 免抵税额为基数）',
  paid_date        DATE,
  refund_received  DATE
) COMMENT '增值税月度申报（生产企业免抵退）';

-- ========== 视图 ==========
CREATE VIEW v_fa_asset AS
SELECT a.*, c.name AS category_name, d.name AS dept_name, d.cost_type, q.code AS equipment_code,
       a.original_cost - a.accumulated_dep AS net_value,
       ROUND(a.accumulated_dep / NULLIF(a.original_cost * (1 - a.residual_rate), 0), 4) AS dep_progress
FROM fa_asset a JOIN fa_category c ON c.id = a.category_id JOIN org_dept d ON d.id = a.dept_id LEFT JOIN eam_equipment q ON q.id = a.equipment_id;

CREATE VIEW v_fa_depreciation AS
SELECT x.*, a.asset_no, a.name AS asset_name, c.name AS category_name, d.name AS dept_name
FROM fa_depreciation x JOIN fa_asset a ON a.id = x.asset_id JOIN fa_category c ON c.id = a.category_id JOIN org_dept d ON d.id = a.dept_id;

CREATE VIEW v_bank_statement AS
SELECT s.*, b.code AS account_code, b.bank_name, IF(s.matched_entry_id IS NULL, 0, 1) AS matched FROM fin_bank_statement s JOIN fin_bank_account b ON b.id = s.account_id;

CREATE VIEW v_budget_usage AS
SELECT b.year, b.month, b.dept, b.category, b.amount AS budget,
       (SELECT COALESCE(SUM(x.amount), 0) FROM fin_expense x WHERE x.dept = b.dept AND x.category = b.category AND x.status = 'APPROVED'
          AND YEAR(x.expense_date) = b.year AND MONTH(x.expense_date) = b.month) AS actual,
       (SELECT COALESCE(SUM(x.amount), 0) FROM fin_expense x WHERE x.dept = b.dept AND x.category = b.category AND x.status = 'SUBMITTED'
          AND YEAR(x.expense_date) = b.year AND MONTH(x.expense_date) = b.month) AS committed
FROM fin_budget b;

CREATE VIEW v_contract AS
SELECT c.*, e.name AS owner_name, DATEDIFF(c.end_date, CURDATE()) AS days_to_expiry,
       CASE WHEN c.type = 'SALES' AND c.counterparty_type = 'CUSTOMER' THEN
         (SELECT COALESCE(SUM(i.amount_cny), 0) FROM sal_invoice i WHERE i.customer_id = c.counterparty_id AND i.invoice_date BETWEEN c.start_date AND c.end_date)
       WHEN c.type = 'PURCHASE' AND c.counterparty_type = 'SUPPLIER' THEN
         (SELECT COALESCE(SUM(p.amount), 0) FROM pur_invoice p WHERE p.supplier_id = c.counterparty_id AND p.status IN ('OPEN','PARTIAL','PAID')
            AND p.invoice_date BETWEEN c.start_date AND c.end_date) END AS fulfilled_amount
FROM ct_contract c JOIN hr_employee e ON e.id = c.owner_emp_id;

CREATE VIEW v_declaration AS
SELECT d.*, c.name AS customer_name, c.country, dl.delivery_no,
       (SELECT ROUND(SUM(l.amount * d.fx_rate * l.refund_rate), 2) FROM cu_declaration_line l WHERE l.decl_id = d.id) AS refundable
FROM cu_declaration d JOIN md_customer c ON c.id = d.customer_id JOIN sal_delivery dl ON dl.id = d.delivery_id;
