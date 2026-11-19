"""
Agent 评测：整张跟单图端到端跑，再单独测审核 Agent。

端到端（run）：每封邮件跑完整张图，停在审批时自动「批准全部动作」（外发邮件、ERP、待办换成记录器，不进工作台；PO 版本照常记，后面的变更才有依据）。
同一客户按时间顺序、不同客户并行。逐个子任务按标准答案判成败：
  po_entry / po_change   PO 号、新单 / 变更 / 重复和版本、分流都对；该生成 ERP 草稿的生成了且和标准销售订单一致，不该的没生成；回复里有 PO 号
  delivery_query         找到的订单 = 标准答案；回复里提到了这张单（我方单号或客户 PO 号）
  expedite               找到的订单对；能否满足和程序判断（check_expedite 的规则，独立再算一次）一致：能满足就确认客户要的日期，
                         不能满足就不给日期、派计划部
  spec_inquiry           派了工程师（技术 Agent 阶段 6 才做）
  other（干扰邮件）       直接结束，不审批、不写任何东西
  一封邮件成功 = 拆分完全正确 + 每个子任务都成功 + 最终草稿通过审核 + 批准后的写操作全部执行成功

交付 Agent 的工具调用准确率：必需的调用都出现且参数正确（find_order 用对了单号、get_order_status / check_expedite 用对了订单号和日期、
不能满足时派了计划部）；另外统计单次调用的出错率、平均步数。

审核 Agent（run_reviewer）：用 reviewer_cases 构造的草稿，算拦截率（违规草稿被拦下）、拦对规则的比例、误拦截率（正常草稿被拦下）。
"""
import base64
import statistics
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.workflow import adapters, store
from app.workflow.adapters.mock import MiniErpERP
from app.workflow.adapters.models import SentReceipt
from app.workflow.eval import metrics as M, reviewer_cases as RC
from app.workflow.eval.datasets import Dataset, by_customer
from app.workflow.graph import build as B, delivery, reviewer as R
from app.workflow.order_pipeline import agent as order_agent

APPROVER = "eval"


class RecEmail:
    def __init__(self):
        self.sent = []

    def fetch_new(self, since=None):
        return []

    def send(self, d):
        from datetime import datetime
        self.sent.append(d)
        return SentReceipt(message_id=f"<eval-{len(self.sent)}@eval>", location="eval", sent_at=datetime.now())


class RecTask:
    def __init__(self):
        self.created = []

    def create_task(self, req) -> str:
        self.created.append(req)
        return f"TK-EVAL-{len(self.created)}"


def _case(g, ds: Dataset, e: dict) -> dict:
    cid = f"EVAL-{ds.name}-{e['id']}"
    cfg = {"configurable": {"thread_id": cid}}
    t0 = time.time()
    try:
        s = g.invoke({"case_id": cid, "raw_b64": base64.b64encode(ds.raw(e["id"])).decode()}, cfg)
        waited = bool(s.get("__interrupt__"))
        ask = s["__interrupt__"][0].value if waited else None
        if waited:
            s = g.invoke(Command(resume={"decision": "approve", "approver": APPROVER}), cfg)
        return {"id": e["id"], "state": s, "ask": ask, "waited": waited, "seconds": round(time.time() - t0, 2)}
    except Exception as ex:
        return {"id": e["id"], "error": f"{type(ex).__name__}: {str(ex)[:300]}", "seconds": round(time.time() - t0, 2)}


def _mentions(body: str, *keys) -> bool:
    return any(k and str(k) in body for k in keys)


def _expedite_oracle(customer_id: int, as_of: str, order_no: str, requested: str) -> bool:
    """独立再算一次：这张单能不能满足客户要的日期（和 check_expedite 同一条业务规则）"""
    fn = delivery.make_tools(customer_id, date.fromisoformat(as_of), [])["check_expedite"][0]
    return bool(fn(order_no, date.fromisoformat(requested)).get("feasible"))


