-- 多仓库与调拨 / 采购价差 / 多级 BOM 与替代料

INSERT INTO gl_account (code, name, category, direction) VALUES
 ('1402', '在途物资（调拨在途）', 'ASSET', 'DEBIT'),
 ('1411', '自制半成品', 'ASSET', 'DEBIT');

-- ========== 多仓库 ==========
ALTER TABLE md_warehouse
  ADD COLUMN region       VARCHAR(20) COMMENT '前置仓服务区域：华东 / EXPORT(外销客户)',
  ADD COLUMN loc_prefix   VARCHAR(5)  NOT NULL DEFAULT '' COMMENT '库位编码前缀（库位编码全局唯一）',
  ADD COLUMN transit_days INT         NOT NULL DEFAULT 0 COMMENT '从总仓调拨的在途天数';

INSERT INTO md_warehouse (code, name, type, region, loc_prefix, transit_days) VALUES
 ('WH-SEMI',  '半成品仓',           'SEMI', NULL,     'S-',  0),
 ('WH-FG-EC', '华东前置仓（苏州）', 'DC',   '华东',   'EC-', 2),
 ('WH-FG-HK', '香港出口仓',         'DC',   'EXPORT', 'HK-', 3);

-- 批次按仓库管理：调拨时批次号随货转移，追溯链不断
ALTER TABLE inv_lot ADD COLUMN warehouse_id BIGINT NOT NULL DEFAULT 0 AFTER id;
UPDATE inv_lot l SET warehouse_id = (SELECT w.id FROM md_warehouse w WHERE w.code = IF(l.item_type = 'PRODUCT', 'WH-FG', 'WH-RAW'));
ALTER TABLE inv_lot DROP INDEX idx_lot_item, ADD INDEX idx_lot_item (warehouse_id, item_type, item_id, received_date);

CREATE TABLE inv_transfer (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  transfer_no       VARCHAR(30) NOT NULL UNIQUE COMMENT '调拨单号',
  from_warehouse_id BIGINT      NOT NULL,
  to_warehouse_id   BIGINT      NOT NULL,
  status            VARCHAR(20) NOT NULL COMMENT 'CREATED待发出/SHIPPED在途/RECEIVED已到货/CANCELLED已取消',
  create_date       DATE        NOT NULL,
  ship_date         DATE,
  expected_date     DATE,
  receive_date      DATE,
  created_by        VARCHAR(50),
  remark            VARCHAR(200),
  INDEX idx_tr_status (status),
  INDEX idx_tr_date (create_date)
) COMMENT '库存调拨单';

CREATE TABLE inv_transfer_line (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  transfer_id BIGINT        NOT NULL,
  item_type   VARCHAR(10)   NOT NULL,
  item_id     BIGINT        NOT NULL,
  qty         DECIMAL(18,3) NOT NULL,
  unit_cost   DECIMAL(18,4) NOT NULL DEFAULT 0 COMMENT '发出时调出仓移动平均成本',
  amount      DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '在途金额',
  INDEX idx_trl_transfer (transfer_id)
) COMMENT '调拨单行';

ALTER TABLE sal_delivery ADD COLUMN warehouse_id BIGINT COMMENT '发货仓库' AFTER customer_id;

-- ========== 多级 BOM ==========
ALTER TABLE md_material
  ADD COLUMN supply_type     VARCHAR(10)   NOT NULL DEFAULT 'BUY' COMMENT 'BUY外购/MAKE自制半成品',
  ADD COLUMN line_code       VARCHAR(20)   COMMENT '自制件生产线',
  ADD COLUMN conversion_cost DECIMAL(18,4) COMMENT '自制件单位加工费',
  ADD COLUMN lot_size        INT           COMMENT '自制件经济批量';

CREATE TABLE md_material_bom (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  parent_material_id BIGINT        NOT NULL COMMENT '自制半成品',
  child_material_id  BIGINT        NOT NULL COMMENT '下层物料（可以还是半成品）',
  qty_per            DECIMAL(18,4) NOT NULL,
  UNIQUE KEY uk_mbom (parent_material_id, child_material_id)
) COMMENT '半成品 BOM（与 md_bom 共同构成多级 BOM）';

