-- 查询视图：列表页统一从视图读取（带名称、汇总字段），BI 与后续财务 Agent 也基于视图

CREATE VIEW v_material AS
SELECT m.*, (SELECT p.price FROM md_material_price p WHERE p.material_id = m.id
             ORDER BY p.price_month DESC LIMIT 1) AS latest_price
FROM md_material m;

CREATE VIEW v_bom AS
SELECT b.id, b.product_id, p.code AS product_code, p.name AS product_name,
       b.material_id, m.code AS material_code, m.name AS material_name, m.uom, b.qty_per
FROM md_bom b JOIN md_product p ON p.id = b.product_id JOIN md_material m ON m.id = b.material_id;

CREATE VIEW v_sales_order AS
SELECT o.id, o.order_no, o.customer_id, c.name AS customer_name, c.industry, c.is_domestic,
       o.order_date, o.required_date, o.currency, o.fx_rate, o.status, o.salesperson, o.remark,
       COALESCE(SUM(l.qty * l.unit_price), 0)                AS amount,
       ROUND(COALESCE(SUM(l.qty * l.unit_price), 0) * o.fx_rate, 2) AS amount_cny,
       COALESCE(SUM(l.qty), 0)                               AS total_qty,
       COALESCE(SUM(l.shipped_qty), 0)                       AS shipped_qty
FROM sal_order o JOIN md_customer c ON c.id = o.customer_id
LEFT JOIN sal_order_line l ON l.order_id = o.id
GROUP BY o.id;

CREATE VIEW v_sales_order_line AS
SELECT l.*, p.code AS product_code, p.name AS product_name, p.series,
       (l.qty - l.shipped_qty) AS open_qty, ROUND(l.qty * l.unit_price, 2) AS amount
FROM sal_order_line l JOIN md_product p ON p.id = l.product_id;

CREATE VIEW v_delivery AS
SELECT d.id, d.delivery_no, d.order_id, o.order_no, d.customer_id, c.name AS customer_name,
       d.delivery_date, d.status, o.currency,
       ROUND(SUM(dl.qty * dl.unit_price), 2) AS amount,
       ROUND(SUM(dl.qty * dl.unit_price) * o.fx_rate, 2) AS amount_cny,
       ROUND(SUM(dl.qty * dl.unit_cost), 2) AS cost_cny
FROM sal_delivery d JOIN sal_order o ON o.id = d.order_id JOIN md_customer c ON c.id = d.customer_id
JOIN sal_delivery_line dl ON dl.delivery_id = d.id
GROUP BY d.id;

CREATE VIEW v_sales_invoice AS
SELECT i.*, c.name AS customer_name, c.industry, c.region,
       (i.total_cny - i.received_cny) AS outstanding_cny,
       CASE WHEN i.status <> 'PAID' AND CURDATE() > i.due_date THEN DATEDIFF(CURDATE(), i.due_date) ELSE 0 END AS overdue_days
FROM sal_invoice i JOIN md_customer c ON c.id = i.customer_id;

CREATE VIEW v_receipt AS
SELECT r.*, c.name AS customer_name, i.invoice_no
FROM sal_receipt r JOIN md_customer c ON c.id = r.customer_id JOIN sal_invoice i ON i.id = r.invoice_id;

CREATE VIEW v_purchase_order AS
SELECT o.id, o.po_no, o.supplier_id, s.name AS supplier_name, o.order_date, o.expected_date, o.status, o.buyer,
       COALESCE(SUM(l.qty * l.unit_price), 0) AS amount
FROM pur_order o JOIN md_supplier s ON s.id = o.supplier_id
LEFT JOIN pur_order_line l ON l.po_id = o.id
GROUP BY o.id;

CREATE VIEW v_purchase_order_line AS
SELECT l.*, m.code AS material_code, m.name AS material_name, m.uom, (l.qty - l.received_qty) AS open_qty
FROM pur_order_line l JOIN md_material m ON m.id = l.material_id;

CREATE VIEW v_goods_receipt AS
SELECT g.id, g.gr_no, g.po_id, o.po_no, g.supplier_id, s.name AS supplier_name, g.receipt_date, g.status,
       ROUND(SUM(l.accepted_qty * l.unit_price), 2) AS accepted_amount,
       SUM(l.inspected = 0) AS pending_lines
FROM pur_receipt g JOIN pur_order o ON o.id = g.po_id JOIN md_supplier s ON s.id = g.supplier_id
JOIN pur_receipt_line l ON l.gr_id = g.id
GROUP BY g.id;