def judge(ds: Dataset, e: dict, run: dict) -> dict:
    """一封邮件的成败（逐个子任务）"""
    s = run["state"]
    subs = {x["id"]: x for x in s.get("subtasks", [])}
    results = {r["subtask_id"]: r for r in s.get("results", [])}
    executed = s.get("executed") or []
    reply = next((x for x in (run["ask"] or {}).get("actions", []) if x["id"] == "reply"), None)
    body = reply["payload"]["body"] if reply else ""
    sent = any(x["id"] == "reply" and x["ok"] for x in executed)
    got = sorted((x["type"], x["refs"].get("attachment")) for x in subs.values())
    want = sorted((st["type"], st["refs"]["attachment"].split("/", 1)[1] if st["refs"].get("attachment") else None) for st in e["subtasks"])
    out = {"id": e["id"], "scenario": e["scenario"], "split_ok": got == want, "subtasks": [], "seconds": run["seconds"]}
    if e["scenario"] == "spam":
        ok = s.get("status") == "IGNORED" and not run["waited"] and not executed
        out.update(success=ok, subtasks=[{"type": "other", "ok": ok, "why": [] if ok else ["干扰邮件没有直接结束"]}])
        return out
    used = set()
    for gst in e["subtasks"]:
        g, why, typ = gst["refs"], [], gst["type"]
        cands = [sid for sid, x in subs.items() if x["type"] == typ and sid not in used]
        if typ in ("po_entry", "po_change"):
            name = g["attachment"].split("/", 1)[1]
            sid = next((sid for sid in cands if subs[sid]["refs"].get("attachment") == name), None)
        elif typ in ("delivery_query", "expedite"):
            sid = next((sid for sid in cands if (results.get(sid) or {}).get("facts", {}).get("order_no") == g["order_no"]), cands[0] if cands else None)
        else:
            sid = cands[0] if cands else None
        r = results.get(sid) if sid else None
        if r is None:
            out["subtasks"].append({"type": typ, "ok": False, "why": ["没有拆出这个子任务"]})
            continue
        used.add(sid)
        acts = r["actions"]
        if typ in ("po_entry", "po_change"):
            gold = ds.pos[g["attachment"]]
            kind = "duplicate" if gold["duplicate_of"] else ("change" if gold["change"] else "new")
            f = r["facts"]
            if f.get("po_no") != gold["po_no"]:
                why.append(f"PO 号 {f.get('po_no')} ≠ {gold['po_no']}")
            if (f.get("kind"), f.get("version")) != (kind, gold["version"]):
                why.append(f"新单/变更/重复 {f.get('kind')} v{f.get('version')} ≠ {kind} v{gold['version']}")
            if (f.get("triage") == "one_click") != (not gold["flags"]):
                why.append("分流不对")
            unknown = any(x["rule"] == "unknown_part" for x in gold["flags"])
            erp = next((a for a in acts if a["kind"] == "erp_save_sales_order"), None)
            if kind != "duplicate" and not unknown:
                exp = gold["expected_order"]
                good = erp and erp["payload"]["customer_po"] == exp["customer_po"] and len(erp["payload"]["lines"]) == len(exp["lines"]) and all(
                    a["material"] == b["material"] and M.num_eq(a["qty"], b["qty"]) and M.num_eq(a["unit_price"], b["unit_price"]) and str(a["delivery_date"]) == b["delivery_date"]
                    for a, b in zip(erp["payload"]["lines"], exp["lines"]))
                if not good:
                    why.append("ERP 草稿和标准销售订单不一致" if erp else "没有生成 ERP 草稿")
            elif erp:
                why.append("不该生成 ERP 草稿")
            if not _mentions(body, gold["po_no"]):
                why.append("回复里没有 PO 号")
        elif typ in ("delivery_query", "expedite"):
            f = r["facts"]
            if f.get("order_no") != g["order_no"]:
                why.append(f"找到的订单 {f.get('order_no')} ≠ {g['order_no']}")
            if not _mentions(body, g["order_no"], g["customer_po"]):
                why.append("回复里没有提到这张单")
            if typ == "expedite" and f.get("order_no") == g["order_no"]:
                feasible = _expedite_oracle(e["customer_id"], s["as_of"], g["order_no"], g["requested_date"])
                if feasible and f.get("committed_date") != g["requested_date"]:
                    why.append("能满足但没有确认客户要的日期")
                if not feasible:
                    if f.get("committed_date"):
                        why.append(f"不能满足却确认了日期 {f['committed_date']}")
                    if not any(a["kind"] == "create_task" and a["payload"].get("role") == "PRODUCTION" for a in acts):
                        why.append("不能满足但没有派计划部")
        elif typ == "spec_inquiry":
            if not any(a["kind"] == "create_task" and a["payload"].get("role") == "ENGINEER" for a in acts):
                why.append("没有派工程师")
        out["subtasks"].append({"type": typ, "ok": not why, "why": why, "subtask_id": sid})
    review_ok = bool(s.get("reviews")) and s["reviews"][-1]["passed"]
    exec_ok = bool(executed) and all(x["ok"] for x in executed)
    case_why = [w for x in out["subtasks"] for w in x["why"]]
    if not out["split_ok"]:
        case_why.append(f"拆分：得到 {got}，应为 {want}")
    if not review_ok:
        case_why.append("最终草稿没通过审核（转人工）")
    if not exec_ok:
        case_why.append("批准后有写操作失败：" + "；".join(f"{x['id']} {x.get('error')}" for x in executed if not x["ok"]))
    if not sent:
        case_why.append("回复没有发出")
    out.update(success=not case_why, why=case_why, review_rounds=len(s.get("drafts") or []) - 1, review_passed=review_ok,
               review_issues=[[i["rule"] for i in rv["issues"]] for rv in s.get("reviews") or []])
    return out


