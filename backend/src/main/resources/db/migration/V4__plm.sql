-- PLM：BOM 版本、工程变更 ECN、技术文档、新产品开发项目(NPI)

ALTER TABLE md_product ADD COLUMN revision VARCHAR(5) NOT NULL DEFAULT 'A' COMMENT '当前生效 BOM 版本' AFTER lot_size;

CREATE TABLE plm_bom_revision (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  product_id     BIGINT      NOT NULL,
  revision       VARCHAR(5)  NOT NULL COMMENT '版本号 A/B/C…',
  status         VARCHAR(20) NOT NULL COMMENT 'RELEASED生效/OBSOLETE已作废',
  effective_date DATE        NOT NULL COMMENT '生效日期',
  obsolete_date  DATE        COMMENT '作废日期',
  ecn_id         BIGINT      COMMENT '由哪个 ECN 产生（初始版本为空）',
  created_at     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_rev (product_id, revision)
) COMMENT 'BOM 版本';

CREATE TABLE plm_bom_revision_line (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  revision_id BIGINT        NOT NULL,
  material_id BIGINT        NOT NULL,
  qty_per     DECIMAL(18,4) NOT NULL,
  INDEX idx_brl_rev (revision_id)
) COMMENT 'BOM 版本明细';

CREATE TABLE plm_ecn (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  ecn_no         VARCHAR(30)   NOT NULL UNIQUE COMMENT '工程变更单号',
  title          VARCHAR(200)  NOT NULL,
  reason         VARCHAR(20)   NOT NULL COMMENT '变更原因：COST_DOWN降本/QUALITY质量改进/CUSTOMER客户要求/SUPPLY供应变更/DESIGN设计优化',
  description    VARCHAR(2000) COMMENT '变更说明',
  status         VARCHAR(20)   NOT NULL COMMENT 'DRAFT草稿/REVIEW会签中/APPROVED已批准/RELEASED已发布/REJECTED已否决',
  requested_by   VARCHAR(50)   NOT NULL COMMENT '申请人',
  request_date   DATE          NOT NULL,
  effective_date DATE          COMMENT '发布生效日期',
  unit_cost_delta DECIMAL(18,4) COMMENT '发布时计算的单台材料成本变化(各产品平均)',
  annual_impact  DECIMAL(18,2) COMMENT '发布时估算的年度成本影响(按近12个月销量，负数=节约)',
  created_at     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_ecn_status (status)
) COMMENT '工程变更通知 ECN';

CREATE TABLE plm_ecn_line (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  ecn_id      BIGINT        NOT NULL,
  product_id  BIGINT        NOT NULL,
  action      VARCHAR(10)   NOT NULL COMMENT 'ADD新增物料/REMOVE删除物料/CHANGE修改用量',
  material_id BIGINT        NOT NULL,
  new_qty     DECIMAL(18,4) COMMENT '新用量（ADD/CHANGE）',
  INDEX idx_ecnl_ecn (ecn_id)
) COMMENT 'ECN 变更明细';

CREATE TABLE plm_ecn_review (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  ecn_id      BIGINT       NOT NULL,
  review_role VARCHAR(20)  NOT NULL COMMENT '会签角色：ENGINEER研发/PRODUCTION生产/QUALITY质量/PURCHASE采购',
  decision    VARCHAR(10)  NOT NULL COMMENT 'PENDING/APPROVE/REJECT',
  reviewer    VARCHAR(50),
  comment     VARCHAR(500),
  review_date DATE,
  UNIQUE KEY uk_ecn_role (ecn_id, review_role)
) COMMENT 'ECN 会签';

CREATE TABLE plm_document (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  doc_no      VARCHAR(30)  NOT NULL COMMENT '文档编号（各版本相同）',
  revision    INT          NOT NULL COMMENT '文档版本 1,2,3…',
  title       VARCHAR(200) NOT NULL,
  doc_type    VARCHAR(20)  NOT NULL COMMENT 'DATASHEET规格书/DRAWING图纸/SOP作业指导书/INSPECTION检验标准/TEST_REPORT测试报告',
  product_id  BIGINT       COMMENT '关联产品（可空）',
  series      VARCHAR(20)  COMMENT '关联产品系列（可空）',
  status      VARCHAR(20)  NOT NULL COMMENT 'CURRENT现行/SUPERSEDED已替代',
  file_name   VARCHAR(200) NOT NULL,
  file_path   VARCHAR(500) NOT NULL COMMENT '存储路径（相对存储根目录）',
  file_size   BIGINT       NOT NULL,
  content_type VARCHAR(100),
  uploaded_by VARCHAR(50),
  upload_date DATE         NOT NULL,
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_doc_rev (doc_no, revision),
  INDEX idx_doc_product (product_id)
) COMMENT '技术文档';