CREATE VIEW v_goods_receipt_line AS
SELECT l.*, m.code AS material_code, m.name AS material_name, m.uom
FROM pur_receipt_line l JOIN md_material m ON m.id = l.material_id;

CREATE VIEW v_purchase_invoice AS
SELECT i.*, s.name AS supplier_name, (i.total - i.paid) AS outstanding,
       CASE WHEN i.status <> 'PAID' AND CURDATE() > i.due_date THEN DATEDIFF(CURDATE(), i.due_date) ELSE 0 END AS overdue_days
FROM pur_invoice i JOIN md_supplier s ON s.id = i.supplier_id;

CREATE VIEW v_payment AS
SELECT p.*, s.name AS supplier_name, i.invoice_no
FROM pur_payment p JOIN md_supplier s ON s.id = p.supplier_id JOIN pur_invoice i ON i.id = p.invoice_id;

CREATE VIEW v_stock AS
SELECT st.id, st.warehouse_id, w.name AS warehouse_name, st.item_type, st.item_id,
       COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name,
       COALESCE(p.series, m.category) AS item_group,
       st.qty, st.avg_cost, ROUND(st.qty * st.avg_cost, 2) AS stock_value
FROM inv_stock st JOIN md_warehouse w ON w.id = st.warehouse_id
LEFT JOIN md_product p ON st.item_type = 'PRODUCT' AND p.id = st.item_id
LEFT JOIN md_material m ON st.item_type = 'MATERIAL' AND m.id = st.item_id;

CREATE VIEW v_inv_txn AS
SELECT t.*, w.name AS warehouse_name,
       COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name
FROM inv_txn t JOIN md_warehouse w ON w.id = t.warehouse_id
LEFT JOIN md_product p ON t.item_type = 'PRODUCT' AND p.id = t.item_id
LEFT JOIN md_material m ON t.item_type = 'MATERIAL' AND m.id = t.item_id;

CREATE VIEW v_work_order AS
SELECT w.*, p.code AS product_code, p.name AS product_name, p.series,
       CASE WHEN (w.good_qty + w.scrap_qty) > 0 THEN ROUND(w.good_qty / (w.good_qty + w.scrap_qty), 4) END AS yield_rate
FROM mes_work_order w JOIN md_product p ON p.id = w.product_id;

CREATE VIEW v_wo_report AS
SELECT r.*, w.wo_no, w.line_code, p.code AS product_code, p.name AS product_name
FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id JOIN md_product p ON p.id = w.product_id;

CREATE VIEW v_iqc AS
SELECT q.*, m.code AS material_code, m.name AS material_name, m.category, s.name AS supplier_name, g.gr_no
FROM qms_iqc q JOIN md_material m ON m.id = q.material_id JOIN md_supplier s ON s.id = q.supplier_id
JOIN pur_receipt g ON g.id = q.gr_id;

CREATE VIEW v_complaint AS
SELECT c.*, cu.name AS customer_name, p.code AS product_code, p.name AS product_name, p.series
FROM qms_complaint c JOIN md_customer cu ON cu.id = c.customer_id JOIN md_product p ON p.id = c.product_id;

-- 销售事实视图（发货行粒度，收入按发货确认）：BI 与财务分析 Agent 的核心数据源
CREATE VIEW v_sales_fact AS
SELECT dl.id AS id, d.delivery_no, d.delivery_date,
       DATE_FORMAT(d.delivery_date, '%Y-%m') AS month,
       CONCAT(YEAR(d.delivery_date), '-Q', QUARTER(d.delivery_date)) AS quarter,
       c.id AS customer_id, c.name AS customer_name, c.industry, c.region, c.is_domestic,
       p.id AS product_id, p.code AS product_code, p.name AS product_name, p.series, p.business_unit,
       o.currency, o.fx_rate, dl.qty,
       ROUND(dl.qty * dl.unit_price * o.fx_rate, 2) AS revenue_cny,
       ROUND(dl.qty * dl.unit_cost, 2)              AS cost_cny,
       ROUND(dl.qty * dl.unit_price * o.fx_rate - dl.qty * dl.unit_cost, 2) AS gross_profit_cny
FROM sal_delivery_line dl
JOIN sal_delivery d ON d.id = dl.delivery_id
JOIN sal_order o    ON o.id = d.order_id
JOIN md_customer c  ON c.id = d.customer_id
JOIN md_product p   ON p.id = dl.product_id;