def tool_accuracy(e: dict, s: dict) -> list[dict]:
    """交付 Agent 的工具调用：必需调用都对了没有"""
    out = []
    subs = {x["id"]: x for x in s.get("subtasks", [])}
    for r in s.get("results", []):
        if r["agent"] != "delivery":
            continue
        typ = subs[r["subtask_id"]]["type"]
        gst = next((x for x in e["subtasks"] if x["type"] == typ), None)
        if gst is None:
            continue
        g = gst["refs"]
        calls = [x for x in r["steps"] if x.get("tool") and x["tool"] != "submit"]
        has = lambda tool, pred: any(x["tool"] == tool and not x.get("error") and pred(x.get("args") or {}) for x in calls)
        need = {"find_order": has("find_order", lambda a: (a.get("customer_po") or "").strip() == g["customer_po"] or (a.get("order_no") or "").strip() == g["order_no"]),
                "get_order_status": has("get_order_status", lambda a: a.get("order_no") == g["order_no"])}
        if typ == "expedite":
            need["check_expedite"] = has("check_expedite", lambda a: a.get("order_no") == g["order_no"] and str(a.get("requested_date")) == g["requested_date"])
            if not _expedite_oracle(e["customer_id"], s["as_of"], g["order_no"], g["requested_date"]):
                need["create_task(PRODUCTION)"] = has("create_task", lambda a: a.get("role") == "PRODUCTION")
        out.append({"id": e["id"], "type": typ, "ok": all(need.values()), "missing": [k for k, v in need.items() if not v], "calls": len(calls),
                    "errors": sum(1 for x in calls if x.get("error")), "submit_retries": sum(1 for x in r["steps"] if x.get("tool") == "submit" and not x.get("ok", True)),
                    "steps": [x["tool"] for x in r["steps"]]})
    return out


