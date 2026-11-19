"""
Workflow 自己的表（放在 assistant 库，和 ERP 业务库分开；ERP 只读）。读写沿用知识库的数据库工具。
  wf_mail / wf_mail_intent    邮件和识别出的意图（一封邮件可以有多个意图）
  wf_customer_ext             客户的补充信息：等级、负责人、邮箱域名（ERP 里没有这几项）
  wf_opportunity / _history   商机和状态变化记录
  wf_inquiry                  询价：标准化后的需求、缺的参数、选型结果
  wf_quote                    报价（每改一次价是一个新版本），带成本快照
  wf_negotiation              议价轮次
  wf_sales_order              收到的客户 PO 和评审结果（客户 + PO 号唯一，用来拦重复下单）
  wf_task                     待办
  wf_outbound                 待外发的内容（发件闸门）：原稿、终稿、谁确认的、是否被采用
  wf_outbox / wf_event_done   事件发件箱、订阅方去重
  wf_rule                     规则（JSON，带版本，改动不覆盖旧版本）
  wf_case                     跟单图的案例（一封邮件一个）
  wf_audit                    审计
"""
import json
import threading
from datetime import datetime

from app.kbase import store as _s

query, one, execute, executemany, transaction, dumps = _s.query, _s.one, _s.execute, _s.executemany, _s.transaction, _s.dumps

