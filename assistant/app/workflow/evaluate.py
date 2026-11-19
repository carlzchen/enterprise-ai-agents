"""
邮件理解的评测：python -m app.workflow.evaluate
把 data/workflow_mailbox/eval 里的邮件逐封做「识别发件人 + 意图分类 + 字段抽取」，和 labels.json 里的标注对比。不写数据库，不触发后续流程。
指标：
  意图完全一致   这封邮件识别出的意图集合和标注一模一样
  各意图的精确率和召回率
  发件人识别     客户或供应商认得对不对；该认不出的是不是没有乱认
  字段准确率     标注了的字段逐个比对（数值相等、文本相同）
"""
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.shared.runtime.logger import PROJECT_ROOT
from app.workflow import mailbox, store, understand as U

ROOT = PROJECT_ROOT / "data" / "workflow_mailbox"


def _eq(got, want) -> bool:
    if want is None:
        return got in (None, "", 0)
    if isinstance(want, (int, float)):
        try:
            return abs(float(got) - float(want)) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(got or "").strip().upper().replace(" ", "") == str(want).strip().upper().replace(" ", "")


def one(name: str, label: dict) -> dict:
    raw = (ROOT / "eval" / f"{name}.eml").read_bytes()
    m = mailbox.parse(raw)
    real = mailbox.forwarded_sender(m["body"])
    who = mailbox.identify(real or m["from_addr"])
    mail = {"from_addr": m["from_addr"], "from_name": m["from_name"], "subject": m["subject"], "attachments": m["attachments"], "party_name": who["name"],
            "body_new": mailbox.strip_quotes(mailbox.forwarded_body(m["body"]) if real else m["body"])}
    t = time.time()
    try:
        a = U.analyze(mail)
    except Exception as e:
        return {"name": name, "error": str(e)[:200], "intents": [], "want": label["intents"], "party_ok": False, "fields": {}}
    got = sorted(i.intent for i in a.intents if i.confidence >= 0.5)
    if len(got) > 1 and "other" in got:
        got.remove("other")
    party_ok = (who["type"] == "UNKNOWN") if label.get("unknown") or not (label.get("customer") or label.get("supplier")) else \
        (who["type"] == "SUPPLIER") if label.get("supplier") else (who["type"] == "CUSTOMER" and who["id"] == label["customer"])
    item = U.normalize_item(a.inquiry[0].model_dump()) if a.inquiry else {}
    po, line = (a.po.model_dump(mode="json") if a.po else {}), {}
    if a.po and a.po.lines:
        line = U.normalize_item({"model": a.po.lines[0].model}) | {"qty": a.po.lines[0].qty, "unit_price": a.po.lines[0].unit_price}
    ctr = a.counter.model_dump() if a.counter else {}
    if ctr.get("model"):
        ctr["model"] = U.normalize_item({"model": ctr["model"]})["model"]
    dlv = a.delivery.model_dump(mode="json") if a.delivery else {}
    fields = {}
    for k, want in (label.get("fields") or {}).items():
        if k in ("po_no", "required_date", "payment_days"):
            got_v = po.get(k)
        elif k in ("order_no", "new_date"):
            got_v = dlv.get(k)
        elif k in ("qty", "unit_price") and "purchase_order" in label["intents"]:
            got_v = line.get(k)
        elif k in ("target_price", "quote_no", "qty") and "counter_offer" in label["intents"]:
            got_v = ctr.get(k)
        elif k == "model":
            got_v = line.get("model") or ctr.get("model") or item.get("model")
        elif k == "po_no_d":
            got_v = dlv.get("po_no")
        else:
            got_v = item.get(k)
        if k == "po_no" and "delivery_query" in label["intents"] and "purchase_order" not in label["intents"]:
            got_v = dlv.get("po_no")
        fields[k] = {"want": want, "got": got_v, "ok": _eq(got_v, want)}
    items_ok = None
    if label.get("items"):
        n = len(a.po.lines) if "purchase_order" in label["intents"] and a.po else len(a.inquiry)
        items_ok = n == label["items"]
    return {"name": name, "intents": got, "want": sorted(label["intents"]), "confidence": {i.intent: i.confidence for i in a.intents}, "party_ok": party_ok,
            "party": who["type"], "fields": fields, "items_ok": items_ok, "seconds": round(time.time() - t, 1), "summary": a.summary}


def main(workers: int = 4) -> dict:
    store.ensure()
    from app.workflow import flows
    flows.seed_reference()
    labels = json.loads((ROOT / "labels.json").read_text(encoding="utf-8"))
    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(lambda kv: one(*kv), labels.items()))
    tp, fp, fn = Counter(), Counter(), Counter()
    for r in rows:
        for i in set(r["intents"]) & set(r["want"]):
            tp[i] += 1
        for i in set(r["intents"]) - set(r["want"]):
            fp[i] += 1
        for i in set(r["want"]) - set(r["intents"]):
            fn[i] += 1
    per = {i: {"标注": tp[i] + fn[i], "精确率": round(tp[i] / (tp[i] + fp[i]), 3) if tp[i] + fp[i] else None, "召回率": round(tp[i] / (tp[i] + fn[i]), 3) if tp[i] + fn[i] else None}
           for i in U.INTENTS if tp[i] + fp[i] + fn[i]}
    fcount = Counter()
    for r in rows:
        for k, v in r["fields"].items():
            fcount[(k, v["ok"])] += 1
    keys = sorted({k for k, _ in fcount})
    field_acc = {k: {"n": fcount[(k, True)] + fcount[(k, False)], "正确": fcount[(k, True)]} for k in keys}
    total_f = sum(v["n"] for v in field_acc.values())
    items = [r["items_ok"] for r in rows if r.get("items_ok") is not None]
    out = {"n": len(rows), "errors": sum(1 for r in rows if r.get("error")), "意图完全一致": sum(r["intents"] == r["want"] for r in rows),
           "发件人识别正确": sum(r["party_ok"] for r in rows), "各意图": per, "字段": field_acc, "字段合计": {"n": total_f, "正确": sum(v["正确"] for v in field_acc.values())},
           "多行明细数量正确": f"{sum(items)}/{len(items)}", "平均耗时秒": round(sum(r.get("seconds", 0) for r in rows) / len(rows), 1),
           "不一致的邮件": [{"name": r["name"], "应为": r["want"], "识别为": r["intents"], "error": r.get("error")} for r in rows if r["intents"] != r["want"]],
           "字段错误": [{"name": r["name"], "field": k, **{x: v[x] for x in ("want", "got")}} for r in rows for k, v in r["fields"].items() if not v["ok"]]}
    (ROOT / "eval_result.json").write_text(json.dumps({"summary": out, "rows": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return out


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
