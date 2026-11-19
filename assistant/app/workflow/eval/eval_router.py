"""
路由评测：Supervisor 的意图识别和任务拆分（加上收信时的客户识别）。

逐封邮件：解析 → 认客户 → supervisor.split（大模型拆分 + 代码配附件），和标准答案的子任务比：
  客户识别准确率     认出的客户 = 标准答案的客户（干扰邮件应认不出）
  意图集合完全一致   这封邮件识别出的子任务类型集合 = 标准答案
  拆分完全一致       子任务多重集合（类型 + 对应的 PO 附件）= 标准答案；复合邮件单独统计
  各类型 P / R / F1  子任务按类型计数
  单号引用准确率     交期 / 加急子任务引用的 PO 号或我方订单号指向标准答案的订单；加急的要求日期一致
  干扰邮件           只给 other 的邮件里，真干扰邮件的精确率和召回率
"""
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.workflow import mailbox
from app.workflow.eval import metrics as M
from app.workflow.eval.datasets import Dataset
from app.workflow.graph import supervisor as SV


def _mail(raw: bytes) -> tuple[dict, int | None]:
    m = mailbox.parse(raw)
    who = mailbox.identify(m["from_addr"])
    mail = {"message_id": m["message_id"], "from_addr": m["from_addr"], "subject": m["subject"], "body": m["body"], "received_at": str(m["received_at"]),
            "attachments": [a["name"] for a in m["attachments"]]}
    return mail, who["id"] if who.get("type") == "CUSTOMER" else None


def _gold(e: dict) -> list[tuple[str, str | None]]:
    return sorted((st["type"], st["refs"]["attachment"].split("/", 1)[1] if st["refs"].get("attachment") else None) for st in e["subtasks"])


def one(ds: Dataset, e: dict, llm_call: Callable[[str], str]) -> dict:
    t0 = time.time()
    mail, cid = _mail(ds.raw(e["id"]))
    row = {"id": e["id"], "scenario": e["scenario"], "customer_ok": cid == e["customer_id"], "want": _gold(e)}
    try:
        subtasks, info = SV.split(mail, llm_call)
    except Exception as ex:
        return {**row, "error": f"{type(ex).__name__}: {str(ex)[:200]}", "got": [], "seconds": round(time.time() - t0, 2)}
    row.update(got=sorted((s.type, s.refs.attachment) for s in subtasks), attempts=info["attempts"], seconds=round(time.time() - t0, 2),
               subtasks=[s.model_dump(mode="json") for s in subtasks])
    row["intent_ok"] = {t for t, _ in row["got"]} == {t for t, _ in row["want"]}
    row["split_ok"] = row["got"] == row["want"]
    refs = []                                                  # 交期 / 加急：引用的单号指没指对
    for gst in e["subtasks"]:
        if gst["type"] not in ("delivery_query", "expedite"):
            continue
        g = gst["refs"]
        cands = [s for s in subtasks if s.type == gst["type"]]
        hit = next((s for s in cands if (s.refs.po_no and s.refs.po_no.strip() == g["customer_po"]) or (s.refs.order_no and s.refs.order_no.strip() == g["order_no"])), None)
        ref = {"type": gst["type"], "order_ok": hit is not None}
        if gst["type"] == "expedite":
            ref["date_ok"] = bool(hit and hit.refs.requested_date and str(hit.refs.requested_date) == g["requested_date"])
        refs.append(ref)
    row["refs"] = refs
    return row


def run(ds: Dataset, llm_call: Callable[[str], str], workers: int = 4, limit: int = 0) -> dict:
    emails = ds.emails[: limit or None]
    t0 = time.time()
    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(lambda e: one(ds, e, llm_call), emails))
    ok = [r for r in rows if "error" not in r]
    compound = [r for r in ok if r["scenario"] == "compound"]
    refs = [x for r in ok for x in r["refs"]]
    per_type = M.per_label([([t for t, _ in r["got"]], [t for t, _ in r["want"]]) for r in rows])
    spam = M.per_label([([t for t, _ in r["got"]] == ["other"] and ["spam"] or [], r["scenario"] == "spam" and ["spam"] or []) for r in rows]).get("spam", M.prf(0, 0, 0))
    summary = {
        "emails": len(rows), "subtasks": sum(len(r["want"]) for r in rows), "errors": len(rows) - len(ok),
        "customer_id": M.ratio(sum(r["customer_ok"] for r in rows), len(rows)),
        "intent_exact": M.ratio(sum(r.get("intent_ok", False) for r in rows), len(rows)),
        "split_exact": M.ratio(sum(r.get("split_ok", False) for r in rows), len(rows)),
        "split_exact_compound": M.ratio(sum(r["split_ok"] for r in compound), sum(r["scenario"] == "compound" for r in rows)),
        "subtask_micro": M.micro(per_type), "per_type": per_type, "spam": spam,
        "order_ref": M.ratio(sum(x["order_ok"] for x in refs), len(refs)),
        "expedite_date": M.ratio(sum(x["date_ok"] for x in refs if x["type"] == "expedite"), sum(x["type"] == "expedite" for x in refs)),
        "retried": sum(1 for r in ok if r.get("attempts", 1) > 1), "seconds": round(time.time() - t0, 1),
    }
    failures = [{"id": r["id"], "scenario": r["scenario"], "want": r["want"], "got": r.get("got"), "error": r.get("error"),
                 "refs": [x for x in r.get("refs", []) if not all(v for k, v in x.items() if k != "type")], "customer_ok": r["customer_ok"]}
                for r in rows if r.get("error") or not r.get("split_ok") or not r["customer_ok"] or not all(all(v for k, v in x.items() if k != "type") for x in r.get("refs", []))]
    return {"summary": summary, "failures": failures, "rows": rows}