_DDL = [
    """CREATE TABLE IF NOT EXISTS wf_mail (
      message_id VARCHAR(255) PRIMARY KEY, thread_id VARCHAR(64) NOT NULL, in_reply_to VARCHAR(255), from_addr VARCHAR(200), from_name VARCHAR(200),
      real_from VARCHAR(200) COMMENT '转发邮件里的真实发件人', subject VARCHAR(500), received_at DATETIME, body_new MEDIUMTEXT COMMENT '去掉引用后的正文',
      attachments JSON, raw_path VARCHAR(600), party_type VARCHAR(10) COMMENT 'CUSTOMER / SUPPLIER / UNKNOWN', party_id BIGINT, party_name VARCHAR(200),
      lang VARCHAR(8), summary VARCHAR(500), status VARCHAR(12) NOT NULL DEFAULT 'NEW' COMMENT 'NEW / PENDING 待确认 / TRIAGED / ARCHIVED / FAILED',
      pending_reason VARCHAR(300), opp_id VARCHAR(32), owner VARCHAR(60), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, triaged_at DATETIME,
      KEY idx_thread (thread_id), KEY idx_status (status, received_at)) COMMENT '业务邮件'""",
    """CREATE TABLE IF NOT EXISTS wf_mail_intent (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, message_id VARCHAR(255) NOT NULL, intent VARCHAR(30) NOT NULL, confidence DECIMAL(4,3), extracted JSON,
      original JSON COMMENT '人工修正前系统给的结果', corrected_by VARCHAR(60), dispatched TINYINT NOT NULL DEFAULT 0, UNIQUE KEY uk (message_id, intent)) COMMENT '邮件意图'""",
    """CREATE TABLE IF NOT EXISTS wf_customer_ext (
      customer_id BIGINT PRIMARY KEY, level VARCHAR(2) NOT NULL, owner VARCHAR(60) NOT NULL, domains VARCHAR(300)) COMMENT '客户补充信息'""",
    """CREATE TABLE IF NOT EXISTS wf_opportunity (
      opp_id VARCHAR(32) PRIMARY KEY, customer_id BIGINT, customer_name VARCHAR(200), owner VARCHAR(60), title VARCHAR(300), status VARCHAR(20) NOT NULL,
      next_action VARCHAR(300), next_due_at DATETIME, close_reason VARCHAR(300), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, KEY idx_owner (owner, status)) COMMENT '商机'""",
    """CREATE TABLE IF NOT EXISTS wf_opp_history (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, opp_id VARCHAR(32) NOT NULL, from_status VARCHAR(20), to_status VARCHAR(20), event VARCHAR(60), actor VARCHAR(60),
      note VARCHAR(500), at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_opp (opp_id, id)) COMMENT '商机状态历史'""",
    """CREATE TABLE IF NOT EXISTS wf_inquiry (
      inquiry_id VARCHAR(32) PRIMARY KEY, opp_id VARCHAR(32) NOT NULL, message_id VARCHAR(255), items JSON, missing JSON, selection JSON,
      status VARCHAR(16) NOT NULL COMMENT 'CLARIFYING / SELECTING / CONFIRMED / NO_MATCH', confirmed_by VARCHAR(60), adopted TINYINT COMMENT '工程师是否采纳了系统推荐的第一个型号',
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_opp (opp_id)) COMMENT '询价'""",
    """CREATE TABLE IF NOT EXISTS wf_quote (
      quote_no VARCHAR(32) NOT NULL, version INT NOT NULL, opp_id VARCHAR(32) NOT NULL, customer_id BIGINT, model VARCHAR(80), annual_volume INT, tiers JSON,
      unit_cost DECIMAL(14,4), floor_price DECIMAL(14,4), margin DECIMAL(6,4) COMMENT '第一档价格对应的毛利率', snapshot JSON COMMENT '成本快照，不可修改',
      status VARCHAR(12) NOT NULL COMMENT 'DRAFT / APPROVING / APPROVED / SENT / REJECTED / SUPERSEDED', approver_role VARCHAR(20), reason VARCHAR(500),
      approved_by VARCHAR(60), valid_until DATE, created_by VARCHAR(60), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, sent_at DATETIME, reminded TINYINT NOT NULL DEFAULT 0,
      PRIMARY KEY (quote_no, version), KEY idx_opp (opp_id)) COMMENT '报价'""",
    """CREATE TABLE IF NOT EXISTS wf_negotiation (
      quote_no VARCHAR(32) NOT NULL, round INT NOT NULL, message_id VARCHAR(255), target_price DECIMAL(14,4), reason VARCHAR(500), analysis JSON,
      strategy VARCHAR(30), new_price DECIMAL(14,4), result VARCHAR(30), decided_by VARCHAR(60), at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (quote_no, round)) COMMENT '议价轮次'""",
    """CREATE TABLE IF NOT EXISTS wf_sales_order (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, customer_id BIGINT NOT NULL, po_no VARCHAR(80) NOT NULL, opp_id VARCHAR(32), message_id VARCHAR(255), data JSON, issues JSON,
      review_status VARCHAR(12) NOT NULL COMMENT 'REVIEWING / PASSED / REJECTED', erp_order_no VARCHAR(60), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_po (customer_id, po_no)) COMMENT '客户 PO 和评审'""",
    """CREATE TABLE IF NOT EXISTS wf_task (
      task_id VARCHAR(32) PRIMARY KEY, type VARCHAR(24) NOT NULL, title VARCHAR(300) NOT NULL, role VARCHAR(20) NOT NULL COMMENT '由哪个角色处理', assignee VARCHAR(60),
      ref_type VARCHAR(20), ref_id VARCHAR(255), opp_id VARCHAR(32), customer_name VARCHAR(200), payload JSON, status VARCHAR(10) NOT NULL DEFAULT 'OPEN',
      due_at DATETIME, escalated TINYINT NOT NULL DEFAULT 0, result VARCHAR(500), done_by VARCHAR(60), done_at DATETIME, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      KEY idx_open (status, role, due_at), KEY idx_opp (opp_id)) COMMENT '待办'""",
    """CREATE TABLE IF NOT EXISTS wf_outbound (
      action_id VARCHAR(32) PRIMARY KEY, task_id VARCHAR(32), kind VARCHAR(24) NOT NULL, opp_id VARCHAR(32), to_addr VARCHAR(200), subject VARCHAR(500), draft MEDIUMTEXT,
      final MEDIUMTEXT, drafted_by VARCHAR(12) COMMENT 'llm / template', status VARCHAR(10) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING / SENT / DISCARDED',
      adoption VARCHAR(10) COMMENT 'AS_IS 直接采用 / EDITED 小改 / DISCARDED 弃用', confirmed_by VARCHAR(60), sent_at DATETIME, sent_path VARCHAR(600),
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP) COMMENT '发件闸门'""",
    """CREATE TABLE IF NOT EXISTS wf_outbox (
      event_id VARCHAR(40) PRIMARY KEY, type VARCHAR(40) NOT NULL, payload JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, published_at DATETIME,
      KEY idx_pub (published_at, created_at)) COMMENT '事件发件箱'""",
    """CREATE TABLE IF NOT EXISTS wf_event_done (
      consumer VARCHAR(40) NOT NULL, event_id VARCHAR(40) NOT NULL, at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (consumer, event_id)) COMMENT '订阅方已处理的事件'""",
    """CREATE TABLE IF NOT EXISTS wf_rule (
      name VARCHAR(40) NOT NULL, version INT NOT NULL, content JSON NOT NULL, active TINYINT NOT NULL DEFAULT 1, updated_by VARCHAR(60),
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (name, version)) COMMENT '规则（带版本）'""",
    # ---- 跟单多 Agent 升级（SPEC 阶段 1）：ERP 里没有、星空里对应字段或扩展表的数据，source 区分模拟数据（mock）和人工 / 学习写入 ----
    """CREATE TABLE IF NOT EXISTS wf_material_ext (
      product_code VARCHAR(40) PRIMARY KEY, moq INT NOT NULL COMMENT '最小起订量', pack_qty INT NOT NULL COMMENT '包装倍数（整箱数量）',
      std_lead_days INT NOT NULL COMMENT '标准交期（下单到交货的天数）', source VARCHAR(10) NOT NULL DEFAULT 'mock') COMMENT '物料的销售属性'""",
    """CREATE TABLE IF NOT EXISTS wf_price_list (
      customer_id BIGINT NOT NULL, product_code VARCHAR(40) NOT NULL, currency VARCHAR(3) NOT NULL, unit_price DECIMAL(14,4) NOT NULL COMMENT '含税单价',
      effective_from DATE NOT NULL, effective_to DATE, source VARCHAR(10) NOT NULL DEFAULT 'mock',
      PRIMARY KEY (customer_id, product_code, effective_from)) COMMENT '客户价目表'""",
    """CREATE TABLE IF NOT EXISTS wf_customer_part_map (
      customer_id BIGINT NOT NULL, customer_part_no VARCHAR(60) NOT NULL, product_code VARCHAR(40) NOT NULL, customer_desc VARCHAR(200),
      source VARCHAR(10) NOT NULL DEFAULT 'mock' COMMENT 'mock 模拟 / learned 人工确认后学到的', confirmed_by VARCHAR(60), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (customer_id, customer_part_no), KEY idx_product (product_code)) COMMENT '客户料号 ↔ 内部物料号'""",
    """CREATE TABLE IF NOT EXISTS wf_order_po_ref (
      order_no VARCHAR(30) PRIMARY KEY, customer_id BIGINT NOT NULL, customer_po VARCHAR(60) NOT NULL, source VARCHAR(10) NOT NULL DEFAULT 'mock',
      UNIQUE KEY uk_cust_po (customer_id, customer_po)) COMMENT 'ERP 销售订单上的客户 PO 号（星空销售订单的客户订单号字段）'""",
    """CREATE TABLE IF NOT EXISTS wf_po_version (
      customer_id BIGINT NOT NULL, po_no VARCHAR(60) NOT NULL, version INT NOT NULL, content JSON NOT NULL COMMENT 'POHeader + POLine[]',
      message_id VARCHAR(255), order_no VARCHAR(30) COMMENT '录入 ERP 后的销售订单号', source VARCHAR(10) NOT NULL DEFAULT 'pipeline', created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (customer_id, po_no, version)) COMMENT 'PO 每一版的内容，变更比对用'""",
    """CREATE TABLE IF NOT EXISTS wf_customer_memory (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, customer_id BIGINT NOT NULL, kind VARCHAR(20) NOT NULL COMMENT 'PREFERENCE / COMMITMENT / NOTE',
      content VARCHAR(1000) NOT NULL, source_ref VARCHAR(255), created_by VARCHAR(60), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_cust (customer_id)) COMMENT '跟进 Agent 的客户长期记忆'""",
    """CREATE TABLE IF NOT EXISTS wf_followup (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, customer_id BIGINT NOT NULL, kind VARCHAR(20) NOT NULL COMMENT 'SAMPLE / QUOTE / OTHER', ref VARCHAR(60),
      due_at DATETIME NOT NULL, status VARCHAR(10) NOT NULL DEFAULT 'OPEN', note VARCHAR(500), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_due (status, due_at)) COMMENT '待跟进事项'""",
    """CREATE TABLE IF NOT EXISTS wf_case (
      case_id VARCHAR(40) PRIMARY KEY, message_id VARCHAR(300) NOT NULL, customer_id BIGINT, subject VARCHAR(300), status VARCHAR(20) NOT NULL COMMENT 'RUNNING / WAITING_APPROVAL / DONE / REJECTED / IGNORED / FAILED',
      subtasks JSON, review_passed TINYINT, review_rounds INT, error VARCHAR(500), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_msg (message_id(250)), KEY idx_status (status)) COMMENT '跟单图的案例（一封邮件一个），图的状态在 checkpointer 里'""",
    """CREATE TABLE IF NOT EXISTS wf_audit (
      id BIGINT AUTO_INCREMENT PRIMARY KEY, actor VARCHAR(60), action VARCHAR(40), object VARCHAR(120), detail JSON, at DATETIME DEFAULT CURRENT_TIMESTAMP,
      KEY idx_obj (object, id)) COMMENT '审计'""",
]