CREATE TABLE plm_project (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_no    VARCHAR(30)   NOT NULL UNIQUE COMMENT '项目编号',
  name          VARCHAR(200)  NOT NULL,
  series        VARCHAR(20)   NOT NULL COMMENT '目标产品系列',
  stage         VARCHAR(20)   NOT NULL COMMENT 'CONCEPT概念/DESIGN设计/PROTOTYPE样机/PILOT试产/MP量产',
  status        VARCHAR(20)   NOT NULL COMMENT 'ACTIVE进行中/ON_HOLD暂停/CLOSED已结项/KILLED已终止',
  owner         VARCHAR(50)   NOT NULL,
  target_customer VARCHAR(100) COMMENT '目标客户/市场',
  start_date    DATE          NOT NULL,
  target_mp_date DATE         NOT NULL COMMENT '计划量产日期',
  budget        DECIMAL(18,2) NOT NULL COMMENT '研发预算',
  spent         DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '已发生费用',
  created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
) COMMENT '新产品开发项目 NPI';

CREATE TABLE plm_project_gate (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT       NOT NULL,
  from_stage VARCHAR(20)  NOT NULL,
  to_stage   VARCHAR(20)  COMMENT '通过后进入的阶段',
  decision   VARCHAR(10)  NOT NULL COMMENT 'GO通过/HOLD暂停/KILL终止',
  comment    VARCHAR(500),
  reviewer   VARCHAR(50),
  gate_date  DATE         NOT NULL,
  INDEX idx_gate_project (project_id)
) COMMENT '阶段门评审记录';

-- ---------- 视图 ----------
CREATE VIEW v_ecn AS
SELECT e.*,
       (SELECT COUNT(DISTINCT l.product_id) FROM plm_ecn_line l WHERE l.ecn_id = e.id) AS product_count,
       (SELECT GROUP_CONCAT(DISTINCT p.series) FROM plm_ecn_line l JOIN md_product p ON p.id = l.product_id WHERE l.ecn_id = e.id) AS series,
       (SELECT COUNT(*) FROM plm_ecn_review r WHERE r.ecn_id = e.id AND r.decision = 'PENDING') AS pending_reviews
FROM plm_ecn e;

CREATE VIEW v_ecn_line AS
SELECT l.*, p.code AS product_code, p.name AS product_name, m.code AS material_code, m.name AS material_name, m.uom
FROM plm_ecn_line l JOIN md_product p ON p.id = l.product_id JOIN md_material m ON m.id = l.material_id;

CREATE VIEW v_bom_revision AS
SELECT r.*, p.code AS product_code, p.name AS product_name, p.series, e.ecn_no, e.title AS ecn_title,
       (SELECT COUNT(*) FROM plm_bom_revision_line l WHERE l.revision_id = r.id) AS line_count
FROM plm_bom_revision r JOIN md_product p ON p.id = r.product_id LEFT JOIN plm_ecn e ON e.id = r.ecn_id;

CREATE VIEW v_bom_revision_line AS
SELECT l.*, m.code AS material_code, m.name AS material_name, m.uom, m.category
FROM plm_bom_revision_line l JOIN md_material m ON m.id = l.material_id;

CREATE VIEW v_document AS
SELECT d.*, p.code AS product_code, p.name AS product_name
FROM plm_document d LEFT JOIN md_product p ON p.id = d.product_id;

CREATE VIEW v_project AS
SELECT pr.*, ROUND(pr.spent / NULLIF(pr.budget, 0), 4) AS budget_used,
       (SELECT COUNT(*) FROM plm_project_gate g WHERE g.project_id = pr.id) AS gate_count
FROM plm_project pr;