def run(ds: Dataset, deps: B.Deps, workers: int = 4, limit: int = 0) -> dict:
    t0 = time.time()
    emails = ds.emails[: limit or None]
    order_agent.reset_pipeline_versions()
    store.execute("DELETE FROM wf_audit WHERE actor = %s", (APPROVER,))
    import tempfile
    from pathlib import Path
    g = B.build(deps, InMemorySaver())
    rec_email, rec_task = RecEmail(), RecTask()
    try:
        with adapters.override(email=rec_email, task=rec_task, erp=MiniErpERP(Path(tempfile.mkdtemp(prefix="eval_erp_")))):
            with ThreadPoolExecutor(workers) as pool:
                runs = [x for part in pool.map(lambda grp: [_case(g, ds, e) for e in grp], by_customer(emails)) for x in part]
    finally:
        order_agent.reset_pipeline_versions()
        store.execute("DELETE FROM wf_audit WHERE actor = %s", (APPROVER,))
    by_id = {r["id"]: r for r in runs}
    rows, tools, cases = [], [], []
    for e in emails:
        r = by_id[e["id"]]
        if "error" in r:
            rows.append({"id": e["id"], "scenario": e["scenario"], "success": False, "why": [r["error"]], "error": r["error"], "subtasks": [], "seconds": r["seconds"]})
            continue
        rows.append(judge(ds, e, r))
        tools += tool_accuracy(e, r["state"])
        s = r["state"]
        if s.get("results") and any(x.get("section") for x in s["results"]):
            infeasible = None                                     # 加急且程序判断不能满足：拿客户要的日期造「无依据承诺」
            for gst in e["subtasks"]:
                if gst["type"] == "expedite" and not _expedite_oracle(e["customer_id"], s["as_of"], gst["refs"]["order_no"], gst["refs"]["requested_date"]):
                    infeasible = gst["refs"]["requested_date"]
            cases.append({"id": e["id"], "results": s["results"], "mail": s["mail"], "lang": s["lang"], "as_of": s["as_of"], "infeasible_request": infeasible})
    subs = [x for r in rows for x in r["subtasks"]]
    nonspam = [r for r in rows if r["scenario"] != "spam" and "error" not in r]
    secs = [r["seconds"] for r in rows]
    summary = {
        "emails": len(rows), "errors": sum(1 for r in rows if "error" in r), "seconds": round(time.time() - t0, 1),
        "case_success": M.ratio(sum(r["success"] for r in rows), len(rows)),
        "case_success_by_scenario": {sc: M.ratio(sum(r["success"] for r in rows if r["scenario"] == sc), sum(r["scenario"] == sc for r in rows))
                                     for sc in sorted({r["scenario"] for r in rows})},
        "subtask_success": M.ratio(sum(x["ok"] for x in subs), len(subs)),
        "subtask_success_by_type": {t: M.ratio(sum(x["ok"] for x in subs if x["type"] == t), sum(x["type"] == t for x in subs)) for t in sorted({x["type"] for x in subs})},
        "tool_accuracy": M.ratio(sum(x["ok"] for x in tools), len(tools)),
        "tool_accuracy_by_type": {t: M.ratio(sum(x["ok"] for x in tools if x["type"] == t), sum(x["type"] == t for x in tools)) for t in sorted({x["type"] for x in tools})},
        "tool_call_errors": M.ratio(sum(x["errors"] for x in tools), sum(x["calls"] for x in tools)),
        "tool_calls_avg": round(statistics.mean([x["calls"] for x in tools]), 2) if tools else None,
        "submit_retries": sum(x["submit_retries"] for x in tools),
        "review_first_pass": M.ratio(sum(1 for r in nonspam if r.get("review_issues") and not r["review_issues"][0]), len(nonspam)),
        "review_final_pass": M.ratio(sum(1 for r in nonspam if r.get("review_passed")), len(nonspam)),
        "review_rounds": dict(sorted(Counter(r.get("review_rounds") for r in nonspam).items())),
        "review_issue_rules": dict(Counter(x for r in nonspam for rv in r.get("review_issues", []) for x in rv)),
        "emails_sent": len(rec_email.sent), "tasks_created": len(rec_task.created),
        "latency_p50": round(statistics.median(secs), 1) if secs else None, "latency_p90": round(sorted(secs)[int(len(secs) * 0.9) - 1], 1) if len(secs) >= 10 else None,
    }
    failures = [{"id": r["id"], "scenario": r["scenario"], "why": r["why"]} for r in rows if not r["success"]]
    tool_failures = [{"id": x["id"], "type": x["type"], "missing": x["missing"], "steps": x["steps"]} for x in tools if not x["ok"]]
    return {"summary": summary, "failures": failures, "tool_failures": tool_failures, "rows": rows, "tools": tools, "cases": cases}


