"""
销售与采购跟单 Workflow 服务（端口 8095）
  待办      GET /tasks   GET /tasks/{id}   POST /tasks/{id}/done
  发件闸门  POST /outbound/{id}/confirm | discard
  邮件      GET /mails   GET /mails/{id}   POST /mails/{id}/correct   POST /mails/pull
  商机      GET /opportunities   GET /opportunities/{id}   POST /opportunities/{id}/close
  选型      POST /inquiries/{id}/confirm
  报价      GET /quotes/{ref}   POST /quotes/{ref}/preview | submit | approve
  议价      GET /negotiations/{ref}   POST /negotiations/{ref}/decide
  订单评审  GET /orders/{id}   POST /orders/issues/{ref}/resolve   POST /orders/{id}/erp-order
  规则      GET /rules   PUT /rules/{name}
  统计      GET /stats   GET /health   GET /metrics
谁能看到什么：待办按角色分；销售只看自己负责的客户；成本构成只有管理层和财务能看。
"""
import json
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.api.auth import CurrentUser, current_user
from app.api.http.middleware import install_trace_middleware
from app.infra.config.settings import settings
from app.platform import metrics as pm
from app.workflow import erp, flows, rules, store, understand as U

app = FastAPI(title="MiniERP 销售与采购跟单 Workflow", version="0.1.0")
install_trace_middleware(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Trace-Id"])
pm.install(app)
SEE_ALL = {"ADMIN", "MANAGEMENT"}


@app.on_event("startup")
def startup():
    store.ensure()
    flows.start_background()


def _rows(rows):
    return json.loads(json.dumps(rows, ensure_ascii=False, default=str))


def _guard(fn, *args):
    try:
        return fn(*args)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


def _can_handle(task: dict, user: CurrentUser) -> bool:
    return user.role == "ADMIN" or task["role"] == user.role


def _task(task_id: str, user: CurrentUser) -> dict:
    t = store.one("SELECT * FROM wf_task WHERE task_id = %s", (task_id,))
    if not t or not (_can_handle(t, user) or user.role in SEE_ALL):
        raise HTTPException(status_code=404, detail="待办不存在或不归你处理")
    return t


def _need_role(user: CurrentUser, *roles: str) -> None:
    if user.role != "ADMIN" and user.role not in roles:
        raise HTTPException(status_code=403, detail="你的角色不能做这个操作")


def _owner_filter(user: CurrentUser, alias: str = "o") -> tuple[str, list]:
    """销售只看自己负责的客户的商机"""
    return (f" AND {alias}.owner = %s", [user.username]) if user.role == "SALES" else ("", [])


# ---------------------------------------------------------------- 待办

@app.get("/tasks")
def tasks(status: str = "OPEN", user: CurrentUser = Depends(current_user)):
    sql, args = "SELECT task_id, type, title, role, opp_id, customer_name, status, due_at, escalated, result, done_by, done_at, created_at, ref_type, ref_id FROM wf_task WHERE status = %s", [status]
    if user.role not in SEE_ALL:
        sql += " AND role = %s"
        args.append(user.role)
    rows = store.query(sql + (" ORDER BY due_at LIMIT 200" if status == "OPEN" else " ORDER BY done_at DESC LIMIT 100"), args)
    for r in rows:
        r["type_cn"], r["mine"] = flows.TASK_CN.get(r["type"], r["type"]), _can_handle(r, user)
    return _rows(rows)


@app.get("/tasks/{task_id}")
def task_detail(task_id: str, user: CurrentUser = Depends(current_user)):
    """一个待办和处理它要看的全部内容"""
    t = _task(task_id, user)
    t["payload"], t["type_cn"], t["mine"] = store.loads(t["payload"]), flows.TASK_CN.get(t["type"], t["type"]), _can_handle(t, user)
    out = {"task": t}
    ref_type, ref = t["ref_type"], t["ref_id"]
    if ref_type == "mail":
        out["mail"] = _mail(ref)
    elif ref_type == "outbound":
        out["outbound"] = store.one("SELECT * FROM wf_outbound WHERE action_id = %s", (ref,))
        src = t["payload"].get("in_reply_to")
        out["mail"] = _mail(src) if src else None
    elif ref_type == "inquiry" and t["type"] != "NONSTD_REVIEW":
        inq = store.one("SELECT * FROM wf_inquiry WHERE inquiry_id = %s", (ref,))
        for k in ("items", "missing", "selection"):
            inq[k] = store.loads(inq[k])
        out["inquiry"], out["mail"] = inq, _mail(inq["message_id"])
        out["products"] = [{"code": p["code"], "name": p["name"], "series": p["series"]} for p in erp.products()]
    elif ref_type == "inquiry" and t["type"] == "NONSTD_REVIEW":
        inq = store.one("SELECT * FROM wf_inquiry WHERE inquiry_id = %s", (ref,))
        for k in ("items", "selection"):
            inq[k] = store.loads(inq[k])
        evs = store.query("SELECT eval_id, item_index, mode, status, report, trace, tokens, seconds, decision FROM wf_nonstd_eval WHERE inquiry_id = %s ORDER BY created_at", (ref,))
        for e in evs:
            e["report"], e["trace"] = store.loads(e["report"]), store.loads(e["trace"])
            if user.role not in ("ADMIN", "MANAGEMENT", "FINANCE") and e["report"] and e["report"].get("cost"):       # 成本明细按角色可见
                e["report"]["cost"] = {k: v for k, v in e["report"]["cost"].items() if k in ("is_estimate", "applied", "nre", "notes", "evidence")}
                if e["report"].get("commercial"):
                    e["report"]["commercial"].pop("unit_cost_used", None)
        out["inquiry"], out["evals"], out["mail"] = inq, evs, _mail(inq["message_id"])
        out["products"] = [{"code": p["code"], "name": p["name"], "series": p["series"]} for p in erp.products()]
    elif ref_type == "quote":
        out["quote"] = flows.quote_view(ref, user.role)
    elif ref_type == "negotiation":
        out["negotiation"] = flows.negotiation_view(ref, user.role)
        out["mail"] = _mail(out["negotiation"]["message_id"])
        last = store.one("SELECT MAX(version) v FROM wf_quote WHERE quote_no = %s", (ref.split("#")[0],))["v"]
        out["quote"] = flows.quote_view(f"{ref.split('#')[0]}#{last}", user.role)
    elif ref_type == "order":
        o = store.one("SELECT * FROM wf_sales_order WHERE id = %s", (int(ref.split("#")[0]),))
        o["data"], o["issues"] = store.loads(o["data"]), store.loads(o["issues"])
        out["order"], out["mail"] = o, _mail(o["message_id"])
        if "#" in ref:
            out["issue"] = next((i for i in o["issues"] if i["id"] == ref.split("#")[1]), None)
    if t["opp_id"]:
        out["opportunity"] = store.one("SELECT opp_id, title, status, customer_name, owner FROM wf_opportunity WHERE opp_id = %s", (t["opp_id"],))
        if out["opportunity"]:
            out["opportunity"]["status_cn"] = flows.STATUS_CN.get(out["opportunity"]["status"])
    return _rows(out)


class DoneRequest(BaseModel):
    result: str = Field(default="", max_length=500)


@app.post("/tasks/{task_id}/done")
def task_done(task_id: str, req: DoneRequest, user: CurrentUser = Depends(current_user)):
    """只分派、没有后续流程的待办（招标、样品、投诉等）：处理完标记完成"""
    t = _task(task_id, user)
    if t["type"] != "ASSIGN" or not _can_handle(t, user):
        raise HTTPException(status_code=409, detail="这个待办要在对应的处理页面里完成")
    flows.done_task(task_id, user.username, req.result or "已处理")
    return {"ok": True}


# ---------------------------------------------------------------- 发件闸门

class ConfirmRequest(BaseModel):
    text: str = Field(max_length=20000)


@app.post("/outbound/{action_id}/confirm")
def outbound_confirm(action_id: str, req: ConfirmRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES")
    return flows.confirm_outbound(action_id, req.text, user.username)


@app.post("/outbound/{action_id}/discard")
def outbound_discard(action_id: str, req: DoneRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES")
    flows.discard_outbound(action_id, user.username, req.result)
    return {"ok": True}


# ---------------------------------------------------------------- 邮件

def _mail(message_id: str) -> dict | None:
    m = store.one("SELECT * FROM wf_mail WHERE message_id = %s", (message_id,))
    if not m:
        return None
    m["attachments"] = [{k: v for k, v in a.items() if k != "text"} | {"preview": (a.get("text") or "")[:600]} for a in (store.loads(m["attachments"]) or [])]
    m["intents"] = store.query("SELECT intent, confidence, extracted, corrected_by, dispatched FROM wf_mail_intent WHERE message_id = %s", (message_id,))
    for i in m["intents"]:
        i["extracted"], i["intent_cn"] = store.loads(i["extracted"]), U.INTENT_CN.get(i["intent"], i["intent"])
    return m


@app.get("/mails")
def mails(status: str | None = None, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES", "MANAGEMENT")
    sql, args = "SELECT message_id, subject, from_addr, from_name, real_from, party_type, party_name, status, pending_reason, summary, opp_id, received_at, lang FROM wf_mail WHERE 1 = 1", []
    if status:
        sql += " AND status = %s"
        args.append(status)
    rows = store.query(sql + " ORDER BY received_at DESC LIMIT 200", args)
    by = {}
    if rows:
        for r in store.query("SELECT message_id, intent, confidence FROM wf_mail_intent WHERE message_id IN %s", ([x["message_id"] for x in rows],)):
            by.setdefault(r["message_id"], []).append({"intent": r["intent"], "intent_cn": U.INTENT_CN.get(r["intent"], r["intent"]), "confidence": float(r["confidence"] or 0)})
    for r in rows:
        r["intents"] = by.get(r["message_id"], [])
    return _rows(rows)


@app.get("/mails/detail")
def mail_detail(id: str, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES", "MANAGEMENT")
    m = _mail(unquote(id))
    if not m:
        raise HTTPException(status_code=404, detail="邮件不存在")
    return _rows(m)


class CorrectRequest(BaseModel):
    id: str
    intents: list[str] | None = None
    customer_id: int | None = None
    fields: dict | None = None
    archive: bool = False


@app.post("/mails/correct")
def mail_correct(req: CorrectRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES")
    if req.intents is not None and any(i not in U.INTENTS for i in req.intents):
        raise HTTPException(status_code=400, detail="有不认识的意图")
    _guard(lambda: flows.correct_mail(req.id, user.username, intents=req.intents, customer_id=req.customer_id, fields=req.fields, archive=req.archive))
    return {"ok": True}


@app.post("/mails/pull")
def mail_pull(user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES", "MANAGEMENT")
    return {"new": flows.poll_mailbox()}


@app.post("/mails/retry-unread")
def mail_retry(user: CurrentUser = Depends(current_user)):
    """大模型不可用期间没读懂的邮件，恢复后点一下重新识别"""
    _need_role(user, "SALES", "MANAGEMENT")
    return {"retried": flows.retry_unread()}


@app.get("/customers")
def customers(user: CurrentUser = Depends(current_user)):
    return _rows([{"id": c["id"], "code": c["code"], "name": c["name"]} for c in erp.customers()])


# ---------------------------------------------------------------- 商机

@app.get("/opportunities")
def opportunities(user: CurrentUser = Depends(current_user)):
    f, args = _owner_filter(user)
    rows = store.query("SELECT o.*, (SELECT COUNT(*) FROM wf_task t WHERE t.opp_id = o.opp_id AND t.status = 'OPEN') open_tasks FROM wf_opportunity o WHERE 1 = 1" + f +
                       " ORDER BY FIELD(o.status, 'LOST', 'WON') , o.updated_at DESC LIMIT 200", args)
    for r in rows:
        r["status_cn"] = flows.STATUS_CN.get(r["status"], r["status"])
    return _rows(rows)


@app.get("/opportunities/{opp_id}")
def opportunity(opp_id: str, user: CurrentUser = Depends(current_user)):
    f, args = _owner_filter(user)
    o = store.one("SELECT o.* FROM wf_opportunity o WHERE o.opp_id = %s" + f, [opp_id, *args])
    if not o:
        raise HTTPException(status_code=404, detail="商机不存在或不是你负责的客户")
    o["status_cn"] = flows.STATUS_CN.get(o["status"])
    quotes = store.query("SELECT quote_no, version, model, tiers, margin, status, valid_until, approver_role, sent_at FROM wf_quote WHERE opp_id = %s ORDER BY quote_no, version", (opp_id,))
    for q in quotes:
        q["tiers"] = store.loads(q["tiers"])
        if user.role == "SALES":
            pass
    orders = store.query("SELECT id, po_no, review_status, erp_order_no, data, issues, created_at FROM wf_sales_order WHERE opp_id = %s", (opp_id,))
    for x in orders:
        x["data"], x["issues"] = store.loads(x["data"]), store.loads(x["issues"])
    history = store.query("SELECT from_status, to_status, event, actor, note, at FROM wf_opp_history WHERE opp_id = %s ORDER BY id", (opp_id,))
    for h in history:
        h["to_cn"] = flows.STATUS_CN.get(h["to_status"], h["to_status"])
    return _rows({"opportunity": o, "history": history, "quotes": quotes, "orders": orders,
                  "mails": store.query("SELECT message_id, subject, from_addr, summary, received_at, status FROM wf_mail WHERE opp_id = %s ORDER BY received_at", (opp_id,)),
                  "outbound": store.query("SELECT action_id, kind, subject, status, adoption, drafted_by, confirmed_by, sent_at, final, draft FROM wf_outbound WHERE opp_id = %s ORDER BY created_at", (opp_id,)),
                  "tasks": store.query("SELECT task_id, type, title, role, status, due_at, done_by, result FROM wf_task WHERE opp_id = %s ORDER BY created_at", (opp_id,)),
                  "negotiations": store.query("SELECT quote_no, round, target_price, strategy, new_price, decided_by, at FROM wf_negotiation WHERE quote_no IN "
                                              "(SELECT quote_no FROM wf_quote WHERE opp_id = %s) ORDER BY quote_no, round", (opp_id,))})


class CloseRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=300)


@app.post("/opportunities/{opp_id}/close")
def opportunity_close(opp_id: str, req: CloseRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES", "MANAGEMENT")
    if not flows.move(opp_id, "LOST", "manual.close", user.username, note=req.reason):
        raise HTTPException(status_code=409, detail="这个商机当前的状态不能关闭")
    store.execute("UPDATE wf_task SET status = 'CANCELLED' WHERE opp_id = %s AND status = 'OPEN'", (opp_id,))
    return {"ok": True}


# ---------------------------------------------------------------- 选型、报价、议价

class SelectionRequest(BaseModel):
    models: list[str | None]


@app.post("/inquiries/{inquiry_id}/confirm")
def inquiry_confirm(inquiry_id: str, req: SelectionRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "ENGINEER")
    _guard(flows.confirm_selection, inquiry_id, req.models, user.username)
    return {"ok": True}


class NonstdRequest(BaseModel):
    decisions: list[dict]


@app.post("/inquiries/{inquiry_id}/nonstd")
def inquiry_nonstd(inquiry_id: str, req: NonstdRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "ENGINEER")
    _guard(flows.confirm_nonstd, inquiry_id, req.decisions, user.username)
    return {"ok": True}


class TiersRequest(BaseModel):
    ref: str
    tiers: list[dict]
    reason: str = ""


@app.post("/quotes/preview")
def quote_preview(req: TiersRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES", "MANAGEMENT")
    return _guard(flows.preview_quote, req.ref, req.tiers)


@app.post("/quotes/submit")
def quote_submit(req: TiersRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES")
    return _guard(flows.submit_quote, req.ref, req.tiers, req.reason, user.username)


class ApproveRequest(BaseModel):
    ref: str
    ok: bool
    note: str = Field(default="", max_length=300)


@app.post("/quotes/approve")
def quote_approve(req: ApproveRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "MANAGEMENT")
    _guard(flows.approve_quote, req.ref, req.ok, req.note, user.username)
    return {"ok": True}


class DecideRequest(BaseModel):
    ref: str
    strategy: str
    price: float | None = None
    condition: str = Field(default="", max_length=300)
    reason: str = Field(default="", max_length=300)


@app.post("/negotiations/decide")
def negotiation_decide(req: DecideRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES")
    _guard(flows.decide_negotiation, req.ref, req.strategy, req.price, req.condition, req.reason, user.username)
    return {"ok": True}


# ---------------------------------------------------------------- 订单评审

class ResolveRequest(BaseModel):
    ref: str
    note: str = Field(min_length=1, max_length=300)
    reject: bool = False


@app.post("/orders/issues/resolve")
def order_issue_resolve(req: ResolveRequest, user: CurrentUser = Depends(current_user)):
    t = store.one("SELECT role FROM wf_task WHERE ref_type = 'order' AND ref_id = %s AND type = 'PO_ISSUE' AND status = 'OPEN'", (req.ref,))
    if not t:
        raise HTTPException(status_code=409, detail="这一项差异已经处理过了")
    _need_role(user, t["role"])
    _guard(flows.resolve_issue, req.ref, req.note, user.username, req.reject)
    return {"ok": True}


class ErpOrderRequest(BaseModel):
    erp_order_no: str = Field(min_length=3, max_length=60)


@app.post("/orders/{order_id}/erp-order")
def order_erp(order_id: int, req: ErpOrderRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "SALES")
    _guard(flows.erp_order_created, order_id, req.erp_order_no, user.username)
    return {"ok": True}


# ---------------------------------------------------------------- 规则和统计

@app.get("/rules")
def rules_list(user: CurrentUser = Depends(current_user)):
    _need_role(user, "MANAGEMENT", "FINANCE", "SALES")
    rows = store.query("SELECT name, version, content, updated_by, updated_at FROM wf_rule WHERE active = 1 ORDER BY name")
    for r in rows:
        r["content"] = store.loads(r["content"])
    return _rows(rows)


class RuleRequest(BaseModel):
    content: dict


@app.put("/rules/{name}")
def rule_update(name: str, req: RuleRequest, user: CurrentUser = Depends(current_user)):
    _need_role(user, "MANAGEMENT", "FINANCE")
    if not store.one("SELECT 1 FROM wf_rule WHERE name = %s LIMIT 1", (name,)):
        raise HTTPException(status_code=404, detail="没有这条规则")
    return {"version": store.set_rule(name, req.content, user.username)}


@app.get("/stats")
def stats(user: CurrentUser = Depends(current_user)):
    """运营数字：邮件分拣情况、起草内容的采纳率、选型推荐的采纳率、各状态的商机数"""
    mails = {r["status"]: r["n"] for r in store.query("SELECT status, COUNT(*) n FROM wf_mail GROUP BY status")}
    adoption = {r["adoption"]: r["n"] for r in store.query("SELECT adoption, COUNT(*) n FROM wf_outbound WHERE adoption IS NOT NULL GROUP BY adoption")}
    by_kind = store.query("SELECT kind, drafted_by, adoption, COUNT(*) n FROM wf_outbound WHERE adoption IS NOT NULL GROUP BY 1, 2, 3")
    sel = store.one("SELECT COUNT(*) n, COALESCE(SUM(adopted), 0) a FROM wf_inquiry WHERE status = 'CONFIRMED'")
    corrected = store.one("SELECT COUNT(DISTINCT message_id) n FROM wf_mail_intent WHERE corrected_by IS NOT NULL")["n"]
    total = sum(adoption.values())
    return _rows({"mails": mails, "mails_total": sum(mails.values()), "mails_corrected": corrected,
                  "adoption": adoption, "adoption_rate": round((adoption.get("AS_IS", 0) + adoption.get("EDITED", 0)) / total, 3) if total else None, "adoption_by_kind": by_kind,
                  "selection": {"confirmed": sel["n"], "adopted": int(sel["a"])},
                  "opportunities": {flows.STATUS_CN.get(r["status"], r["status"]): r["n"] for r in store.query("SELECT status, COUNT(*) n FROM wf_opportunity GROUP BY status")},
                  "tasks_open": store.one("SELECT COUNT(*) n FROM wf_task WHERE status = 'OPEN'")["n"],
                  "tasks_overdue": store.one("SELECT COUNT(*) n FROM wf_task WHERE status = 'OPEN' AND due_at < NOW()")["n"],
                  "erp_write": settings.workflow_erp_write, "mailbox": "IMAP" if settings.workflow_imap_host else f"本地目录 {settings.workflow_mail_dir}"})


@app.get("/meta")
def meta(user: CurrentUser = Depends(current_user)):
    return {"intents": U.INTENT_CN, "status": flows.STATUS_CN, "tasks": flows.TASK_CN, "series": rules.SERIES_CN, "approvers": rules.ROLE_CN}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.app_host, port=settings.workflow_app_port)