# 规则的初始值。数值都是示例，真实的阈值要由销售和财务来定
DEFAULT_RULES = {
    "margin": {"说明": "按客户等级设定目标毛利率和最低毛利率",
               "levels": {"A": {"target": 0.20, "min": 0.10}, "B": {"target": 0.24, "min": 0.13}, "C": {"target": 0.28, "min": 0.16}, "NEW": {"target": 0.30, "min": 0.18}}},
    "tiers": {"说明": "数量阶梯：达到这个数量时，在第一档价格上乘以系数；任何一档都不会低于底价", "tiers": [{"qty": 1000, "factor": 1.0}, {"qty": 5000, "factor": 0.97}, {"qty": 10000, "factor": 0.94}]},
    "approval": {"说明": "报价毛利率不低于目标 → 销售经理；介于最低和目标之间 → 销售总监；低于最低 → 总经理并填写理由",
                 "at_or_above_target": "SALES_MANAGER", "between": "SALES_DIRECTOR", "below_min": "GM"},
    "customer_level": {"说明": "ERP 里没有客户等级，这里按信用额度推一个初始值", "A_credit_at_least": 20000000, "B_credit_at_least": 10000000},
    "required_params": {"说明": "各产品线询价时必须有的参数；缺了就先向客户澄清",
                        "SERVO": ["power_w", "voltage_v"], "STEPPER": ["frame_size"], "BLDC": ["power_w", "voltage_v"], "SHADED_POLE": ["power_w", "voltage_v"],
                        "CROSSFLOW": ["frame_size", "voltage_v"], "GEAR": ["power_w", "voltage_v"], "ALL": ["annual_volume"]},
    "selection": {"说明": "选型比对的容差", "power_over_ratio": 0.5, "speed_tolerance": 0.1},
    "quote": {"说明": "报价有效期和到期前几天提醒", "valid_days": 30, "remind_days_before": 5},
    "po_review": {"说明": "PO 核对的容差和交期规则", "price_tolerance": 0.005, "lead_days": 20},
    "sla_hours": {"说明": "各类待办的处理时限（小时），超时提醒并升级", "TRIAGE": 4, "CLARIFY": 8, "SELECTION": 24, "QUOTE_REVIEW": 24, "APPROVAL": 24, "SEND": 8,
                  "NEGOTIATION": 24, "PO_ISSUE": 24, "ERP_IMPORT": 8, "ASSIGN": 24},
    "autosend": {"说明": "哪些外发动作允许不经人工确认直接发。默认全部需要确认", "allowed": []},
    "order_check": {"说明": "订单 Agent 的规则校验：单价偏离价目表 / 最近成交价超过 price_tolerance 就标记；金额对不上的容差（元）",
                    "price_tolerance": 0.03, "amount_tolerance": 0.01},
    "po_templates": {"说明": "已配置解析模板的客户：客户 ID → 模板名（cn_pdf_grid 中文 PDF 表格 / cn_xlsx 中文 Excel）。没配的客户用大模型抽取", "customers": {}},
}

