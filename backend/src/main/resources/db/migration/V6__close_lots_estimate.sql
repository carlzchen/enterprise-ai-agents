-- 月结与结转损益 / 应付暂估 / 批次管理与质量追溯

INSERT INTO gl_account (code, name, category, direction) VALUES
 ('220202', '应付账款-暂估', 'LIABILITY', 'CREDIT'),
 ('4103', '本年利润', 'EQUITY', 'CREDIT'),
 ('4104', '利润分配-未分配利润', 'EQUITY', 'CREDIT');

CREATE TABLE gl_period (
  period     CHAR(7)     PRIMARY KEY COMMENT '会计期间 YYYY-MM',
  status     VARCHAR(10) NOT NULL COMMENT 'OPEN / CLOSED',
  closed_by  VARCHAR(50),
  closed_at  DATETIME,
  profit     DECIMAL(18,2) COMMENT '本期结转利润'
) COMMENT '会计期间（已结账期间禁止记账）';

-- 批次：每次入库形成一个批次（原材料批次 = 到货单号，成品批次 = 工单号），出库按先进先出消耗
CREATE TABLE inv_lot (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  item_type     VARCHAR(10)   NOT NULL,
  item_id       BIGINT        NOT NULL,
  lot_no        VARCHAR(40)   NOT NULL COMMENT '批次号',
  source_type   VARCHAR(20)   NOT NULL COMMENT 'GOODS_RECEIPT / WORK_ORDER / ADJUST',
  source_no     VARCHAR(30),
  supplier_id   BIGINT        COMMENT '原材料批次的供应商',
  received_date DATE          NOT NULL,
  qty_in        DECIMAL(18,3) NOT NULL,
  qty_remaining DECIMAL(18,3) NOT NULL,
  INDEX idx_lot_item (item_type, item_id, received_date),
  INDEX idx_lot_no (lot_no)
) COMMENT '库存批次';

CREATE TABLE inv_lot_txn (
  id        BIGINT AUTO_INCREMENT PRIMARY KEY,
  lot_id    BIGINT        NOT NULL,
  txn_type  VARCHAR(20)   NOT NULL COMMENT 'WO_ISSUE / SO_DELIVERY / ADJUST',
  qty       DECIMAL(18,3) NOT NULL COMMENT '消耗数量（正数）',
  ref_no    VARCHAR(30)   NOT NULL COMMENT '消耗单据：工单号 / 发货单号',
  txn_date  DATE          NOT NULL,
  INDEX idx_lt_lot (lot_id),
  INDEX idx_lt_ref (ref_no)
) COMMENT '批次消耗记录（追溯链路）';

CREATE VIEW v_lot AS
SELECT l.*, COALESCE(p.code, m.code) AS item_code, COALESCE(p.name, m.name) AS item_name, s.name AS supplier_name,
       (SELECT q.result FROM qms_iqc q JOIN pur_receipt g ON g.id = q.gr_id
         WHERE g.gr_no = l.source_no AND q.material_id = l.item_id LIMIT 1) AS iqc_result
FROM inv_lot l
LEFT JOIN md_product p ON l.item_type = 'PRODUCT' AND p.id = l.item_id
LEFT JOIN md_material m ON l.item_type = 'MATERIAL' AND m.id = l.item_id
LEFT JOIN md_supplier s ON s.id = l.supplier_id;

CREATE VIEW v_period AS
SELECT p.*, (SELECT COUNT(*) FROM gl_voucher v WHERE DATE_FORMAT(v.voucher_date, '%Y-%m') = p.period) AS voucher_count
FROM gl_period p;

-- APS 物料齐套
ALTER TABLE aps_plan_item ADD COLUMN kit_status VARCHAR(20) NOT NULL DEFAULT 'READY' COMMENT 'READY齐套/WAIT_PO等待在途到货/SHORTAGE缺料无在途',
                          ADD COLUMN earliest_start DATE COMMENT '物料最早齐套日',
                          ADD COLUMN shortage VARCHAR(300) COMMENT '缺料说明';
