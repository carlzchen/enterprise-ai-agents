"""
流程编排：把邮件接入、理解层、规则层串起来，管商机状态、待办、发件闸门和事件。

  事件          流程之间不直接调用，靠事件交接。事件先写发件箱表（wf_outbox），再由转发器送到 RabbitMQ；队列不可用时在进程内直接处理
  商机状态机    状态只能按 TRANSITIONS 里列出的路径变化，每次变化记一条历史
  发件闸门      所有要发给客户的内容都先变成一条「待外发」和一个待办，人确认（或修改）后才发；发出的内容和原稿都留着
  写 ERP        默认关闭：评审通过后生成「请人工导入」的待办，由人把 ERP 订单号填回来

已经做的流程：W1 邮件分拣、W2 选型、W3 报价和审批、W4 议价、W7 订单评审、W8 里的「客户问进度」。
样品、招标、投诉、供应商回复、发票这几类邮件目前只识别和分派给对应的人，没有后续流程。
"""
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from app.infra.config.settings import settings
from app.platform import alerts, mq
from app.shared.runtime.logger import logger
from app.workflow import adapters, erp, rules, store, understand as U
from app.workflow.adapters.models import EmailDraft

QUEUE = "wf.events"
SENDER = "科朗销售部"
SYNC = False                      # 自测时设为 True：事件不走消息队列，在当前线程里立刻处理

STATUS_CN = {"NEW": "新询价", "CLARIFYING": "需求确认中", "CONFIRMED": "需求已确认", "QUOTED": "已报价", "SAMPLING": "样品中", "SAMPLE_APPROVED": "样品已承认",
             "ORDERED": "已下单", "DELIVERED": "已交付待回款", "WON": "已回款关闭", "LOST": "输单或取消"}
TRANSITIONS = {"NEW": {"CLARIFYING", "CONFIRMED", "LOST"}, "CLARIFYING": {"CONFIRMED", "CLARIFYING", "LOST"}, "CONFIRMED": {"QUOTED", "LOST"},
               "QUOTED": {"QUOTED", "SAMPLING", "ORDERED", "LOST"}, "SAMPLING": {"SAMPLING", "SAMPLE_APPROVED", "LOST"}, "SAMPLE_APPROVED": {"QUOTED", "ORDERED", "LOST"},
               "ORDERED": {"DELIVERED", "ORDERED"}, "DELIVERED": {"WON"}}
TASK_CN = {"TRIAGE": "确认邮件分类", "CLARIFY": "确认澄清邮件", "SELECTION": "确认选型", "NONSTD_REVIEW": "确认非标评估", "QUOTE_REVIEW": "核对报价", "APPROVAL": "审批报价", "SEND": "确认外发",
           "NEGOTIATION": "决定议价策略", "PO_ISSUE": "处理订单差异", "ERP_IMPORT": "在 ERP 里建单", "ASSIGN": "跟进"}
ORDER_STATUS_CN = {"DRAFT": "草稿", "CONFIRMED": "已确认，生产或备货中", "SHIPPED": "已发货", "CANCELLED": "已取消"}
ASSIGN_ONLY = {"tender": ("SALES", "招标通知：请准备投标材料（系统不会代为登录客户平台）"), "sample": ("SALES", "样品相关邮件：请跟进"),
               "complaint": ("QUALITY", "质量投诉：请按客诉流程处理"), "supplier_reply": ("PURCHASE", "供应商回复：请查看报价或交期"), "invoice": ("FINANCE", "发票或对账单：请核对")}


# ---------------------------------------------------------------- 事件

# 【讲解】事件：流程之间不直接调用对方的函数，而是发事件。报价发出后只发一个「报价已发出」，谁关心谁订阅，
# 这样每个流程可以单独开发、单独上线。事件先写进发件箱表 wf_outbox，再由 relay() 送到 RabbitMQ。
def emit(kind: str, payload: dict) -> str:
    eid = "evt_" + uuid.uuid4().hex[:24]
    store.execute("INSERT INTO wf_outbox (event_id, type, payload) VALUES (%s,%s,%s)", (eid, kind, store.dumps(payload)))
    if SYNC:
        relay()
    return eid


def relay(limit: int = 50) -> int:
    """把发件箱里还没发的事件送出去。队列可用就发到 RabbitMQ，由消费线程处理；不可用（或同步模式）就在这里直接处理"""
    rows = store.query("SELECT event_id, type, payload FROM wf_outbox WHERE published_at IS NULL ORDER BY created_at, event_id LIMIT %s", (limit,))
    use_mq = not SYNC and rows and mq.available()
    for r in rows:
        event = {"event_id": r["event_id"], "type": r["type"], "payload": store.loads(r["payload"])}
        store.execute("UPDATE wf_outbox SET published_at = NOW() WHERE event_id = %s AND published_at IS NULL", (r["event_id"],))
        if use_mq:
            mq.publish(QUEUE, event)
        else:
            dispatch(event)
    return len(rows)


# 【讲解】订阅方去重：消息队列可能重复投递，所以按 event_id 插入 wf_event_done，插入成功（第一次见到）才处理；
# 处理失败就删掉这条记录，让它下次还能被处理。
def dispatch(event: dict) -> None:
    """订阅方：按 event_id 去重，同一个事件重复送达只处理一次"""
    if not store.execute("INSERT IGNORE INTO wf_event_done (consumer, event_id) VALUES ('workflow', %s)", (event["event_id"],)):
        return
    handler = HANDLERS.get(event["type"])
    if handler:
        try:
            handler(event["payload"])
        except Exception:
            logger.exception(f"[workflow] 处理事件 {event['type']} 失败")
            store.execute("DELETE FROM wf_event_done WHERE consumer = 'workflow' AND event_id = %s", (event["event_id"],))
            raise


# ---------------------------------------------------------------- 待办、通知、商机

def _notify(title: str, text: str) -> None:
    try:
        alerts.send_feishu(title, text, "P3")
    except Exception:
        pass


