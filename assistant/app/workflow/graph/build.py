"""
跟单图（LangGraph）：一封邮件一个案例（thread_id = case_id）。

    intake → supervisor ─┬─(Send)→ order_agent    ─┐
                         ├─(Send)→ delivery_agent ─┼→ compose → review ─┬─ 通过 ───────────────→ approval ──┬─ 批准 → execute → END
                         ├─(Send)→ tech_agent     ─┘     ↑              ├─ 不通过且没到上限 → compose     └─ 驳回 → rejected → END
                         └─ 全是无关邮件 → ignore → END   └──────────────┘└─ 不通过且到上限 → approval（标「审核未通过，转人工」）

  - Supervisor 拆出的子任务用 Send 并行分派，结果按 results 字段（operator.add）汇总
  - approval 节点调用 interrupt() 停下等人；状态存在 checkpointer 里，进程重启后用同一个 case_id 恢复
  - 写操作只在 execute 节点里发生，而且每个都在 approved(审批人, 动作编号) 里执行；审批之前的节点都不写任何业务数据
"""
import base64
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date
from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from app.infra.config.settings import settings
from app.shared.runtime.logger import PROJECT_ROOT
from app.workflow import mailbox, store
from app.workflow.graph import actions as A, compose as C, delivery, reviewer as R, supervisor as SV, workers
from app.workflow.graph.state import AGENT_OF, Approval, CaseState, ProposedAction, SubTask, TYPE_CN

CHECKPOINT_DB = PROJECT_ROOT / "data" / "workflow_cases.sqlite"


def _default_json_llm(prompt: str) -> str:
    from app.copilot import llm
    return llm.invoke(prompt, json_mode=True, app="workflow").content


@dataclass
class Deps:
    """图用到的模型。测试时换成按剧本回答的假模型"""
    llm_json: Callable[[str], str] | None = field(default=_default_json_llm)     # Supervisor 拆分、合成回复
    order_llm: Callable[[str], str] | None = None                                 # 订单抽取（空 = 订单 Agent 自己的默认模型）
    agent_model: object | None = None                                             # 交付 Agent 的 ReAct 模型（空 = 默认）
    tone_llm: Callable[[str], str] | None = field(default=_default_json_llm)       # 审核的语气检查（大模型）；None = 只用规则
    sensitive: Callable[[list[dict]], dict] = R.sensitive_numbers
    max_rounds: int | None = None                                                 # 审核驳回后最多重写几次（空 = 配置）