CREATE TABLE md_material_substitute (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  material_id   BIGINT        NOT NULL COMMENT '主料',
  substitute_id BIGINT        NOT NULL COMMENT '替代料',
  ratio         DECIMAL(10,4) NOT NULL DEFAULT 1 COMMENT '替代比例：1 单位主料 = ratio 单位替代料',
  priority      INT           NOT NULL DEFAULT 1 COMMENT '多个替代料时按优先级使用',
  valid_from    DATE,
  valid_to      DATE,
  reason        VARCHAR(200),
  UNIQUE KEY uk_sub (material_id, substitute_id)
) COMMENT '替代料（主料不足时按优先级替代）';

ALTER TABLE mes_work_order
  MODIFY product_id BIGINT NULL,
  ADD COLUMN item_type    VARCHAR(10) NOT NULL DEFAULT 'PRODUCT' COMMENT 'PRODUCT成品工单/MATERIAL半成品工单' AFTER wo_no,
  ADD COLUMN material_id  BIGINT      COMMENT '半成品工单产出的物料' AFTER product_id,
  ADD COLUMN parent_wo_id BIGINT      COMMENT '组件工单对应的上级工单';

CREATE TABLE mes_wo_substitution (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  wo_id         BIGINT        NOT NULL,
  material_id   BIGINT        NOT NULL COMMENT '主料',
  substitute_id BIGINT        NOT NULL COMMENT '实际使用的替代料',
  primary_qty   DECIMAL(18,3) NOT NULL COMMENT '被替代的主料数量',
  substitute_qty DECIMAL(18,3) NOT NULL COMMENT '耗用替代料数量',
  txn_date      DATE          NOT NULL,
  INDEX idx_ws_wo (wo_id),
  INDEX idx_ws_date (txn_date)
) COMMENT '工单替代料使用记录';

INSERT INTO mes_line (line_code, name, series, daily_capacity) VALUES
 ('L7', '伺服组件线（定子嵌线/转子装配）', 'SEMI', 700),
 ('L8', '冲压叠片线', 'SEMI', 1500);

ALTER TABLE aps_plan_item
  ADD COLUMN bom_level    INT          NOT NULL DEFAULT 0 COMMENT 'BOM 层级：0 成品，1+ 半成品',
  ADD COLUMN substitution VARCHAR(300) COMMENT '齐套时使用的替代料';

-- ========== 采购价差 ==========
ALTER TABLE pur_invoice
  DROP INDEX gr_id,
  ADD INDEX idx_pi_gr (gr_id),
  MODIFY status VARCHAR(20) NOT NULL COMMENT 'PENDING_APPROVAL价差待审批/OPEN/PARTIAL/PAID/REJECTED',
  ADD COLUMN estimate_amount DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '暂估金额 = 订单价 × 合格数量',
  ADD COLUMN variance        DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '价差 = 发票金额 - 暂估金额',
  ADD COLUMN variance_stock  DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '价差计入存货（批次仍在库部分）',
  ADD COLUMN variance_cogs   DECIMAL(18,2) NOT NULL DEFAULT 0 COMMENT '价差计入主营业务成本（已领用部分）',
  ADD COLUMN approved_by     VARCHAR(50),
  ADD COLUMN approved_date   DATE;

CREATE TABLE pur_invoice_line (
  id              BIGINT AUTO_INCREMENT PRIMARY KEY,
  invoice_id      BIGINT        NOT NULL,
  gr_line_id      BIGINT        NOT NULL,
  material_id     BIGINT        NOT NULL,
  qty             DECIMAL(18,3) NOT NULL COMMENT '合格入库数量',
  po_price        DECIMAL(18,4) NOT NULL,
  invoice_price   DECIMAL(18,4) NOT NULL,
  estimate_amount DECIMAL(18,2) NOT NULL,
  amount          DECIMAL(18,2) NOT NULL,
  variance        DECIMAL(18,2) NOT NULL,
  stock_ratio     DECIMAL(8,4)  COMMENT '记账时该批次仍在库比例',
  variance_stock  DECIMAL(18,2) NOT NULL DEFAULT 0,
  variance_cogs   DECIMAL(18,2) NOT NULL DEFAULT 0,
  INDEX idx_pil_invoice (invoice_id)
) COMMENT '采购发票行（三单匹配：订单价 vs 发票价）';

-- ========== 视图 ==========
DROP VIEW v_material;
CREATE VIEW v_material AS
SELECT m.*,
       COALESCE((SELECT p.price FROM md_material_price p WHERE p.material_id = m.id ORDER BY p.price_month DESC LIMIT 1),
                NULL) AS latest_price,
       (SELECT COUNT(*) FROM md_material_substitute s WHERE s.material_id = m.id) AS substitute_count
