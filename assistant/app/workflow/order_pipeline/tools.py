"""
订单 Agent 的工具。R = 只读，W = 写（必须带人工审批通过的凭证才能执行）。

  R  parse_document        解析附件
  R  map_customer_part     客户料号 → 内部物料号（映射表，含人工确认后学到的）
  R  get_price_reference   价目表（下单日有效）+ 最近成交价
  R  find_existing_po      这个客户这个 PO 号已经收过哪些版本
  W  create_erp_sales_order  在 ERP 保存销售订单（星空只 Save，不提交、不审核）
  W  learn_part_mapping      把人工确认的料号映射写回映射表
  W  record_po_version       记下这一版 PO（之后的邮件按它判断变更 / 重复）

W 工具的保护：调用时必须在 approved(...) 上下文里（阶段 3 由图上的人工审批节点进入），否则直接拒绝，保证审批前不会发生任何写操作。
"""
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

from app.workflow import adapters, erp, store
from app.workflow.adapters.models import ERPSaveResult, SalesOrderDraft
from app.workflow.order_pipeline.parse import ParsedDoc, parse_document as _parse

_approval: ContextVar[dict | None] = ContextVar("approval", default=None)
TOOLS: dict[str, tuple[Callable, str]] = {}


class NotApproved(PermissionError):
    """写类工具在没有人工审批凭证的情况下被调用"""


def tool(kind: str):
    def wrap(fn):
        TOOLS[fn.__name__] = (fn, kind)
        if kind == "R":
            return fn

        def guarded(*a, **kw):
            if not _approval.get():
                raise NotApproved(f"{fn.__name__} 是写操作，必须经过人工审批")
            return fn(*a, **kw)
        guarded.__name__, guarded.__doc__ = fn.__name__, fn.__doc__
        TOOLS[fn.__name__] = (guarded, kind)
        return guarded
    return wrap


@contextmanager
def approved(approver: str, action_id: str):
    """人工审批通过后执行写操作的上下文；审计日志记下谁批的哪个动作"""
    token = _approval.set({"approver": approver, "action_id": action_id})
    try:
        yield
    finally:
        _approval.reset(token)


@tool("R")
def parse_document(name: str, data: bytes) -> ParsedDoc:
    return _parse(name, data)


@tool("R")
def map_customer_part(customer_id: int) -> dict[str, tuple[str, str]]:
    rows = store.query("SELECT customer_part_no, product_code, source FROM wf_customer_part_map WHERE customer_id = %s", (customer_id,))
    return {r["customer_part_no"]: (r["product_code"], r["source"]) for r in rows}


@tool("R")
def get_price_reference(customer_id: int, on: date | None) -> tuple[dict[str, float], dict[str, float]]:
    """（价目表单价, 最近成交价）两个字典，键是内部物料号"""
    on = on or date.today()
    rows = store.query("SELECT product_code, unit_price FROM wf_price_list WHERE customer_id = %s AND effective_from <= %s AND (effective_to IS NULL OR effective_to >= %s) "
                       "ORDER BY effective_from", (customer_id, on, on))
    prices = {r["product_code"]: float(r["unit_price"]) for r in rows}
    deals = {}
    for r in erp._q("SELECT p.code, l.unit_price FROM sal_order o JOIN sal_order_line l ON l.order_id = o.id JOIN md_product p ON p.id = l.product_id "
                    "WHERE o.customer_id = %s AND o.status <> 'CANCELLED' ORDER BY o.order_date, o.id", (customer_id,)):
        deals[r["code"]] = round(float(r["unit_price"]), 4)          # 按时间顺序覆盖，留下的是最近一次
    return prices, deals


@tool("R")
def find_existing_po(customer_id: int, po_no: str) -> list[dict]:
    rows = store.query("SELECT version, content, message_id, order_no, source FROM wf_po_version WHERE customer_id = %s AND po_no = %s ORDER BY version",
                       (customer_id, po_no))
    return [{**r, "content": store.loads(r["content"])} for r in rows]


@tool("W")
def create_erp_sales_order(draft: SalesOrderDraft) -> ERPSaveResult:
    r = adapters.erp().save_sales_order(draft)
    a = _approval.get()
    store.audit(a["approver"], "erp.save_sales_order", f"po:{draft.customer_code}:{draft.customer_po}", {"action": a["action_id"], "result": r.model_dump()})
    return r


@tool("W")
def learn_part_mapping(customer_id: int, customer_part_no: str, product_code: str, description: str | None = None) -> None:
    a = _approval.get()
    store.execute("INSERT INTO wf_customer_part_map (customer_id, customer_part_no, product_code, customer_desc, source, confirmed_by) VALUES (%s,%s,%s,%s,'learned',%s) "
                  "ON DUPLICATE KEY UPDATE product_code = VALUES(product_code), source = 'learned', confirmed_by = VALUES(confirmed_by)",
                  (customer_id, customer_part_no, product_code, (description or "")[:200], a["approver"]))
    store.audit(a["approver"], "part_map.learn", f"part:{customer_id}:{customer_part_no}", {"product_code": product_code, "action": a["action_id"]})


def insert_po_version(customer_id: int, po_no: str, version: int, content: dict, message_id: str | None) -> bool:
    """写一版 PO 记录；同一封邮件已经记过就跳过。阶段 2 的单独演示直接调它，跟单图上经 record_po_version（审批后）调用"""
    if message_id and store.one("SELECT 1 FROM wf_po_version WHERE customer_id = %s AND po_no = %s AND message_id = %s", (customer_id, po_no, message_id)):
        return False
    store.execute("INSERT INTO wf_po_version (customer_id, po_no, version, content, message_id, source) VALUES (%s,%s,%s,%s,%s,'pipeline')",
                  (customer_id, po_no, version, store.dumps(content), message_id))
    return True


@tool("W")
def record_po_version(customer_id: int, po_no: str, version: int, content: dict, message_id: str | None) -> bool:
    a = _approval.get()
    done = insert_po_version(customer_id, po_no, version, content, message_id)
    store.audit(a["approver"], "po.record_version", f"po:{customer_id}:{po_no}", {"version": version, "message_id": message_id, "inserted": done, "action": a["action_id"]})
    return done