def create_task(kind: str, title: str, role: str, *, ref_type: str = "", ref_id: str = "", opp_id: str | None = None, customer: str | None = None,
                payload: dict | None = None, assignee: str | None = None) -> str:
    tid = store.new_id("TK")
    hours = store.rule("sla_hours").get(kind, 24)
    store.execute("INSERT INTO wf_task (task_id, type, title, role, assignee, ref_type, ref_id, opp_id, customer_name, payload, due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (tid, kind, title[:300], role, assignee, ref_type, ref_id, opp_id, customer, store.dumps(payload or {}), datetime.now() + timedelta(hours=hours)))
    _notify(f"[待办] {TASK_CN.get(kind, kind)}：{title[:60]}", f"**客户** {customer or '未识别'}\n**处理角色** {role}\n**时限** {hours} 小时内\n请到「跟单工作台 → 待办中心」处理。")
    return tid


def done_task(task_id: str, user: str, result: str = "") -> None:
    store.execute("UPDATE wf_task SET status = 'DONE', done_by = %s, done_at = NOW(), result = %s WHERE task_id = %s AND status = 'OPEN'", (user, result[:500], task_id))


def _opp(opp_id: str) -> dict:
    return store.one("SELECT * FROM wf_opportunity WHERE opp_id = %s", (opp_id,))


def create_opp(customer_id: int, title: str, status: str = "NEW", event: str = "mail.classified") -> str:
    c = erp.customer(customer_id)
    ext = store.one("SELECT owner FROM wf_customer_ext WHERE customer_id = %s", (customer_id,)) or {"owner": "sales01"}
    n = store.one("SELECT COUNT(*) n FROM wf_opportunity WHERE opp_id LIKE %s", (f"OPP-{date.today():%y%m}-%",))["n"]
    opp_id = f"OPP-{date.today():%y%m}-{n + 1:04d}"
    while _opp(opp_id):
        n += 1
        opp_id = f"OPP-{date.today():%y%m}-{n + 1:04d}"
    store.execute("INSERT INTO wf_opportunity (opp_id, customer_id, customer_name, owner, title, status) VALUES (%s,%s,%s,%s,%s,%s)", (opp_id, customer_id, c["name"], ext["owner"], title[:300], status))
    store.execute("INSERT INTO wf_opp_history (opp_id, from_status, to_status, event, actor) VALUES (%s, NULL, %s, %s, 'system')", (opp_id, status, event))
    return opp_id


# 【讲解】商机状态机：只允许 TRANSITIONS 表里列出的转移（比如「需求确认中」不能直接跳到「已下单」），
# 不合法的转移拒绝并告警；每次转移写一条历史，谁、什么时候、因为哪个事件。
def move(opp_id: str, to: str, event: str, actor: str = "system", note: str = "", next_action: str | None = None, due_days: int | None = None) -> bool:
    """商机状态转移。不在 TRANSITIONS 里的转移一律拒绝并告警"""
    o = _opp(opp_id)
    if not o:
        return False
    if to != o["status"] and to not in TRANSITIONS.get(o["status"], set()):
        logger.warning(f"[workflow] 商机 {opp_id} 不允许从 {o['status']} 变成 {to}（事件 {event}），已拒绝")
        alerts.fire("workflow", "bad_transition", "P3", f"商机 {opp_id} 出现不合法的状态转移", f"{o['status']} → {to}，事件 {event}", f"workflow:transition:{opp_id}:{to}")
        return False
    store.execute("UPDATE wf_opportunity SET status = %s, next_action = COALESCE(%s, next_action), next_due_at = %s, close_reason = %s WHERE opp_id = %s",
                  (to, next_action, datetime.now() + timedelta(days=due_days) if due_days else o["next_due_at"], note if to == "LOST" else o["close_reason"], opp_id))
    store.execute("INSERT INTO wf_opp_history (opp_id, from_status, to_status, event, actor, note) VALUES (%s,%s,%s,%s,%s,%s)", (opp_id, o["status"], to, event, actor, note[:500]))
    return True


# ---------------------------------------------------------------- 发件闸门

# 【讲解】发件闸门：所有要发给客户的内容只有这一个出口。先存成「待外发」并生成待办，人确认后才发；
# 默认所有类型都要确认，规则表 autosend 里允许的类型才会自动发（用于以后逐步放开低风险的动作）。
def create_outbound(kind: str, opp_id: str | None, to_addr: str, subject: str, text: str, drafted_by: str, *, customer: str | None = None, after: dict | None = None,
                    in_reply_to: str | None = None) -> str:
    """要发给外部的内容一律走这里：先存成待外发，再生成一个待办让人确认。autosend 规则里允许的类型才会直接发"""
    aid = store.new_id("OB")
    tid = create_task("CLARIFY" if kind == "CLARIFY" else "SEND", f"{ {'CLARIFY': '澄清邮件', 'QUOTE': '报价邮件', 'COUNTER_REPLY': '议价回复', 'ORDER_CONFIRM': '订单确认书', 'DELIVERY_REPLY': '进度回复'}.get(kind, kind)}：{subject[:80]}",
                      "SALES", ref_type="outbound", ref_id=aid, opp_id=opp_id, customer=customer, payload={"after": after or {}, "in_reply_to": in_reply_to})
    store.execute("INSERT INTO wf_outbound (action_id, task_id, kind, opp_id, to_addr, subject, draft, drafted_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (aid, tid, kind, opp_id, to_addr, subject[:500], text, drafted_by))
    if kind in store.rule("autosend").get("allowed", []):
        confirm_outbound(aid, text, "system（自动外发）")
    return aid


# 【讲解】确认发送：只处理 PENDING 状态的，所以重复点确认、重复回调都不会发第二封。
# 比较终稿和原稿，记下是「直接采用」还是「修改后采用」——这就是采纳率指标的来源。发出后触发后续动作（_after_send）。
def confirm_outbound(action_id: str, final: str, user: str) -> dict:
    ob = store.one("SELECT * FROM wf_outbound WHERE action_id = %s", (action_id,))
    if not ob or ob["status"] != "PENDING":                   # 重复点击、重复回调：不会发第二封
        return {"sent": False, "reason": "已经处理过了"}
    final = (final or "").strip() or ob["draft"]
    task = store.one("SELECT payload FROM wf_task WHERE task_id = %s", (ob["task_id"],))
    meta = store.loads(task["payload"]) if task else {}
    receipt = adapters.email().send(EmailDraft(to=[ob["to_addr"]], subject=ob["subject"], body=final, in_reply_to=meta.get("in_reply_to"),
                                               from_mailbox=f"{SENDER} <sales@kelang.example.com>", ref=action_id))   # Mock：存成 eml；Real：Graph / SMTP
    adoption = "AS_IS" if final == ob["draft"].strip() else "EDITED"
    store.execute("UPDATE wf_outbound SET status = 'SENT', final = %s, adoption = %s, confirmed_by = %s, sent_at = NOW(), sent_path = %s WHERE action_id = %s AND status = 'PENDING'",
                  (final, adoption, user, receipt.location, action_id))
    done_task(ob["task_id"], user, "直接采用" if adoption == "AS_IS" else "修改后采用")
    store.audit(user, "outbound.send", f"outbound:{action_id}", {"kind": ob["kind"], "adoption": adoption, "to": ob["to_addr"]})
    _after_send(ob["kind"], ob["opp_id"], meta.get("after") or {}, user)
    return {"sent": True, "adoption": adoption, "message_id": receipt.message_id}


def discard_outbound(action_id: str, user: str, reason: str = "") -> None:
    ob = store.one("SELECT task_id FROM wf_outbound WHERE action_id = %s AND status = 'PENDING'", (action_id,))
    if ob:
        store.execute("UPDATE wf_outbound SET status = 'DISCARDED', adoption = 'DISCARDED', confirmed_by = %s WHERE action_id = %s", (user, action_id))
        done_task(ob["task_id"], user, "弃用：" + reason)
        store.audit(user, "outbound.discard", f"outbound:{action_id}", {"reason": reason})


def _after_send(kind: str, opp_id: str | None, after: dict, user: str) -> None:
    if kind == "CLARIFY" and opp_id:
        move(opp_id, "CLARIFYING", "clarify.sent", user, next_action="等客户补充参数", due_days=3)
    elif kind in ("QUOTE", "COUNTER_REPLY") and after.get("quote_no"):
        q = store.one("SELECT valid_until FROM wf_quote WHERE quote_no = %s AND version = %s", (after["quote_no"], after["version"]))
        store.execute("UPDATE wf_quote SET status = 'SUPERSEDED' WHERE quote_no = %s AND version < %s AND status = 'SENT'", (after["quote_no"], after["version"]))
        store.execute("UPDATE wf_quote SET status = 'SENT', sent_at = NOW() WHERE quote_no = %s AND version = %s", (after["quote_no"], after["version"]))
        move(opp_id, "QUOTED", "quote.sent", user, next_action=f"等客户回复，报价 {after['quote_no']} 有效期到 {q['valid_until']}", due_days=7)
        emit("quote.sent", {"opp_id": opp_id, "quote_no": after["quote_no"], "version": after["version"]})
    elif kind == "COUNTER_REPLY" and after.get("lost") and opp_id:
        move(opp_id, "LOST", "negotiation.declined", user, note=after.get("reason") or "价格谈不拢")


# ---------------------------------------------------------------- W1 邮件分拣

def _mail(message_id: str) -> dict:
    m = store.one("SELECT * FROM wf_mail WHERE message_id = %s", (message_id,))
    m["attachments"] = store.loads(m["attachments"]) or []
    return m


def _reply_to(m: dict) -> str:
    return m["real_from"] or m["from_addr"]


# 【讲解】W1 分拣的核心：调模型读邮件 → 存意图 → 判断要不要人先看一眼 → 没问题就按意图派发。
# 进人工待确认的几种情况：认不出发件人、分类把握低于阈值、PO 关键字段没读全、供应商发来像客户的业务。
# 模型挂了也不丢邮件：直接进待确认队列，恢复后可以重新识别（retry_unread）。
def triage(payload: dict) -> None:
    m = _mail(payload["message_id"])
    if m["status"] != "NEW":
        return
    reasons = []
    try:
        a = U.analyze(m)
    except Exception as e:
        store.execute("UPDATE wf_mail SET status = 'PENDING', pending_reason = %s WHERE message_id = %s", (f"系统没能读懂这封邮件（{str(e)[:120]}）", m["message_id"]))
        create_task("TRIAGE", f"请人工分类：{m['subject'][:80]}", "SALES", ref_type="mail", ref_id=m["message_id"], customer=m["party_name"])
        return
    for i in a.intents:
        fields = {"new_inquiry": [x.model_dump() for x in a.inquiry], "counter_offer": a.counter.model_dump() if a.counter else {}, "purchase_order": a.po.model_dump(mode="json") if a.po else {},
                  "delivery_query": a.delivery.model_dump(mode="json") if a.delivery else {}}.get(i.intent, {})
        store.execute("INSERT INTO wf_mail_intent (message_id, intent, confidence, extracted) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE confidence = VALUES(confidence), extracted = VALUES(extracted)",
                      (m["message_id"], i.intent, i.confidence, store.dumps(fields)))
    kinds = {i.intent: i.confidence for i in a.intents}
    real = {k: v for k, v in kinds.items() if k != "other"}
    opp_id = _opp_of_thread(m["thread_id"])
    owner = (store.one("SELECT owner FROM wf_customer_ext WHERE customer_id = %s", (m["party_id"],)) or {}).get("owner") if m["party_type"] == "CUSTOMER" else None
    store.execute("UPDATE wf_mail SET summary = %s, opp_id = %s, owner = %s WHERE message_id = %s", (a.summary[:500], opp_id, owner, m["message_id"]))
    if not real:
        store.execute("UPDATE wf_mail SET status = 'ARCHIVED', triaged_at = NOW() WHERE message_id = %s", (m["message_id"],))
        return
    # 哪些情况要人先看一眼：认不出发件人、把握不大、关键字段不全
    if m["party_type"] == "UNKNOWN":
        reasons.append(f"认不出发件人 {_reply_to(m)} 是哪个客户或供应商")
    low = [U.INTENT_CN[k] for k, v in real.items() if v < settings.workflow_confidence]
    if low:
        reasons.append("对这些分类把握不大：" + "、".join(low))
    if "purchase_order" in real and not (a.po and a.po.po_no and a.po.lines and all(l.model and l.qty for l in a.po.lines)):
        reasons.append("识别为采购订单，但没有读全 PO 号、型号或数量")
    if "new_inquiry" in real and not a.inquiry:
        reasons.append("识别为询价，但没有读出具体需求")
    if m["party_type"] == "SUPPLIER" and real.keys() & {"new_inquiry", "counter_offer", "purchase_order", "delivery_query"}:
        reasons.append("发件人是供应商，但内容像是客户的业务，请确认")
    if reasons:
        store.execute("UPDATE wf_mail SET status = 'PENDING', pending_reason = %s WHERE message_id = %s", ("；".join(reasons)[:300], m["message_id"]))
        create_task("TRIAGE", f"请确认邮件：{m['subject'][:80]}", "SALES", ref_type="mail", ref_id=m["message_id"], customer=m["party_name"], payload={"reasons": reasons})
        return
    _dispatch_mail(m["message_id"])


def _dispatch_mail(message_id: str) -> None:
    store.execute("UPDATE wf_mail SET status = 'TRIAGED', triaged_at = NOW(), pending_reason = NULL WHERE message_id = %s", (message_id,))
    kinds = {r["intent"] for r in store.query("SELECT intent FROM wf_mail_intent WHERE message_id = %s", (message_id,))}
    for r in store.query("SELECT intent FROM wf_mail_intent WHERE message_id = %s AND dispatched = 0 AND intent <> 'other'", (message_id,)):
        if r["intent"] == "delivery_query" and "purchase_order" in kinds:      # 新下的 PO 里问交期：订单评审里会核对交期，不另走「问进度」
            continue
        store.execute("UPDATE wf_mail_intent SET dispatched = 1 WHERE message_id = %s AND intent = %s", (message_id, r["intent"]))
        emit("mail.classified", {"message_id": message_id, "intent": r["intent"]})


def correct_mail(message_id: str, user: str, *, intents: list[str] | None = None, customer_id: int | None = None, fields: dict | None = None, archive: bool = False) -> None:
    """人工修正分拣结果。系统原来的判断留在 original 里，修正记录以后进评测集"""
    m = _mail(message_id)
    if customer_id:
        c = erp.customer(customer_id)
        store.execute("UPDATE wf_mail SET party_type = 'CUSTOMER', party_id = %s, party_name = %s WHERE message_id = %s", (customer_id, c["name"], message_id))
    old = {r["intent"]: r for r in store.query("SELECT intent, confidence, extracted FROM wf_mail_intent WHERE message_id = %s", (message_id,))}
    if intents is not None:
        for k in set(old) - set(intents):
            store.execute("DELETE FROM wf_mail_intent WHERE message_id = %s AND intent = %s", (message_id, k))
        for k in intents:
            store.execute("INSERT INTO wf_mail_intent (message_id, intent, confidence, extracted, original, corrected_by) VALUES (%s,%s,1,%s,%s,%s) "
                          "ON DUPLICATE KEY UPDATE confidence = 1, corrected_by = VALUES(corrected_by), original = COALESCE(original, VALUES(original))",
                          (message_id, k, store.dumps(store.loads(old[k]["extracted"]) if k in old else {}), store.dumps({"intents": {x: float(old[x]["confidence"]) for x in old}}), user))
    for k, v in (fields or {}).items():
        row = store.one("SELECT extracted, original FROM wf_mail_intent WHERE message_id = %s AND intent = %s", (message_id, k))
        if row:
            store.execute("UPDATE wf_mail_intent SET extracted = %s, original = COALESCE(original, %s), corrected_by = %s WHERE message_id = %s AND intent = %s",
                          (store.dumps(v), store.dumps({"extracted": store.loads(row["extracted"])}), user, message_id, k))
    store.audit(user, "mail.correct", f"mail:{message_id}", {"intents": intents, "customer_id": customer_id, "fields": list((fields or {}).keys()), "archive": archive})
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'mail' AND ref_id = %s AND type = 'TRIAGE' AND status = 'OPEN'", (message_id,)):
        done_task(t["task_id"], user, "已人工确认")
    if archive:
        store.execute("UPDATE wf_mail SET status = 'ARCHIVED', triaged_at = NOW() WHERE message_id = %s", (message_id,))
        return
    if _mail(message_id)["party_type"] == "UNKNOWN":
        raise ValueError("还没有指定这封邮件属于哪个客户")
    _dispatch_mail(message_id)


def _opp_of_thread(thread_id: str) -> str | None:
    row = store.one("SELECT opp_id FROM wf_mail WHERE thread_id = %s AND opp_id IS NOT NULL ORDER BY received_at DESC LIMIT 1", (thread_id,))
    return row["opp_id"] if row else None


def on_classified(payload: dict) -> None:
    m, intent = _mail(payload["message_id"]), payload["intent"]
    row = store.one("SELECT extracted FROM wf_mail_intent WHERE message_id = %s AND intent = %s", (m["message_id"], intent))
    data = store.loads(row["extracted"]) if row else {}
    if intent == "new_inquiry":
        handle_inquiry(m, data or [])
    elif intent == "counter_offer":
        handle_counter(m, data or {})
    elif intent == "purchase_order":
        handle_po(m, data or {})
    elif intent == "delivery_query":
        handle_delivery_query(m, data or {})
    elif intent in ASSIGN_ONLY:
        role, title = ASSIGN_ONLY[intent]
        create_task("ASSIGN", f"{title}｜{m['subject'][:60]}", role, ref_type="mail", ref_id=m["message_id"], opp_id=m["opp_id"], customer=m["party_name"], payload={"summary": m["summary"]})


# ---------------------------------------------------------------- W2 询价理解与选型

def _contact(m: dict) -> str:
    return m["from_name"] or (erp.primary_contact(m["party_id"]) or {}).get("name") or ""


# 【讲解】W2：缺必填参数 → 起草澄清邮件（客户补充后会并进同一个商机继续）；参数齐 → 规则选型；
# 完全匹配 → 工程师确认后报价；近似匹配或无匹配 → 交给多 Agent 非标评估（_start_nonstd）。
def handle_inquiry(m: dict, items: list[dict]) -> None:
    items = [U.normalize_item(i) for i in items]
    opp_id = m["opp_id"]
    inq = store.one("SELECT * FROM wf_inquiry WHERE opp_id = %s AND status = 'CLARIFYING' ORDER BY created_at DESC LIMIT 1", (opp_id,)) if opp_id else None
    if inq:                                                   # 客户在补充之前缺的参数：把新读到的值并进原来的需求
        old = store.loads(inq["items"])
        for i, new in enumerate(items[:len(old)] or []):
            old[i] = {**old[i], **{k: v for k, v in new.items() if v not in (None, "")}}
        if len(old) == 1 and len(items) == 1:
            pass
        items, inquiry_id = old, inq["inquiry_id"]
    else:
        if not opp_id or _opp(opp_id)["status"] in ("ORDERED", "DELIVERED", "WON", "LOST"):
            first = items[0] if items else {}
            opp_id = create_opp(m["party_id"], f"{rules.SERIES_CN.get(first.get('series'), first.get('product_type') or '电机')}询价｜{m['subject'][:60]}")
        inquiry_id = store.new_id("INQ")
        store.execute("INSERT INTO wf_inquiry (inquiry_id, opp_id, message_id, items, status) VALUES (%s,%s,%s,%s,'SELECTING')", (inquiry_id, opp_id, m["message_id"], store.dumps(items)))
    store.execute("UPDATE wf_mail SET opp_id = %s WHERE message_id = %s", (opp_id, m["message_id"]))
    required = store.rule("required_params")
    missing = [{"index": i, "params": rules.missing_params(it, required)} for i, it in enumerate(items)]
    missing = [x for x in missing if x["params"]]
    if missing:
        names = sorted({("产品类型（伺服、步进、无刷等）" if p == "series" else rules.PARAM_CN.get(p, p)) for x in missing for p in x["params"]})
        en = {"额定电压": "rated voltage", "额定功率": "rated power", "额定转速": "rated speed", "额定转矩": "rated torque", "机座号或外形尺寸": "frame size / dimensions",
              "年用量": "estimated annual volume", "产品类型（伺服、步进、无刷等）": "motor type (servo / stepper / BLDC ...)"}
        facts = {"contact": _contact(m), "sender": SENDER, "missing": names if m["lang"] != "en" else [en.get(n, n) for n in names]}
        text, by = U.draft("CLARIFY", facts, m["lang"], m["body_new"])
        store.execute("UPDATE wf_inquiry SET items = %s, missing = %s, status = 'CLARIFYING' WHERE inquiry_id = %s", (store.dumps(items), store.dumps(missing), inquiry_id))
        create_outbound("CLARIFY", opp_id, _reply_to(m), "Re: " + m["subject"], text, by, customer=m["party_name"], in_reply_to=m["message_id"])
        return
    products, tol = erp.products(), store.rule("selection")
    selection = [rules.select(it, products, erp.product_params, tol) for it in items]
    for it, sel in zip(items, selection):                 # 标准参数都满足、但客户还有定制要求（轴伸、出线、包装……）：不能当标准品，按近似匹配走非标评估
        if sel["grade"] == "EXACT" and it.get("special_requirements"):
            sel.update(grade="NEAR", by=sel["by"] + "；客户有定制要求：" + "；".join(it["special_requirements"]))
    all_none = all(s["grade"] == "NONE" for s in selection)
    nonstd = [i for i, s in enumerate(selection) if s["grade"] != "EXACT"]
    store.execute("UPDATE wf_inquiry SET items = %s, missing = NULL, selection = %s, status = %s WHERE inquiry_id = %s",
                  (store.dumps(items), store.dumps(selection), "EVALUATING" if nonstd and settings.workflow_agents else "NO_MATCH" if all_none else "SELECTING", inquiry_id))
    if _opp(opp_id)["status"] == "CLARIFYING":
        move(opp_id, "CLARIFYING", "requirement.completed", note="客户已补齐参数", next_action="等工程师确认选型", due_days=1)
    if nonstd and settings.workflow_agents:              # 有标准品接不住的需求：先交给多 Agent 评估，评估完再给工程师确认
        _start_nonstd(inquiry_id, opp_id, m["party_id"], m["party_name"], items, selection, nonstd)
        return
    if all_none:
        create_task("ASSIGN", f"没有匹配的标准品，请研发评估｜{m['subject'][:60]}", "ENGINEER", ref_type="inquiry", ref_id=inquiry_id, opp_id=opp_id, customer=m["party_name"],
                    payload={"note": "需要新开发：开发费和模具费要单独核算，报价周期会比较长"})
    else:
        create_task("SELECTION", f"请确认选型｜{m['party_name']}｜{'、'.join(s['candidates'][0]['model'] for s in selection if s['candidates'])}", "ENGINEER",
                    ref_type="inquiry", ref_id=inquiry_id, opp_id=opp_id, customer=m["party_name"])


def _start_nonstd(inquiry_id: str, opp_id: str, customer_id: int, customer: str, items: list[dict], selection: list[dict], idx: list[int]) -> None:
    """非标评估比较慢（多个 Agent、几十次工具调用），放到后台线程跑；跑完生成一个「确认非标评估」的待办"""
    from app.workflow.agents import nonstd

    def work():
        ids = [nonstd.start(inquiry_id, i, opp_id, items[i], customer_id, selection[i]) for i in idx]
        done = store.query("SELECT eval_id FROM wf_nonstd_eval WHERE eval_id IN %s AND status = 'DONE'", (ids,))
        if not done:                                      # 全部失败（比如模型不可用）：退回原来的做法，直接交给工程师
            store.execute("UPDATE wf_inquiry SET status = 'SELECTING' WHERE inquiry_id = %s", (inquiry_id,))
            create_task("SELECTION", f"非标评估没有完成，请人工选型｜{customer}", "ENGINEER", ref_type="inquiry", ref_id=inquiry_id, opp_id=opp_id, customer=customer)
            return
        create_task("NONSTD_REVIEW", f"请确认非标评估｜{customer}｜{len(idx)} 项需要改型或新开发", "ENGINEER", ref_type="inquiry", ref_id=inquiry_id, opp_id=opp_id,
                    customer=customer, payload={"evals": ids})
    if SYNC:
        work()
    else:
        threading.Thread(target=work, daemon=True, name="wf-nonstd").start()


def confirm_nonstd(inquiry_id: str, decisions: list[dict], user: str) -> None:
    """
    工程师确认非标评估。decisions 每一项对应一项需求：{"accept": true/false, "model": 型号, "note": ""}
      标准品（完全匹配）的项：model 就是确认的型号
      非标的项：accept=true 表示按评估报告的改型方案报价（用报告里的估算成本走正式定价和审批）；false 表示这一项不报
    """
    inq = store.one("SELECT * FROM wf_inquiry WHERE inquiry_id = %s", (inquiry_id,))
    if not inq or inq["status"] != "EVALUATING":
        raise ValueError("这条询价当前不在等待确认非标评估的状态")
    items, selection = store.loads(inq["items"]), store.loads(inq["selection"])
    if len(decisions) != len(items):
        raise ValueError("每一项需求都要给出结论")
    evals = {r["item_index"]: r for r in store.query("SELECT eval_id, item_index, status, report FROM wf_nonstd_eval WHERE inquiry_id = %s ORDER BY created_at", (inquiry_id,))}
    payloads = []
    for i, (it, d) in enumerate(zip(items, decisions)):
        ev = evals.get(i)
        if ev:
            store.execute("UPDATE wf_nonstd_eval SET decision = %s, decided_by = %s, decided_at = NOW() WHERE eval_id = %s",
                          ("ACCEPT" if d.get("accept") else "REJECT", user, ev["eval_id"]))
        if not d.get("accept"):
            it["confirmed_model"] = None
            continue
        if ev and ev["status"] == "DONE":
            rep = store.loads(ev["report"])
            if not rep.get("cost") or not rep.get("tech") or not rep["tech"].get("base_model"):
                raise ValueError(f"第 {i + 1} 项的评估报告不完整（可能需要新开发），不能直接报价，请转研发")
            it["confirmed_model"] = rep["tech"]["base_model"]
            payloads.append({"model": it["confirmed_model"], "annual_volume": it.get("annual_volume"), "target_price": it.get("target_price"),
                             "unit_cost_override": rep["cost"]["unit_cost"] + ((rep.get("commercial") or {}).get("amortize") or {}).get("per_unit", 0),
                             "nonstd": {"eval_id": ev["eval_id"], "changes": rep["tech"]["changes"], "nre": rep["cost"].get("nre"), "lead": rep.get("lead"),
                                        "nre_policy": (rep.get("commercial") or {}).get("nre_policy")}})
        else:
            it["confirmed_model"] = d.get("model")
            if it["confirmed_model"]:
                payloads.append({"model": it["confirmed_model"], "annual_volume": it.get("annual_volume"), "target_price": it.get("target_price")})
    adopted = all(d.get("accept") for d in decisions)
    store.execute("UPDATE wf_inquiry SET items = %s, status = 'CONFIRMED', confirmed_by = %s, adopted = %s WHERE inquiry_id = %s", (store.dumps(items), user, 1 if adopted else 0, inquiry_id))
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'inquiry' AND ref_id = %s AND type = 'NONSTD_REVIEW' AND status = 'OPEN'", (inquiry_id,)):
        done_task(t["task_id"], user, "采纳评估结论" if adopted else "部分采纳")
    store.audit(user, "nonstd.confirm", f"inquiry:{inquiry_id}", {"decisions": decisions})
    if not payloads:
        create_task("ASSIGN", "非标评估全部不采纳，请转研发评估或回复客户", "ENGINEER", ref_type="inquiry", ref_id=inquiry_id, opp_id=inq["opp_id"])
        return
    move(inq["opp_id"], "CONFIRMED", "requirement.confirmed", user, next_action="出报价", due_days=1)
    for p in payloads:
        emit("requirement.confirmed", {"opp_id": inq["opp_id"], "inquiry_id": inquiry_id, **p})


def confirm_selection(inquiry_id: str, models: list[str | None], user: str) -> None:
    """工程师确认每一项需求最终选哪个型号（None 表示这一项没有合适的）"""
    inq = store.one("SELECT * FROM wf_inquiry WHERE inquiry_id = %s", (inquiry_id,))
    if not inq or inq["status"] != "SELECTING":
        raise ValueError("这条询价当前不在等待确认选型的状态")
    items, selection = store.loads(inq["items"]), store.loads(inq["selection"])
    if len(models) != len(items):
        raise ValueError("每一项需求都要给出结论")
    known = {p["code"] for p in erp.products()}
    for mdl in models:
        if mdl and mdl not in known:
            raise ValueError(f"型号 {mdl} 不存在")
    adopted = all((mdl or None) == (s["candidates"][0]["model"] if s["candidates"] and s["grade"] != "NONE" else None) for mdl, s in zip(models, selection))
    for it, mdl in zip(items, models):
        it["confirmed_model"] = mdl
    store.execute("UPDATE wf_inquiry SET items = %s, status = 'CONFIRMED', confirmed_by = %s, adopted = %s WHERE inquiry_id = %s", (store.dumps(items), user, 1 if adopted else 0, inquiry_id))
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'inquiry' AND ref_id = %s AND type = 'SELECTION' AND status = 'OPEN'", (inquiry_id,)):
        done_task(t["task_id"], user, "采纳系统推荐" if adopted else "改选了其他型号")
    store.audit(user, "selection.confirm", f"inquiry:{inquiry_id}", {"models": models, "adopted": adopted})
    move(inq["opp_id"], "CONFIRMED", "requirement.confirmed", user, next_action="出报价", due_days=1)
    for it in items:
        if it.get("confirmed_model"):
            emit("requirement.confirmed", {"opp_id": inq["opp_id"], "inquiry_id": inquiry_id, "model": it["confirmed_model"], "annual_volume": it.get("annual_volume"), "target_price": it.get("target_price")})


# ---------------------------------------------------------------- W3 报价

def _level(customer_id: int) -> str:
    ext = store.one("SELECT level FROM wf_customer_ext WHERE customer_id = %s", (customer_id,))
    return ext["level"] if ext else "NEW"


# 【讲解】W3 出报价：取 BOM 成本（非标的项用评估报告里程序算出的估算成本）→ 规则定价 → 存报价草稿和成本快照 → 给销售一个核对的待办。
# 成本快照记下当时用的规则版本、物料价格月份、历史成交价，以后查「当时为什么报这个价」靠它。
def start_quote(payload: dict) -> None:
    opp, model = _opp(payload["opp_id"]), payload["model"]
    cost = erp.cost_inputs(model)
    if payload.get("unit_cost_override") is not None:      # 非标改型：用评估报告里估算的单位成本（已经由程序按改动清单算好），照样走定价规则和审批
        cost = {**cost, "lines": [], "missing": [], "conversion_cost": float(payload["unit_cost_override"]), "estimated_from_nonstd": True}
    try:
        priced = rules.price_quote(cost, _level(opp["customer_id"]), store.rule("margin"), store.rule("tiers"))
    except ValueError as e:
        create_task("ASSIGN", f"{model} 算不出成本，请补物料价格", "PURCHASE", ref_type="opp", ref_id=opp["opp_id"], opp_id=opp["opp_id"], customer=opp["customer_name"], payload={"note": str(e)})
        return
    n = store.one("SELECT COUNT(DISTINCT quote_no) n FROM wf_quote WHERE quote_no LIKE %s", (f"Q-{date.today():%y%m}-%",))["n"]
    quote_no = f"Q-{date.today():%y%m}-{n + 1:04d}"
    snapshot = {"cost_inputs": cost, "cost": priced["cost"], "level": priced["level"], "target_margin": priced["target_margin"], "min_margin": priced["min_margin"],
                "rule_versions": {"margin": store.rule("margin")["_version"], "tiers": store.rule("tiers")["_version"]}, "priced_at": datetime.now().isoformat(timespec="seconds"),
                "last_deal": erp.last_deal(opp["customer_id"], model), "deal_range": erp.deal_range(model), "system_tiers": priced["tiers"], "rule_tiers": priced["tiers"], "inquiry_id": payload.get("inquiry_id"),
                "customer_target_price": payload.get("target_price"), "nonstd": payload.get("nonstd")}
    q = store.rule("quote")
    store.execute("INSERT INTO wf_quote (quote_no, version, opp_id, customer_id, model, annual_volume, tiers, unit_cost, floor_price, margin, snapshot, status, valid_until, created_by) "
                  "VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s,'system')",
                  (quote_no, opp["opp_id"], opp["customer_id"], model, payload.get("annual_volume"), store.dumps(priced["tiers"]), priced["cost"]["total"], priced["floor_price"], priced["margin"],
                   store.dumps(snapshot), date.today() + timedelta(days=q["valid_days"])))
    create_task("QUOTE_REVIEW", f"请核对报价 {quote_no}｜{model}", "SALES", ref_type="quote", ref_id=f"{quote_no}#1", opp_id=opp["opp_id"], customer=opp["customer_name"])


def _quote(ref: str) -> dict:
    no, ver = ref.split("#")
    q = store.one("SELECT * FROM wf_quote WHERE quote_no = %s AND version = %s", (no, int(ver)))
    if not q:
        raise ValueError("报价不存在")
    q["tiers"], q["snapshot"] = store.loads(q["tiers"]), store.loads(q["snapshot"])
    return q


def quote_view(ref: str, role: str) -> dict:
    """报价详情。成本构成只给管理层和财务看；底价销售能看到（谈判要用）"""
    q = _quote(ref)
    snap = q.pop("snapshot")
    out = {**q, "unit_cost": None, "floor_price": float(q["floor_price"]), "nonstd": snap.get("nonstd"), "margin": float(q["margin"]), "target_margin": snap["target_margin"], "min_margin": snap["min_margin"],
           "level": snap["level"], "last_deal": snap.get("last_deal"), "deal_range": snap.get("deal_range"), "system_tiers": snap.get("system_tiers"),
           "customer_target_price": snap.get("customer_target_price"), "origin": snap.get("origin"), "condition": snap.get("condition"),
           "approver": rules.ROLE_CN.get(q["approver_role"] or "", q["approver_role"])}
    if role in ("ADMIN", "MANAGEMENT", "FINANCE"):
        out["unit_cost"] = float(q["unit_cost"])
        out["cost"] = snap["cost"]
        out["cost_lines"] = snap["cost_inputs"]["lines"]
        out["rule_versions"] = snap["rule_versions"]
    return out


def preview_quote(ref: str, tiers: list[dict]) -> dict:
    """销售改价时实时重算：每一档的毛利率、需要谁审批"""
    q = _quote(ref)
    new = rules.reprice(tiers, float(q["unit_cost"]))
    lowest = min(t["margin"] for t in new)
    role = rules.approval_for(new, q["snapshot"].get("rule_tiers") or q["snapshot"]["system_tiers"], q["snapshot"]["min_margin"], store.rule("approval"))
    return {"tiers": new, "lowest_margin": lowest, "approver_role": role, "approver": rules.ROLE_CN[role], "below_floor": lowest < q["snapshot"]["min_margin"] - 1e-9}


def submit_quote(ref: str, tiers: list[dict], reason: str, user: str) -> dict:
    q = _quote(ref)
    if q["status"] not in ("DRAFT", "REJECTED"):
        raise ValueError("这份报价已经提交过了")
    p = preview_quote(ref, tiers)
    if p["below_floor"] and not (reason or "").strip():
        raise ValueError("毛利率低于最低毛利率，必须填写理由")
    store.execute("UPDATE wf_quote SET tiers = %s, margin = %s, status = 'APPROVING', approver_role = %s, reason = %s, created_by = %s WHERE quote_no = %s AND version = %s",
                  (store.dumps(p["tiers"]), p["tiers"][0]["margin"], p["approver_role"], (reason or "")[:500], user, q["quote_no"], q["version"]))
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'quote' AND ref_id = %s AND type = 'QUOTE_REVIEW' AND status = 'OPEN'", (ref,)):
        changed = [t2["unit_price"] for t2 in p["tiers"]] != [t2["unit_price"] for t2 in q["snapshot"].get("system_tiers", [])]
        done_task(t["task_id"], user, "调整了价格" if changed else "采用系统算出的价格")
    opp = _opp(q["opp_id"])
    create_task("APPROVAL", f"报价 {q['quote_no']} 待{p['approver']}审批｜{q['model']}｜最低毛利率 {p['lowest_margin']:.1%}", "MANAGEMENT", ref_type="quote", ref_id=ref,
                opp_id=q["opp_id"], customer=opp["customer_name"], payload={"approver_role": p["approver_role"]})
    store.audit(user, "quote.submit", f"quote:{ref}", {"tiers": p["tiers"], "approver_role": p["approver_role"], "reason": reason})
    return p


def approve_quote(ref: str, ok: bool, note: str, user: str) -> None:
    q = _quote(ref)
    if q["status"] != "APPROVING":
        raise ValueError("这份报价当前不在审批中")
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'quote' AND ref_id = %s AND type = 'APPROVAL' AND status = 'OPEN'", (ref,)):
        done_task(t["task_id"], user, ("通过" if ok else "驳回") + ("：" + note if note else ""))
    store.audit(user, "quote.approve" if ok else "quote.reject", f"quote:{ref}", {"note": note})
    opp = _opp(q["opp_id"])
    if not ok:
        store.execute("UPDATE wf_quote SET status = 'REJECTED' WHERE quote_no = %s AND version = %s", (q["quote_no"], q["version"]))
        create_task("QUOTE_REVIEW", f"报价 {q['quote_no']} 被驳回，请修改｜{note[:60]}", "SALES", ref_type="quote", ref_id=ref, opp_id=q["opp_id"], customer=opp["customer_name"])
        return
    store.execute("UPDATE wf_quote SET status = 'APPROVED', approved_by = %s WHERE quote_no = %s AND version = %s", (user, q["quote_no"], q["version"]))
    _draft_quote_mail(q, opp)


def _draft_quote_mail(q: dict, opp: dict) -> None:
    snap = q["snapshot"]
    mail = store.one("SELECT * FROM wf_mail WHERE opp_id = %s AND party_type = 'CUSTOMER' ORDER BY received_at DESC LIMIT 1", (opp["opp_id"],))
    contact = erp.primary_contact(opp["customer_id"]) or {}
    to, lang = (_reply_to(mail) if mail else contact.get("email", "")), (mail["lang"] if mail else "zh")
    p, c = erp.product(q["model"]), erp.customer(opp["customer_id"])
    tiers = [{"qty": t["qty"], "unit_price": t["unit_price"]} for t in q["tiers"]]
    after = {"quote_no": q["quote_no"], "version": q["version"]}
    if snap.get("origin") == "negotiation":
        facts = {"contact": (mail or {}).get("from_name") or contact.get("name"), "sender": SENDER, "model": q["model"], "new_price": tiers[0]["unit_price"], "quote_no": q["quote_no"],
                 "valid_until": str(q["valid_until"]), "condition": snap.get("condition") or ""}
        text, by = U.draft("COUNTER_REPLY", facts, lang, (mail or {}).get("body_new", ""))
        create_outbound("COUNTER_REPLY", opp["opp_id"], to, "Re: " + (mail["subject"] if mail else f"报价 {q['quote_no']}"), text, by, customer=opp["customer_name"], after=after,
                        in_reply_to=(mail or {}).get("message_id"))
        return
    facts = {"contact": (mail or {}).get("from_name") or contact.get("name"), "sender": SENDER, "model": q["model"], "model_name": p["name"], "tiers": tiers, "quote_no": q["quote_no"],
             "valid_until": str(q["valid_until"]), "payment_days": c["payment_terms_days"]}
    text, by = U.draft("QUOTE", facts, lang, (mail or {}).get("body_new", ""))
    create_outbound("QUOTE", opp["opp_id"], to, "Re: " + (mail["subject"] if mail else f"报价 {q['quote_no']}"), text, by, customer=opp["customer_name"], after=after,
                    in_reply_to=(mail or {}).get("message_id"))


def remind_expiring_quotes(today: date | None = None) -> int:
    """报价快到期而客户还没回复：给销售一个跟进的待办。每份报价只提醒一次"""
    today, n = today or date.today(), 0
    days = store.rule("quote")["remind_days_before"]
    for q in store.query("SELECT q.quote_no, q.version, q.model, q.valid_until, q.opp_id, o.customer_name FROM wf_quote q JOIN wf_opportunity o ON o.opp_id = q.opp_id "
                         "WHERE q.status = 'SENT' AND q.reminded = 0 AND o.status = 'QUOTED' AND q.valid_until <= %s", (today + timedelta(days=days),)):
        store.execute("UPDATE wf_quote SET reminded = 1 WHERE quote_no = %s AND version = %s", (q["quote_no"], q["version"]))
        create_task("ASSIGN", f"报价 {q['quote_no']} 将在 {q['valid_until']} 到期，客户还没回复，请跟进", "SALES", ref_type="quote", ref_id=f"{q['quote_no']}#{q['version']}", opp_id=q["opp_id"],
                    customer=q["customer_name"])
        n += 1
    return n


# ---------------------------------------------------------------- W4 议价

def _latest_sent_quote(opp_id: str | None, customer_id: int, quote_no: str | None, model: str | None) -> dict | None:
    conds, args = ["q.status = 'SENT'", "q.customer_id = %s"], [customer_id]
    if quote_no:
        conds.append("q.quote_no = %s")
        args.append(quote_no)
    elif opp_id:
        conds.append("q.opp_id = %s")
        args.append(opp_id)
    if model and not quote_no:
        conds.append("q.model = %s")
        args.append(model)
    row = store.one(f"SELECT q.quote_no, q.version FROM wf_quote q WHERE {' AND '.join(conds)} ORDER BY q.sent_at DESC LIMIT 1", args)
    return _quote(f"{row['quote_no']}#{row['version']}") if row else None


def handle_counter(m: dict, c: dict) -> None:
    model = U.normalize_item({"model": c.get("model")})["model"] if c.get("model") else None
    q = _latest_sent_quote(m["opp_id"], m["party_id"], c.get("quote_no"), model) or _latest_sent_quote(None, m["party_id"], None, model)
    if not q:
        create_task("ASSIGN", f"客户还价，但找不到对应的已发出报价｜{m['subject'][:60]}", "SALES", ref_type="mail", ref_id=m["message_id"], opp_id=m["opp_id"], customer=m["party_name"])
        return
    store.execute("UPDATE wf_mail SET opp_id = %s WHERE message_id = %s", (q["opp_id"], m["message_id"]))
    snap, qty = q["snapshot"], c.get("qty") or q["annual_volume"] or q["tiers"][0]["qty"]
    current = rules.tier_price(q["tiers"], qty)
    analysis = rules.negotiation(current, c.get("target_price"), float(q["unit_cost"]), float(q["floor_price"]), snap["target_margin"], snap["min_margin"], snap.get("last_deal"),
                                 rules.tier_price(snap.get("rule_tiers") or snap["system_tiers"], qty))
    analysis.update(qty=qty, customer_reason=c.get("reason"))
    rnd = (store.one("SELECT MAX(round) r FROM wf_negotiation WHERE quote_no = %s", (q["quote_no"],))["r"] or 0) + 1
    store.execute("INSERT INTO wf_negotiation (quote_no, round, message_id, target_price, reason, analysis) VALUES (%s,%s,%s,%s,%s,%s)",
                  (q["quote_no"], rnd, m["message_id"], c.get("target_price"), (c.get("reason") or "")[:500], store.dumps(analysis)))
    create_task("NEGOTIATION", f"客户还价（第 {rnd} 轮）｜{q['model']}｜目标价 {c.get('target_price') or '未给出'}，当前 {current:.2f}", "SALES", ref_type="negotiation",
                ref_id=f"{q['quote_no']}#{rnd}", opp_id=q["opp_id"], customer=m["party_name"])


def negotiation_view(ref: str, role: str) -> dict:
    no, rnd = ref.split("#")
    n = store.one("SELECT * FROM wf_negotiation WHERE quote_no = %s AND round = %s", (no, int(rnd)))
    n["analysis"] = store.loads(n["analysis"])
    return n


def decide_negotiation(ref: str, strategy: str, price: float | None, condition: str, reason: str, user: str) -> None:
    """销售选定做法。涉及新价格的生成报价的新版本；毛利低于目标的要先审批"""
    no, rnd = ref.split("#")
    n = store.one("SELECT * FROM wf_negotiation WHERE quote_no = %s AND round = %s", (no, int(rnd)))
    if not n or n["strategy"]:
        raise ValueError("这一轮议价已经处理过了")
    last = store.one("SELECT MAX(version) v FROM wf_quote WHERE quote_no = %s", (no,))["v"]
    q, m = _quote(f"{no}#{last}"), _mail(n["message_id"])
    opp, snap = _opp(q["opp_id"]), q["snapshot"]
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'negotiation' AND ref_id = %s AND status = 'OPEN'", (ref,)):
        done_task(t["task_id"], user, strategy)
    store.execute("UPDATE wf_negotiation SET strategy = %s, new_price = %s, decided_by = %s WHERE quote_no = %s AND round = %s", (strategy, price, user, no, int(rnd)))
    store.audit(user, "negotiation.decide", f"negotiation:{ref}", {"strategy": strategy, "price": price, "condition": condition})
    facts = {"contact": m["from_name"], "sender": SENDER, "model": q["model"], "quote_no": no}
    if strategy in ("DECLINE", "ASK_TARGET"):
        zh = m["lang"] != "en"
        facts["message"] = ({"DECLINE": f"感谢您的反馈。经过评估，{q['model']} 目前的报价已经是我们能给出的最优价格，很遗憾无法满足您的目标价。如果后续用量或条件有变化，欢迎再联系我们。",
                             "ASK_TARGET": f"感谢您的反馈。为了评估能否调整 {q['model']} 的价格，方便告知您的目标价和预计采购数量吗？"} if zh else
                            {"DECLINE": f"Thank you for your feedback. After review, our current quotation for {q['model']} is already our best offer and we regret that we cannot meet your target price.",
                             "ASK_TARGET": f"Thank you for your feedback. Could you share your target price and expected quantity for {q['model']} so that we can review?"})[strategy]
        text = U.template("COUNTER_REPLY", facts, m["lang"])
        create_outbound("COUNTER_REPLY", q["opp_id"], _reply_to(m), "Re: " + m["subject"], text, "template", customer=opp["customer_name"], in_reply_to=m["message_id"],
                        after={"lost": True, "reason": reason or "目标价低于底价"} if strategy == "DECLINE" else {})
        return
    if strategy == "TRADE_SOLUTION":
        create_task("ASSIGN", f"请评估 {q['model']} 的降本方案（客户目标价 {n['target_price']}）", "ENGINEER", ref_type="quote", ref_id=f"{no}#{last}", opp_id=q["opp_id"], customer=opp["customer_name"])
        return
    if not price or price <= 0:
        raise ValueError("请填写新的价格")
    tiers = rules.reprice([{"qty": t["qty"], "unit_price": min(float(t["unit_price"]), float(price))} for t in q["tiers"]], float(q["unit_cost"]))
    lowest = min(t["margin"] for t in tiers)
    rule_tiers = snap.get("rule_tiers") or snap["system_tiers"]
    role = rules.approval_for(tiers, rule_tiers, snap["min_margin"], store.rule("approval"))
    if lowest < snap["min_margin"] - 1e-9 and not (reason or "").strip():
        raise ValueError("新价格的毛利率低于最低毛利率，必须填写理由")
    need = role != store.rule("approval")["at_or_above_target"]          # 让到规则价以下就要审批；没低于规则价的在销售权限内
    new_snap = {**snap, "origin": "negotiation", "condition": condition or "", "round": int(rnd), "rule_tiers": rule_tiers, "system_tiers": q["tiers"]}
    version, ref2 = last + 1, f"{no}#{last + 1}"
    store.execute("INSERT INTO wf_quote (quote_no, version, opp_id, customer_id, model, annual_volume, tiers, unit_cost, floor_price, margin, snapshot, status, approver_role, reason, valid_until, created_by) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (no, version, q["opp_id"], q["customer_id"], q["model"], q["annual_volume"], store.dumps(tiers), q["unit_cost"], q["floor_price"], tiers[0]["margin"], store.dumps(new_snap),
                   "APPROVING" if need else "APPROVED", role if need else None, (reason or "")[:500], date.today() + timedelta(days=store.rule("quote")["valid_days"]), user))
    if need:
        create_task("APPROVAL", f"议价让价 {no} 待{rules.ROLE_CN[role]}审批｜{q['model']}｜新价 {price:.2f}，毛利率 {lowest:.1%}", "MANAGEMENT", ref_type="quote", ref_id=ref2, opp_id=q["opp_id"],
                    customer=opp["customer_name"], payload={"approver_role": role})
    else:
        _draft_quote_mail(_quote(ref2), opp)


# ---------------------------------------------------------------- W7 订单与合同评审

# 【讲解】W7：客户 + PO 号唯一，重复的直接拦截；查好报价、产品、库存、信用传给 rules.review_po 做六项核对；
# 没有差异走简化评审，有差异按角色分给销售、技术、计划、财务，全部处理完才算评审通过。
def handle_po(m: dict, po: dict) -> None:
    po_no = (po.get("po_no") or "").strip()
    if store.one("SELECT 1 FROM wf_sales_order WHERE customer_id = %s AND po_no = %s", (m["party_id"], po_no)):
        create_task("ASSIGN", f"重复的 PO 已拦截：{po_no} 之前已经收到过，没有重复建单", "SALES", ref_type="mail", ref_id=m["message_id"], opp_id=m["opp_id"], customer=m["party_name"])
        return
    for l in po.get("lines", []):
        l["model"] = U.normalize_item({"model": l.get("model")})["model"] if l.get("model") else None
    models = [l["model"] for l in po.get("lines", []) if l.get("model")]
    quotes = {}
    for mdl in models:
        q = _latest_sent_quote(None, m["party_id"], po.get("quote_no"), mdl) or _latest_sent_quote(None, m["party_id"], None, mdl)
        if q and q["model"] == mdl:
            quotes[mdl] = {"quote_no": q["quote_no"], "tiers": q["tiers"], "valid_until": q["valid_until"], "opp_id": q["opp_id"]}
    products = {p["code"]: p for p in erp.products()}
    need = date.fromisoformat(po["required_date"]) if po.get("required_date") else None
    ctx = {"quotes": quotes, "products": {k: products[k] for k in models if k in products}, "stock": {k: erp.stock(k) for k in models}, "credit": erp.credit(m["party_id"]),
           "last_deals": {k: (erp.last_deal(m["party_id"], k) or {}).get("unit_price") for k in models}}
    issues = rules.review_po({**po, "required_date": need}, ctx, store.rule("po_review"), date.today())
    opp_id = next((q["opp_id"] for q in quotes.values()), None) or m["opp_id"]
    if not opp_id:                                            # 老客户直接下单，没有经过询价报价：直接建一个「已下单」的商机
        opp_id = create_opp(m["party_id"], f"订单 {po_no}", status="ORDERED", event="po.received")
    data = {**po, "customer_name": m["party_name"], "quotes": {k: v["quote_no"] for k, v in quotes.items()},
            "amount": round(sum(float(l.get("qty") or 0) * float(l.get("unit_price") or 0) for l in po.get("lines", [])), 2)}
    n = store.execute("INSERT IGNORE INTO wf_sales_order (customer_id, po_no, opp_id, message_id, data, issues, review_status) VALUES (%s,%s,%s,%s,%s,%s,'REVIEWING')",
                      (m["party_id"], po_no, opp_id, m["message_id"], store.dumps(data), store.dumps(issues)))
    if not n:
        return
    store.execute("UPDATE wf_mail SET opp_id = %s WHERE message_id = %s", (opp_id, m["message_id"]))
    oid = store.one("SELECT id FROM wf_sales_order WHERE customer_id = %s AND po_no = %s", (m["party_id"], po_no))["id"]
    if not issues:
        _po_passed(oid, "system（没有差异，简化评审）")
        return
    for i in issues:
        create_task("PO_ISSUE", f"PO {po_no}：{i['title']}", i["role"], ref_type="order", ref_id=f"{oid}#{i['id']}", opp_id=opp_id, customer=m["party_name"], payload={"detail": i["detail"], "type": i["type"]})


def resolve_issue(ref: str, note: str, user: str, reject: bool = False) -> None:
    oid, iid = ref.split("#")
    o = store.one("SELECT * FROM wf_sales_order WHERE id = %s", (int(oid),))
    issues = store.loads(o["issues"])
    for i in issues:
        if i["id"] == iid:
            i.update(resolved=True, resolved_by=user, note=note, rejected=reject)
    store.execute("UPDATE wf_sales_order SET issues = %s WHERE id = %s", (store.dumps(issues), o["id"]))
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'order' AND ref_id = %s AND status = 'OPEN'", (ref,)):
        done_task(t["task_id"], user, ("不接受：" if reject else "已处理：") + note)
    store.audit(user, "order.issue.resolve", f"order:{ref}", {"note": note, "reject": reject})
    if reject:
        store.execute("UPDATE wf_sales_order SET review_status = 'REJECTED' WHERE id = %s", (o["id"],))
        for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'order' AND ref_id LIKE %s AND status = 'OPEN'", (f"{oid}#%",)):
            store.execute("UPDATE wf_task SET status = 'CANCELLED' WHERE task_id = %s", (t["task_id"],))
        create_task("ASSIGN", f"PO {o['po_no']} 评审不通过，请和客户沟通：{note[:60]}", "SALES", ref_type="order", ref_id=str(oid), opp_id=o["opp_id"])
    elif all(i["resolved"] for i in issues) and o["review_status"] == "REVIEWING":
        _po_passed(o["id"], user)


def _po_passed(order_id: int, user: str) -> None:
    o = store.one("SELECT * FROM wf_sales_order WHERE id = %s", (order_id,))
    store.execute("UPDATE wf_sales_order SET review_status = 'PASSED' WHERE id = %s", (order_id,))
    opp = _opp(o["opp_id"])
    if opp["status"] != "ORDERED":
        move(o["opp_id"], "ORDERED", "po.reviewed", user, next_action="在 ERP 里建销售订单", due_days=1)
    # 写 ERP 的开关默认关着：生成一个待办，把要录的数据列出来，由人在 ERP 里建单后把订单号填回来
    create_task("ERP_IMPORT", f"评审通过，请在 ERP 里建销售订单｜PO {o['po_no']}", "SALES", ref_type="order", ref_id=str(order_id), opp_id=o["opp_id"], customer=opp["customer_name"],
                payload={"write_enabled": settings.workflow_erp_write})


def erp_order_created(order_id: int, erp_order_no: str, user: str) -> None:
    o = store.one("SELECT * FROM wf_sales_order WHERE id = %s", (order_id,))
    if not o or o["review_status"] != "PASSED":
        raise ValueError("这张 PO 还没有评审通过")
    if o["erp_order_no"]:
        raise ValueError("这张 PO 已经填过 ERP 订单号了")
    store.execute("UPDATE wf_sales_order SET erp_order_no = %s WHERE id = %s", (erp_order_no.strip(), order_id))
    for t in store.query("SELECT task_id FROM wf_task WHERE ref_type = 'order' AND ref_id = %s AND type = 'ERP_IMPORT' AND status = 'OPEN'", (str(order_id),)):
        done_task(t["task_id"], user, f"ERP 订单号 {erp_order_no}")
    store.audit(user, "order.erp_created", f"order:{order_id}", {"erp_order_no": erp_order_no})
    emit("sales_order.created", {"order_id": order_id, "opp_id": o["opp_id"], "erp_order_no": erp_order_no})
    data, m = store.loads(o["data"]), _mail(o["message_id"])
    c = erp.customer(o["customer_id"])
    facts = {"contact": m["from_name"], "sender": SENDER, "po_no": o["po_no"], "order_no": erp_order_no, "required_date": data.get("required_date") or "待确认",
             "payment_days": data.get("payment_days") or c["payment_terms_days"],
             "lines": [{"model": l["model"], "qty": float(l["qty"]), "unit_price": float(l["unit_price"] or 0)} for l in data.get("lines", [])]}
    text, by = U.draft("ORDER_CONFIRM", facts, m["lang"], m["body_new"])
    create_outbound("ORDER_CONFIRM", o["opp_id"], _reply_to(m), "Re: " + m["subject"], text, by, customer=data.get("customer_name"), in_reply_to=m["message_id"])


# ---------------------------------------------------------------- W8 客户问进度

# 【讲解】W8 回答「货到哪了」：按订单号或 PO 号查 ERP，订单必须属于这个客户；回复用模板填查到的事实，不让模型描述订单状态。
def handle_delivery_query(m: dict, d: dict) -> None:
    order_no = d.get("order_no")
    if not order_no and d.get("po_no"):
        row = store.one("SELECT erp_order_no FROM wf_sales_order WHERE customer_id = %s AND po_no = %s", (m["party_id"], d["po_no"]))
        order_no = row["erp_order_no"] if row else None
    st = erp.order_status(order_no) if order_no else None
    if not st or st["customer_id"] != m["party_id"]:          # 查不到，或者订单不是这个客户的：不回复，交给人
        create_task("ASSIGN", f"客户问订单进度，但找不到对应的订单（{d.get('po_no') or d.get('order_no') or '邮件里没有单号'}）", "SALES", ref_type="mail", ref_id=m["message_id"],
                    opp_id=m["opp_id"], customer=m["party_name"])
        return
    facts = {"contact": m["from_name"], "sender": SENDER, "order_no": st["order_no"], "status": st["status"], "status_cn": ORDER_STATUS_CN.get(st["status"], st["status"]),
             "required_date": st["required_date"], "deliveries": st["deliveries"][-3:], "lines": st["lines"]}
    text = U.template("DELIVERY_REPLY", facts, m["lang"])     # 订单状态只按查到的事实填模板，不让模型描述
    create_outbound("DELIVERY_REPLY", m["opp_id"], _reply_to(m), "Re: " + m["subject"], text, "template", customer=m["party_name"], in_reply_to=m["message_id"])
    if d.get("new_date") or (d.get("ask") and any(w in str(d["ask"]) for w in ("提前", "推迟", "延后", "earlier", "postpone", "expedite", "delay"))):
        create_task("ASSIGN", f"客户要求调整订单 {st['order_no']} 的交期（{d.get('ask') or ''} {d.get('new_date') or ''}），请评估产能和物料", "PRODUCTION", ref_type="mail",
                    ref_id=m["message_id"], opp_id=m["opp_id"], customer=m["party_name"])


# ---------------------------------------------------------------- 定时和启动

def escalate_overdue() -> int:
    """待办超时：提醒并升级一次"""
    rows = store.query("SELECT task_id, type, title, role, customer_name, due_at FROM wf_task WHERE status = 'OPEN' AND escalated = 0 AND due_at < NOW() LIMIT 50")
    for t in rows:
        store.execute("UPDATE wf_task SET escalated = 1 WHERE task_id = %s", (t["task_id"],))
        _notify(f"[超时] {TASK_CN.get(t['type'], t['type'])}：{t['title'][:60]}", f"**客户** {t['customer_name'] or '-'}\n**应在** {t['due_at']} 前处理，已超时，已通知上级。")
    return len(rows)


def retry_unread() -> int:
    """模型不可用期间没读懂的邮件：模型恢复后重新识别一遍（已经有人工处理过的不动）"""
    rows = store.query("SELECT message_id FROM wf_mail WHERE status = 'PENDING' AND pending_reason LIKE '系统没能读懂%%'")
    for r in rows:
        store.execute("UPDATE wf_task SET status = 'CANCELLED', result = '模型恢复后重新识别' WHERE ref_type = 'mail' AND ref_id = %s AND type = 'TRIAGE' AND status = 'OPEN'", (r["message_id"],))
        store.execute("UPDATE wf_mail SET status = 'NEW', pending_reason = NULL WHERE message_id = %s", (r["message_id"],))
        emit("mail.received", {"message_id": r["message_id"]})
    return len(rows)


def seed_reference() -> None:
    """ERP 里没有的三样东西，这里给初始值：客户等级（按信用额度）、客户负责人、客户和供应商的邮箱域名"""
    store.ensure()
    rule = store.rule("customer_level")
    for c in erp.customers():
        store.execute("INSERT IGNORE INTO wf_customer_ext (customer_id, level, owner, domains) VALUES (%s,%s,'sales01',%s)",
                      (c["id"], rules.customer_level(float(c["credit_limit"] or 0), rule), f"{c['code'].lower()}-corp.example.com"))
    if not store.one("SELECT 1 FROM wf_rule WHERE name = 'supplier_domains' LIMIT 1"):
        store.execute("INSERT INTO wf_rule (name, version, content, updated_by) VALUES ('supplier_domains', 1, %s, 'system')",
                      (store.dumps({"说明": "供应商的邮箱域名（ERP 里没有供应商联系人）", "domains": {f"{s['code'].lower()}-supplier.example.com": s["id"] for s in erp.suppliers()}}),))


HANDLERS = {"mail.received": triage, "mail.classified": on_classified, "requirement.confirmed": start_quote}
_started = False


def poll_mailbox() -> int:
    from app.workflow import mailbox
    new = mailbox.pull()
    for mid in new:
        emit("mail.received", {"message_id": mid})
    return len(new)


def start_background() -> None:
    global _started
    if _started:
        return
    _started = True
    seed_reference()

    def loop(name, fn, gap):
        while True:
            try:
                fn()
            except Exception:
                logger.exception(f"[workflow] {name} 出错")
            time.sleep(gap)
    threading.Thread(target=loop, args=("邮箱轮询", poll_mailbox, 20), daemon=True, name="wf-mail").start()
    threading.Thread(target=loop, args=("事件转发", relay, 1), daemon=True, name="wf-relay").start()
    threading.Thread(target=loop, args=("超时检查", lambda: (escalate_overdue(), remind_expiring_quotes()), 300), daemon=True, name="wf-timer").start()
    threading.Thread(target=mq.consume, args=(QUEUE, dispatch), kwargs={"name": "wf-events"}, daemon=True, name="wf-events").start()