# ---------------------------------------------------------------- 审核 Agent

def run_reviewer(cases: list[dict], llm_call=None, sensitive_of=R.sensitive_numbers, tone_llm=None, workers: int = 4) -> dict:
    """llm_call：构造大模型改写样本用（空 = 只用代码构造的样本）；tone_llm：审核时的大模型语气检查，和图上的配置保持一致"""
    t0 = time.time()
    cache: dict[str, dict] = {}

    def sens(results: list[dict]) -> dict:
        key = ",".join(sorted(r["subtask_id"] + str(r.get("facts", {}).get("po_no") or r.get("facts", {}).get("order_no")) for r in results))
        if key not in cache:
            cache[key] = sensitive_of(results)
        return cache[key]

    samples = RC.build(cases, sens)
    dropped = {}
    if llm_call:
        with ThreadPoolExecutor(workers) as pool:
            parts = list(pool.map(lambda c: RC.build_llm([c], sens, llm_call), cases))
        samples += [x for s, _ in parts for x in s]
        dropped = {k: sum(d[k] for _, d in parts) for k in ("paraphrase", "inject")}
    by_case = {c["id"]: c for c in cases}
    def check(x: dict) -> None:
        c = by_case[x["case"]]
        rv = R.review(x["body"], c["results"], c["mail"], date.fromisoformat(c["as_of"]), sens(c["results"]), tone_llm)
        x["flagged"] = not rv.passed
        x["rules"] = sorted({i.rule for i in rv.issues})
        x["issues"] = [i.model_dump() for i in rv.issues]
    for c in cases:                                               # 先把敏感数字算好（查库），并行审核时只读缓存
        sens(c["results"])
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(check, samples))
    bad = [x for x in samples if x["kind"] == "bad"]
    clean = [x for x in samples if x["kind"] == "clean"]

    def block(rows: list[dict]) -> dict:
        return {"intercept": M.ratio(sum(x["flagged"] for x in rows), len(rows)), "right_rule": M.ratio(sum(x["expect"] in x["rules"] for x in rows), len(rows))}

    summary = {
        "cases": len(cases), "samples": len(samples), "dropped_llm": dropped, "seconds": round(time.time() - t0, 1),
        "intercept": M.ratio(sum(x["flagged"] for x in bad), len(bad)),
        "right_rule": M.ratio(sum(x["expect"] in x["rules"] for x in bad), len(bad)),
        "false_intercept": M.ratio(sum(x["flagged"] for x in clean), len(clean)),
        "by_source": {src: block([x for x in bad if x["source"] == src]) for src in sorted({x["source"] for x in bad})},
        "false_by_source": {src: M.ratio(sum(x["flagged"] for x in clean if x["source"] == src), sum(x["source"] == src for x in clean))
                            for src in sorted({x["source"] for x in clean})},
        "by_category": {f"{src}/{cat}": block([x for x in bad if x["source"] == src and x["category"] == cat])
                        for src, cat in sorted({(x["source"], x["category"]) for x in bad})},
    }
    misses = [{"case": x["case"], "source": x["source"], "category": x["category"], "inserted": x["inserted"], "rules": x["rules"],
               "body": x["body"][-400:]} for x in bad if not x["flagged"]]
    false_alarms = [{"case": x["case"], "source": x["source"], "issues": [f"{i['rule']}: {i['detail']}" for i in x["issues"]], "body": x["body"][:600]}
                    for x in clean if x["flagged"]]
    return {"summary": summary, "misses": misses, "false_alarms": false_alarms, "samples": samples}