FROM md_material m;

DROP VIEW v_bom;
CREATE VIEW v_bom AS
SELECT b.id, b.product_id, p.code AS product_code, p.name AS product_name,
       b.material_id, m.code AS material_code, m.name AS material_name, m.uom, m.supply_type, b.qty_per
FROM md_bom b JOIN md_product p ON p.id = b.product_id JOIN md_material m ON m.id = b.material_id;

CREATE VIEW v_material_bom AS
SELECT b.id, b.parent_material_id, pm.code AS parent_code, pm.name AS parent_name, pm.line_code,
       b.child_material_id, cm.code AS child_code, cm.name AS child_name, cm.uom, cm.supply_type, b.qty_per
FROM md_material_bom b JOIN md_material pm ON pm.id = b.parent_material_id JOIN md_material cm ON cm.id = b.child_material_id;

CREATE VIEW v_substitute AS
SELECT s.*, m.code AS material_code, m.name AS material_name, m.uom, sm.code AS substitute_code, sm.name AS substitute_name,
       (SELECT COALESCE(SUM(st.qty), 0) FROM inv_stock st WHERE st.item_type = 'MATERIAL' AND st.item_id = s.substitute_id) AS substitute_stock,
       (SELECT COALESCE(SUM(u.substitute_qty), 0) FROM mes_wo_substitution u WHERE u.material_id = s.material_id AND u.substitute_id = s.substitute_id) AS used_qty
FROM md_material_substitute s JOIN md_material m ON m.id = s.material_id JOIN md_material sm ON sm.id = s.substitute_id;

CREATE VIEW v_substitution AS
SELECT u.*, w.wo_no, COALESCE(p.code, wm.code) AS product_code, m.code AS material_code, m.name AS material_name,
       sm.code AS substitute_code, sm.name AS substitute_name
FROM mes_wo_substitution u JOIN mes_work_order w ON w.id = u.wo_id
LEFT JOIN md_product p ON p.id = w.product_id LEFT JOIN md_material wm ON wm.id = w.material_id
JOIN md_material m ON m.id = u.material_id JOIN md_material sm ON sm.id = u.substitute_id;

DROP VIEW v_work_order;
CREATE VIEW v_work_order AS
SELECT w.*, COALESCE(p.code, m.code) AS product_code, COALESCE(p.name, m.name) AS product_name,
       COALESCE(p.series, 'SEMI') AS series, pw.wo_no AS parent_wo_no,
       CASE WHEN (w.good_qty + w.scrap_qty) > 0 THEN ROUND(w.good_qty / (w.good_qty + w.scrap_qty), 4) END AS yield_rate
FROM mes_work_order w LEFT JOIN md_product p ON p.id = w.product_id LEFT JOIN md_material m ON m.id = w.material_id
LEFT JOIN mes_work_order pw ON pw.id = w.parent_wo_id;

DROP VIEW v_wo_report;
CREATE VIEW v_wo_report AS
SELECT r.*, w.wo_no, w.line_code, w.item_type, COALESCE(p.code, m.code) AS product_code, COALESCE(p.name, m.name) AS product_name
FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id
LEFT JOIN md_product p ON p.id = w.product_id LEFT JOIN md_material m ON m.id = w.material_id;

DROP VIEW v_stock;
CREATE VIEW v_stock AS
SELECT st.id, st.warehouse_id, w.code AS warehouse_code, w.name AS warehouse_name, st.item_type, st.item_id,
       COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name,
       COALESCE(p.series, m.category) AS item_group,
       st.qty, st.avg_cost, ROUND(st.qty * st.avg_cost, 2) AS stock_value
FROM inv_stock st JOIN md_warehouse w ON w.id = st.warehouse_id
LEFT JOIN md_product p ON st.item_type = 'PRODUCT' AND p.id = st.item_id
LEFT JOIN md_material m ON st.item_type = 'MATERIAL' AND m.id = st.item_id;

CREATE VIEW v_warehouse AS
SELECT w.*,
       (SELECT COUNT(*) FROM inv_stock s WHERE s.warehouse_id = w.id AND s.qty > 0) AS sku_count,
       (SELECT COALESCE(SUM(s.qty), 0) FROM inv_stock s WHERE s.warehouse_id = w.id) AS total_qty,
       (SELECT COALESCE(ROUND(SUM(s.qty * s.avg_cost), 2), 0) FROM inv_stock s WHERE s.warehouse_id = w.id) AS stock_value,
       (SELECT COALESCE(SUM(l.amount), 0) FROM inv_transfer t JOIN inv_transfer_line l ON l.transfer_id = t.id
         WHERE t.to_warehouse_id = w.id AND t.status = 'SHIPPED') AS in_transit_value
