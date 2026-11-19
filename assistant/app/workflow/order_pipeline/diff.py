"""
PO 变更比对（纯代码）：同一个 PO 号再来时，和上一版逐行比，输出数量、单价、交期的变化，以及新增行、取消行。
按客户料号对行（行号在变更时可能重排）；没有料号的行退回按行号对。
"""
from datetime import date

from app.workflow.order_pipeline.schemas import FieldChange, POChangeReport, POExtraction

FIELDS = ("qty", "unit_price", "delivery_date")


def _key(ln: dict) -> str:
    return f"P:{ln['customer_part_no']}" if ln.get("customer_part_no") else f"L:{ln['line_no']}"


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return v.isoformat() if isinstance(v, date) else str(v)


def as_content(po: POExtraction) -> dict:
    """存进 wf_po_version 的格式（也是比对的输入格式）"""
    return {"po_no": po.header.po_no, "order_date": _fmt(po.header.order_date) if po.header.order_date else None, "currency": po.header.currency,
            "total_amount": po.header.total_amount,
            "lines": [{"line_no": ln.line_no, "customer_part_no": ln.customer_part_no, "description": ln.description, "qty": ln.qty,
                       "unit_price": ln.unit_price, "delivery_date": _fmt(ln.delivery_date) if ln.delivery_date else None} for ln in po.lines]}


def same_content(a: dict, b: dict) -> bool:
    """两版内容是否完全相同（重复发送的判断）：只比明细的料号、数量、单价、交期"""
    def norm(c):
        return sorted((_key(ln), *(_fmt(ln.get(f)) for f in FIELDS)) for ln in c.get("lines", []))
    return norm(a) == norm(b)


def diff(base: dict, new: dict, base_version: int, new_version: int) -> POChangeReport:
    old_lines = {_key(ln): ln for ln in base.get("lines", [])}
    new_lines = {_key(ln): ln for ln in new.get("lines", [])}
    changes = []
    for k, ln in new_lines.items():
        if k not in old_lines:
            continue
        o = old_lines[k]
        for f in FIELDS:
            ov, nv = o.get(f), ln.get(f)
            if f in ("qty", "unit_price") and ov is not None and nv is not None and abs(float(ov) - float(nv)) < 1e-9:
                continue
            if _fmt(ov) != _fmt(nv):
                changes.append(FieldChange(line_no=ln["line_no"], customer_part_no=ln.get("customer_part_no"), field=f, old=_fmt(ov), new=_fmt(nv)))
    return POChangeReport(po_no=new.get("po_no") or base.get("po_no"), base_version=base_version, new_version=new_version, changes=changes,
                          added_lines=sorted(ln["line_no"] for k, ln in new_lines.items() if k not in old_lines),
                          cancelled_lines=sorted(ln["line_no"] for k, ln in old_lines.items() if k not in new_lines))
