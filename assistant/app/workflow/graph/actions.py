"""
审批通过后的写操作。全部是 W 工具（和订单 Agent 的写工具同一套保护：不在 approved(...) 里调用直接报 NotApproved）。

  W  send_email               发回复邮件（经邮箱适配器，用动作编号做幂等）
  W  create_task              派待办（经任务适配器：Mock 是跟单工作台的待办中心，真实是飞书）
  W  create_erp_sales_order   在 ERP 保存销售订单草稿（订单 Agent 的工具）
  W  record_po_version        记下这一版 PO（订单 Agent 的工具）

execute() 是图上「执行」节点唯一调用的入口：逐个动作进 approved(审批人, 动作编号)，执行结果和失败都记下来，一个失败不影响其他动作。
"""
from app.workflow import adapters, store
from app.workflow.adapters.models import EmailDraft, SalesOrderDraft, TaskRequest
from app.workflow.order_pipeline import tools as OT
from app.workflow.order_pipeline.tools import approved, tool


@tool("W")
def send_email(draft: EmailDraft) -> dict:
    a = OT._approval.get()
    r = adapters.email().send(draft)
    store.audit(a["approver"], "email.send", f"mail:{draft.ref}", {"to": draft.to, "subject": draft.subject, "action": a["action_id"]})
    return r.model_dump(mode="json")


@tool("W")
def create_task(req: TaskRequest) -> str:
    a = OT._approval.get()
    tid = adapters.task().create_task(req)
    store.audit(a["approver"], "task.create", f"task:{tid}", {"title": req.title, "role": req.role, "action": a["action_id"]})
    return tid


def _run_one(kind: str, payload: dict, case_id: str, action_id: str):
    if kind == "send_email":
        return send_email(EmailDraft(**payload, ref=f"{case_id}:{action_id}"))
    if kind == "create_task":
        p = dict(payload)
        detail, order_no = p.pop("detail", ""), p.pop("order_no", None)
        req = TaskRequest(**{**p, "ref_id": case_id, "payload": {"detail": detail, "order_no": order_no, "action": action_id}})
        return create_task(req)
    if kind == "erp_save_sales_order":
        return OT.create_erp_sales_order(SalesOrderDraft.model_validate(payload)).model_dump(mode="json")
    if kind == "record_po_version":
        return OT.record_po_version(**payload)
    raise ValueError(f"不认识的动作 {kind}")


def execute(case_id: str, actions: list[dict], approver: str) -> list[dict]:
    out = []
    for a in actions:
        with approved(approver, f"{case_id}:{a['id']}"):
            try:
                out.append({"id": a["id"], "kind": a["kind"], "ok": True, "result": _run_one(a["kind"], a["payload"], case_id, a["id"])})
            except Exception as e:
                out.append({"id": a["id"], "kind": a["kind"], "ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return out