FROM md_warehouse w;

CREATE VIEW v_transfer AS
SELECT t.*, fw.name AS from_warehouse, tw.name AS to_warehouse,
       (SELECT COUNT(*) FROM inv_transfer_line l WHERE l.transfer_id = t.id) AS line_count,
       (SELECT COALESCE(SUM(l.qty), 0) FROM inv_transfer_line l WHERE l.transfer_id = t.id) AS total_qty,
       (SELECT COALESCE(SUM(l.amount), 0) FROM inv_transfer_line l WHERE l.transfer_id = t.id) AS amount
FROM inv_transfer t JOIN md_warehouse fw ON fw.id = t.from_warehouse_id JOIN md_warehouse tw ON tw.id = t.to_warehouse_id;

CREATE VIEW v_transfer_line AS
SELECT l.*, COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name
FROM inv_transfer_line l
LEFT JOIN md_product p ON l.item_type = 'PRODUCT' AND p.id = l.item_id
LEFT JOIN md_material m ON l.item_type = 'MATERIAL' AND m.id = l.item_id;

DROP VIEW v_lot;
CREATE VIEW v_lot AS
SELECT l.*, w.name AS warehouse_name, COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name, s.name AS supplier_name,
       (SELECT q.result FROM qms_iqc q JOIN pur_receipt g ON g.id = q.gr_id
         WHERE g.gr_no = l.source_no AND q.material_id = l.item_id LIMIT 1) AS iqc_result
FROM inv_lot l
JOIN md_warehouse w ON w.id = l.warehouse_id
LEFT JOIN md_product p ON l.item_type = 'PRODUCT' AND p.id = l.item_id
LEFT JOIN md_material m ON l.item_type = 'MATERIAL' AND m.id = l.item_id
LEFT JOIN md_supplier s ON s.id = l.supplier_id;

DROP VIEW v_delivery;
CREATE VIEW v_delivery AS
SELECT d.id, d.delivery_no, d.order_id, o.order_no, d.customer_id, c.name AS customer_name,
       d.warehouse_id, w.name AS warehouse_name,
       d.delivery_date, d.status, o.currency,
       ROUND(SUM(dl.qty * dl.unit_price), 2) AS amount,
       ROUND(SUM(dl.qty * dl.unit_price) * o.fx_rate, 2) AS amount_cny,
       ROUND(SUM(dl.qty * dl.unit_cost), 2) AS cost_cny
FROM sal_delivery d JOIN sal_order o ON o.id = d.order_id JOIN md_customer c ON c.id = d.customer_id
LEFT JOIN md_warehouse w ON w.id = d.warehouse_id
JOIN sal_delivery_line dl ON dl.delivery_id = d.id
GROUP BY d.id;

DROP VIEW v_purchase_invoice;
CREATE VIEW v_purchase_invoice AS
SELECT i.*, s.name AS supplier_name, (i.total - i.paid) AS outstanding,
       CASE WHEN i.estimate_amount <> 0 THEN ROUND(i.variance / i.estimate_amount, 4) END AS variance_rate,
       CASE WHEN i.status IN ('OPEN','PARTIAL') AND CURDATE() > i.due_date THEN DATEDIFF(CURDATE(), i.due_date) ELSE 0 END AS overdue_days
FROM pur_invoice i JOIN md_supplier s ON s.id = i.supplier_id;

CREATE VIEW v_price_variance AS
SELECT l.id, i.id AS invoice_id, i.invoice_no, i.invoice_date, DATE_FORMAT(i.invoice_date, '%Y-%m') AS month, i.status,
       i.supplier_id, s.name AS supplier_name, l.material_id, m.code AS material_code, m.name AS material_name, m.category,
       l.qty, l.po_price, l.invoice_price, l.estimate_amount, l.amount, l.variance,
       CASE WHEN l.estimate_amount <> 0 THEN ROUND(l.variance / l.estimate_amount, 4) END AS variance_rate,
       l.stock_ratio, l.variance_stock, l.variance_cogs
FROM pur_invoice_line l JOIN pur_invoice i ON i.id = l.invoice_id
JOIN md_supplier s ON s.id = i.supplier_id JOIN md_material m ON m.id = l.material_id
WHERE l.variance <> 0;
