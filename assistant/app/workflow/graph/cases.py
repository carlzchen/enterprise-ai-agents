"""
跟单图的入口：一封邮件开一个案例，跑到人工审批停下；审批人提交后从断点接着跑。

    start(raw)              → 案例停在审批（WAITING_APPROVAL），或者是无关邮件直接结束（IGNORED）
    pending(case_id)        → 审批页要展示的内容（拆分结果、草稿、审核结果、待执行的写操作）
    decide(case_id, {...})  → 批准就执行写操作（DONE），驳回就结束（REJECTED）

图的状态存在 checkpointer（默认 data/workflow_cases.sqlite）；wf_case 表只记案例的索引和状态，方便待办中心列出待审批的案例。
同一封邮件（Message-ID）只开一个案例。
"""
import base64

from langgraph.types import Command

from app.workflow import mailbox, store
from app.workflow.graph import build as B
from app.workflow.graph.state import Approval

_graph = None


def graph():
    """默认的图：真实模型 + SQLite checkpointer"""
    global _graph
    if _graph is None:
        _graph = B.build(B.Deps(), B.sqlite_saver())
    return _graph


def _cfg(case_id: str) -> dict:
    return {"configurable": {"thread_id": case_id}}


def _save(case_id: str, message_id: str, state: dict, status: str, error: str | None = None) -> None:
    rv = (state.get("reviews") or [None])[-1]
    store.execute("INSERT INTO wf_case (case_id, message_id, customer_id, subject, status, subtasks, review_passed, review_rounds, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                  "ON DUPLICATE KEY UPDATE status = VALUES(status), customer_id = VALUES(customer_id), subtasks = VALUES(subtasks), "
                  "review_passed = VALUES(review_passed), review_rounds = VALUES(review_rounds), error = VALUES(error)",
                  (case_id, message_id, (state.get("customer") or {}).get("id"), (state.get("mail") or {}).get("subject", "")[:300], status,
                   store.dumps(state.get("subtasks") or []), None if rv is None else int(rv["passed"]), len(state.get("drafts") or []) - 1 if state.get("drafts") else None,
                   (error or "")[:500] or None))


def _status(state: dict) -> str:
    return "WAITING_APPROVAL" if state.get("__interrupt__") else state.get("status") or "RUNNING"


def start(raw: bytes, g=None) -> dict:
    """处理一封邮件。返回 {case_id, status, state}；同一封邮件再来一次直接返回已有的案例"""
    store.ensure()
    g = g or graph()
    mid = mailbox.parse(raw)["message_id"]
    old = store.one("SELECT case_id, status FROM wf_case WHERE message_id = %s", (mid,))
    if old:
        return {"case_id": old["case_id"], "status": old["status"], "state": g.get_state(_cfg(old["case_id"])).values, "existing": True}
    case_id = store.new_id("CS")
    _save(case_id, mid, {}, "RUNNING")
    try:
        state = g.invoke({"case_id": case_id, "raw_b64": base64.b64encode(raw).decode()}, _cfg(case_id))
    except Exception as e:
        _save(case_id, mid, {}, "FAILED", f"{type(e).__name__}: {e}")
        raise
    status = _status(state)
    _save(case_id, mid, state, status)
    return {"case_id": case_id, "status": status, "state": state}


def pending(case_id: str, g=None) -> dict | None:
    """停在审批时，审批页要看的内容（interrupt 的参数）；不在等审批就是 None"""
    snap = (g or graph()).get_state(_cfg(case_id))
    for t in snap.tasks:
        if t.interrupts:
            return t.interrupts[0].value
    return None


def decide(case_id: str, approval: dict, g=None) -> dict:
    """审批人提交。只有停在审批的案例才能提交，重复提交会被拒绝（写操作不会执行两次）"""
    g = g or graph()
    if pending(case_id, g) is None:
        raise ValueError(f"案例 {case_id} 不在等待审批")
    ap = Approval.model_validate(approval)
    state = g.invoke(Command(resume=ap.model_dump(mode="json")), _cfg(case_id))
    status = _status(state)
    _save(case_id, state["mail"]["message_id"], state, status)
    return {"case_id": case_id, "status": status, "state": state}