def build(deps: Deps | None = None, checkpointer=None):
    deps = deps or Deps()
    max_rounds = deps.max_rounds if deps.max_rounds is not None else settings.workflow_review_max_rounds

    def intake(s: CaseState) -> dict:
        m = mailbox.parse(base64.b64decode(s["raw_b64"]))
        who = mailbox.identify(m["from_addr"])
        cust = {"id": who["id"], "name": who.get("name")} if who.get("type") == "CUSTOMER" else None
        if cust:
            from app.workflow import erp
            c = erp.customer(cust["id"]) or {}
            cust.update(code=c.get("code"), name=c.get("name") or cust["name"])
        mail = {"message_id": m["message_id"], "from_addr": m["from_addr"], "from_name": m["from_name"], "subject": m["subject"], "body": m["body"],
                "received_at": str(m["received_at"]), "attachments": [a["name"] for a in m["attachments"]]}
        lang = "zh" if mailbox.language(m["body"] or m["subject"]) == "zh" else "en"
        return {"mail": mail, "customer": cust, "lang": lang, "as_of": str(m["received_at"])[:10], "results": [], "drafts": [], "reviews": []}

    def supervise(s: CaseState) -> dict:
        subtasks, _ = SV.split(s["mail"], deps.llm_json)
        return {"subtasks": [st.model_dump(mode="json") for st in subtasks]}

    def dispatch(s: CaseState):
        jobs = [st for st in s["subtasks"] if st["type"] in AGENT_OF]
        if not jobs:
            return "ignore"
        ctx = {k: s[k] for k in ("case_id", "raw_b64", "mail", "customer", "lang", "as_of")}
        return [Send(AGENT_OF[st["type"]], {**ctx, "subtask": st}) for st in jobs]

    def order_agent(p: dict) -> dict:
        st = SubTask.model_validate(p["subtask"])
        return {"results": [workers.run_order(st, p["raw_b64"], p["mail"], p["customer"], p["lang"], deps.order_llm).model_dump(mode="json")]}

    def delivery_agent(p: dict) -> dict:
        st = SubTask.model_validate(p["subtask"])
        out = delivery.run(st, p["mail"], p["customer"], p["lang"], date.fromisoformat(p["as_of"]), model=deps.agent_model)
        return {"results": [out.model_dump(mode="json")]}

    def tech_agent(p: dict) -> dict:
        st = SubTask.model_validate(p["subtask"])
        return {"results": [workers.run_tech(st, p["customer"], p["lang"]).model_dump(mode="json")]}

    def compose(s: CaseState) -> dict:
        last = s["reviews"][-1] if s.get("reviews") else None
        prev = s["drafts"][-1]["body"] if s.get("drafts") else None
        d = C.write_reply(s["results"], s["mail"], s["lang"], deps.llm_json, last["issues"] if last else None, prev)
        return {"drafts": s.get("drafts", []) + [d.model_dump(mode="json")]}

    def review(s: CaseState) -> dict:
        d = s["drafts"][-1]
        r = R.review(d["body"], s["results"], s["mail"], date.fromisoformat(s["as_of"]), deps.sensitive(s["results"]), deps.tone_llm, len(s["drafts"]) - 1)
        return {"reviews": s.get("reviews", []) + [r.model_dump(mode="json")]}

    def after_review(s: CaseState) -> str:
        if s["reviews"][-1]["passed"] or len(s["reviews"]) > max_rounds:     # 初稿 + 最多 max_rounds 次重写
            return "approval"
        return "compose"

    def proposed(s: CaseState) -> list[dict]:
        acts = [a for r in sorted(s["results"], key=lambda r: r["subtask_id"]) for a in r["actions"]]
        d = s["drafts"][-1]
        if any(r.get("section") for r in s["results"]):
            acts.insert(0, ProposedAction(id="reply", kind="send_email", summary=f"回复客户 {d['to'][0]}：{d['subject']}",
                                          payload={"to": d["to"], "subject": d["subject"], "body": d["body"], "in_reply_to": d.get("in_reply_to")}).model_dump(mode="json"))
        return acts

    def approval(s: CaseState) -> dict:
        rv = s["reviews"][-1]
        ask = {"case_id": s["case_id"], "customer": s.get("customer"), "subject": s["mail"]["subject"],
               "subtasks": [{"id": t["id"], "type": t["type"], "type_cn": TYPE_CN[t["type"]], "summary": t["summary"]} for t in s["subtasks"]],
               "results": [{"subtask_id": r["subtask_id"], "agent": r["agent"], "ok": r["ok"], "note": r["note"]} for r in s["results"]],
               "draft": s["drafts"][-1], "template_draft": C.template_reply(s["results"], s["mail"], s["lang"]).model_dump(mode="json"),
               "review": rv, "review_failed": not rv["passed"], "rounds": len(s["drafts"]) - 1, "actions": proposed(s)}
        decision = Approval.model_validate(interrupt(ask))     # 在这里停下；resume 时 interrupt() 返回审批结果
        return {"approval": decision.model_dump(mode="json")}

    def after_approval(s: CaseState) -> str:
        return "execute" if s["approval"]["decision"] == "approve" else "rejected"

    def execute(s: CaseState) -> dict:
        ap = s["approval"]
        acts = proposed(s)
        ai = next((a for a in acts if a["id"] == "reply"), None)
        if ai:                                                    # 审批人改过的主题 / 正文替换进去，AI 原稿留在审计里
            ai["payload"] = {**ai["payload"], "subject": ap.get("final_subject") or ai["payload"]["subject"], "body": ap.get("final_body") or ai["payload"]["body"]}
        chosen = [a for a in acts if ap.get("actions") is None or a["id"] in ap["actions"]]
        done = A.execute(s["case_id"], chosen, ap["approver"])
        _audit_decision(s, ap, acts, chosen)
        return {"executed": done, "status": "DONE"}

    def rejected(s: CaseState) -> dict:
        _audit_decision(s, s["approval"], proposed(s), [])
        return {"executed": [], "status": "REJECTED"}

    def ignore(s: CaseState) -> dict:
        return {"status": "IGNORED", "executed": []}

    g = StateGraph(CaseState)
    for name, fn in [("intake", intake), ("supervisor", supervise), ("order_agent", order_agent), ("delivery_agent", delivery_agent), ("tech_agent", tech_agent),
                     ("compose", compose), ("review", review), ("approval", approval), ("execute", execute), ("rejected", rejected), ("ignore", ignore)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_edge("intake", "supervisor")
    g.add_conditional_edges("supervisor", dispatch, ["order_agent", "delivery_agent", "tech_agent", "ignore"])
    for n in ("order_agent", "delivery_agent", "tech_agent"):
        g.add_edge(n, "compose")
    g.add_edge("compose", "review")
    g.add_conditional_edges("review", after_review, ["approval", "compose"])
    g.add_conditional_edges("approval", after_approval, ["execute", "rejected"])
    for n in ("execute", "rejected", "ignore"):
        g.add_edge(n, END)
    return g.compile(checkpointer=checkpointer)


def _audit_decision(s: CaseState, ap: dict, acts: list[dict], chosen: list[dict]) -> None:
    """审计：AI 建议 vs 人工最终版本，哪些动作批了、哪些没批"""
    d = s["drafts"][-1]
    store.audit(ap["approver"], f"case.{ap['decision']}", f"case:{s['case_id']}", {
        "message_id": s["mail"]["message_id"], "ai_subject": d["subject"], "ai_body": d["body"], "ai_written_by": d["written_by"],
        "final_subject": ap.get("final_subject"), "final_body": ap.get("final_body"),
        "edited": bool((ap.get("final_body") and ap["final_body"] != d["body"]) or (ap.get("final_subject") and ap["final_subject"] != d["subject"])),
        "review_passed": s["reviews"][-1]["passed"], "review_rounds": len(s["drafts"]) - 1,
        "approved_actions": [a["id"] for a in chosen], "skipped_actions": [a["id"] for a in acts if a not in chosen], "note": ap.get("note", ""),
        "at": time.strftime("%Y-%m-%d %H:%M:%S")})


def sqlite_saver(path=CHECKPOINT_DB):
    from langgraph.checkpoint.sqlite import SqliteSaver
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