_lock, _ready = threading.Lock(), False


def ensure() -> None:
    global _ready
    with _lock:
        if _ready:
            return
        for ddl in _DDL:
            execute(ddl)
        for name, content in DEFAULT_RULES.items():
            if not one("SELECT 1 FROM wf_rule WHERE name = %s LIMIT 1", (name,)):
                execute("INSERT INTO wf_rule (name, version, content, updated_by) VALUES (%s, 1, %s, 'system')", (name, dumps(content)))
        _ready = True


def rule(name: str) -> dict:
    ensure()
    row = one("SELECT content, version FROM wf_rule WHERE name = %s AND active = 1 ORDER BY version DESC LIMIT 1", (name,))
    content = row["content"]
    return {**(json.loads(content) if isinstance(content, str) else content), "_version": row["version"]}


def set_rule(name: str, content: dict, who: str) -> int:
    """保存规则的新版本，旧版本保留"""
    content = {k: v for k, v in content.items() if not k.startswith("_")}
    v = (one("SELECT MAX(version) v FROM wf_rule WHERE name = %s", (name,))["v"] or 0) + 1
    execute("UPDATE wf_rule SET active = 0 WHERE name = %s", (name,))
    execute("INSERT INTO wf_rule (name, version, content, updated_by) VALUES (%s,%s,%s,%s)", (name, v, dumps(content), who))
    audit(who, "rule.update", f"rule:{name}", {"version": v})
    return v


def audit(actor: str, action: str, obj: str, detail: dict | None = None) -> None:
    execute("INSERT INTO wf_audit (actor, action, object, detail) VALUES (%s,%s,%s,%s)", (actor, action, obj[:120], dumps(detail or {})))


def new_id(prefix: str) -> str:
    import uuid
    return f"{prefix}{datetime.now():%y%m%d}{uuid.uuid4().hex[:6].upper()}"


def loads(v):
    return json.loads(v) if isinstance(v, str) else v
