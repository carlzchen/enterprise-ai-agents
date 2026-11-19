"""
PO 评测：订单 Agent（解析 → 抽取 → 料号映射 → 规则校验 → 变更比对 → 星空销售订单草稿）。

每个数据集先清掉流程写入的 PO 版本，再按邮件时间顺序处理全部 PO 附件（同一客户顺序跑，不同客户并行），处理完记下这一版
（相当于审批通过），后面的变更 / 重复邮件才有依据。和标准答案（ground_truth/pos.json，其中 expected_order 就是应录入的销售订单）比：

  抬头字段准确率     PO 号、下单日期、币种、PO 总额
  行级准确率         行数一致；逐行（客户料号、数量、单价、交期）全对；各字段的准确率
  整单完全正确       抬头 + 全部行都对
  料号映射命中率     登记过的料号映射到正确的内部物料号；没登记的料号，正确物料在候选第 1 / 前 3
  变更识别准确率     新单 / 变更 / 重复 + 版本号；变更单的逐项变化（行、字段、新值）P / R，新增行和取消行
  规则标记 P / R     （规则, 行号）逐项比，按规则分开
  分流准确率         可一键确认 ⇔ 标准答案没有任何问题
  ERP 草稿一致率     应该生成草稿的单（料号都能映射、不是重复单），草稿和标准销售订单逐行一致
"""
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from app.workflow import mailbox
from app.workflow.eval import metrics as M
from app.workflow.eval.datasets import Dataset, by_customer
from app.workflow.order_pipeline import agent as order_agent

HEADER = {"po_no": "po_no", "order_date": "order_date", "currency": "currency", "total_amount": "printed_total"}
LINE = {"customer_part_no": "customer_part_no", "qty": "qty", "unit_price": "unit_price", "delivery_date": "delivery_date"}


def _v(x):
    return x.isoformat() if hasattr(x, "isoformat") else x


def _line_eq(got, want: dict) -> dict[str, bool]:
    return {f: M.num_eq(_v(getattr(got, f)), want[g]) if f in ("qty", "unit_price") else str(_v(getattr(got, f)) or "") == str(want[g] or "")
            for f, g in LINE.items()}


def _change_items(ch) -> tuple[set, set, set]:
    """我方变更报告 → ({(行号, 字段, 新值)}, 新增行, 取消行)"""
    if ch is None:
        return set(), set(), set()
    return ({(c.line_no, c.field, M.norm_value(c.new)) for c in ch.changes}, set(ch.added_lines), set(ch.cancelled_lines))


def _gold_change(g: dict | None) -> tuple[set, set, set]:
    if not g:
        return set(), set(), set()
    return ({(c["line_no"], c["field"], M.norm_value(c["to"])) for c in g["changes"]}, set(g["added_lines"]), set(g["cancelled_lines"]))


def score(key: str, gold: dict, r) -> dict:
    """一个附件的逐项比对（纯函数，r 是 OrderAgentResult）"""
    po = r.extraction.po
    hdr = {f: (M.num_eq(_v(getattr(po.header, f)), gold[g]) if f == "total_amount" else str(_v(getattr(po.header, f)) or "") == str(gold[g] or ""))
           for f, g in HEADER.items()}
    lines = [_line_eq(a, b) for a, b in zip(po.lines, gold["lines"])]
    count_ok = len(po.lines) == len(gold["lines"])
    unknown = {f["line_no"] for f in gold["flags"] if f["rule"] == "unknown_part"}
    mapping = []                                                   # (是否登记过, 命中, 候选前1, 候选前3)
    for i, (m, g) in enumerate(zip(r.report.mappings, gold["lines"])):
        if g["line_no"] in unknown:
            top = [c.material for c in m.candidates]
            mapping.append({"known": False, "top1": top[:1] == [g["expected_material"]], "top3": g["expected_material"] in top[:3], "line_no": g["line_no"]})
        else:
            mapping.append({"known": True, "hit": m.material == g["expected_material"], "line_no": g["line_no"]})
    kind = "duplicate" if gold["duplicate_of"] else ("change" if gold["change"] else "new")
    got_ch, got_add, got_cancel = _change_items(r.change)
    want_ch, want_add, want_cancel = _gold_change(gold["change"])
    got_flags = [(i.rule, i.line_no) for i in r.report.issues]
    want_flags = [(f["rule"], f["line_no"]) for f in gold["flags"]]
    should_draft = kind != "duplicate" and not unknown
    exp = gold["expected_order"]
    draft_ok = None
    if should_draft:
        d = r.erp_draft
        draft_ok = bool(d) and d.customer_code == exp["customer_code"] and d.customer_po == exp["customer_po"] and str(d.order_date) == exp["order_date"] \
            and (d.currency or "") == (exp["currency"] or "") and len(d.lines) == len(exp["lines"]) \
            and all(a.material == b["material"] and M.num_eq(a.qty, b["qty"]) and M.num_eq(a.unit_price, b["unit_price"]) and str(a.delivery_date) == b["delivery_date"]
                    for a, b in zip(d.lines, exp["lines"]))
    return {
        "key": key, "layout": gold["layout"], "method": r.extraction.method, "attempts": r.extraction.attempts,
        "header": hdr, "line_count_ok": count_ok, "lines": lines, "lines_total": len(gold["lines"]),
        "doc_exact": all(hdr.values()) and count_ok and all(all(x.values()) for x in lines),
        "mapping": mapping, "kind_ok": (r.kind, r.version) == (kind, gold["version"]), "kind": r.kind, "want_kind": kind,
        "change": {"tp_fp_fn": M.multiset_prf(list(got_ch), list(want_ch)), "added_ok": got_add == want_add, "cancelled_ok": got_cancel == want_cancel,
                   "is_change": kind == "change", "exact": (got_ch, got_add, got_cancel) == (want_ch, want_add, want_cancel)},
        "flags": {"got": got_flags, "want": want_flags, "exact": sorted(got_flags) == sorted(want_flags)},
        "triage_ok": (r.triage == "one_click") == (not gold["flags"]),
        "should_draft": should_draft, "draft_ok": draft_ok, "unexpected_draft": (not should_draft) and r.erp_draft is not None,
    }


def _run_group(ds: Dataset, group: list[dict], llm_call) -> list[dict]:
    out = []
    for e in group:
        if not e["attachments"]:
            continue
        raw = ds.raw(e["id"])
        m = mailbox.parse(raw)
        who = mailbox.identify(m["from_addr"])
        cid = who["id"] if who.get("type") == "CUSTOMER" else None
        lang = "zh" if mailbox.language(m["body"] or m["subject"]) == "zh" else "en"
        for name, data in order_agent.attachments_of(raw):
            key = f"{e['id']}/{name}"
            t0 = time.time()
            try:
                r = order_agent.run(name, data, cid, lang=lang, reply_to=m["from_addr"], message_id=m["message_id"], llm_call=llm_call, record=True)
                out.append({**score(key, ds.pos[key], r), "seconds": round(time.time() - t0, 2)})
            except Exception as ex:
                out.append({"key": key, "layout": ds.pos[key]["layout"], "error": f"{type(ex).__name__}: {str(ex)[:200]}"})
    return out


def run(ds: Dataset, llm_call: Callable[[str], str] | None, workers: int = 4, limit: int = 0) -> dict:
    """llm_call 为空时用订单 Agent 默认的真实模型"""
    t0 = time.time()
    order_agent.reset_pipeline_versions()
    emails = ds.emails[: limit or None]
    try:
        with ThreadPoolExecutor(workers) as pool:
            rows = [x for part in pool.map(lambda g: _run_group(ds, g, llm_call), by_customer(emails)) for x in part]
    finally:
        order_agent.reset_pipeline_versions()
    rows.sort(key=lambda r: r["key"])
    ok = [r for r in rows if "error" not in r]
    lines = [x for r in ok for x in r["lines"]]
    maps = [x for r in ok for x in r["mapping"]]
    changes = [r for r in ok if r["change"]["is_change"]]
    ch = [sum(r["change"]["tp_fp_fn"][i] for r in ok) for i in range(3)]
    flag_per = M.per_label([(r["flags"]["got"], r["flags"]["want"]) for r in ok], key=lambda x: x[0])
    drafts = [r for r in ok if r["should_draft"]]

    def part(rs: list[dict]) -> dict:
        return {"docs": len(rs), "doc_exact": M.ratio(sum(r["doc_exact"] for r in rs), len(rs)),
                "line_exact": M.ratio(sum(all(x.values()) for r in rs for x in r["lines"]), sum(r["lines_total"] for r in rs))}

    summary = {
        "documents": len(rows), "errors": len(rows) - len(ok), "lines": sum(r["lines_total"] for r in ok), "seconds": round(time.time() - t0, 1),
        "header": {f: M.ratio(sum(r["header"][f] for r in ok), len(ok)) for f in HEADER},
        "line_count": M.ratio(sum(r["line_count_ok"] for r in ok), len(ok)),
        "line_exact": M.ratio(sum(all(x.values()) for x in lines), sum(r["lines_total"] for r in ok)),
        "line_field": {f: M.ratio(sum(x[f] for x in lines), sum(r["lines_total"] for r in ok)) for f in LINE},
        "doc_exact": M.ratio(sum(r["doc_exact"] for r in ok), len(ok)),
        "mapping_hit": M.ratio(sum(x["hit"] for x in maps if x["known"]), sum(x["known"] for x in maps)),
        "unknown_top1": M.ratio(sum(x["top1"] for x in maps if not x["known"]), sum(not x["known"] for x in maps)),
        "unknown_top3": M.ratio(sum(x["top3"] for x in maps if not x["known"]), sum(not x["known"] for x in maps)),
        "kind": M.ratio(sum(r["kind_ok"] for r in ok), len(ok)),
        "change_items": M.prf(*ch), "change_exact": M.ratio(sum(r["change"]["exact"] for r in changes), len(changes)),
        "flags_micro": M.micro(flag_per), "flags_per_rule": flag_per, "flags_doc_exact": M.ratio(sum(r["flags"]["exact"] for r in ok), len(ok)),
        "triage": M.ratio(sum(r["triage_ok"] for r in ok), len(ok)),
        "erp_draft": M.ratio(sum(bool(r["draft_ok"]) for r in drafts), len(drafts)),
        "unexpected_draft": sum(r["unexpected_draft"] for r in ok),
        "by_method": {m: part([r for r in ok if r["method"] == m]) for m in sorted({r["method"] for r in ok})},
        "by_layout": {lay: part([r for r in ok if r["layout"] == lay]) for lay in sorted({r["layout"] for r in ok})},
        "llm_retried": sum(1 for r in ok if r["method"] == "llm" and r["attempts"] > 1),
    }
    failures = []
    for r in rows:
        if "error" in r:
            failures.append({"key": r["key"], "error": r["error"]})
            continue
        why = []
        if not r["doc_exact"]:
            why.append("抽取：" + ", ".join([f for f, v in r["header"].items() if not v] + ([] if r["line_count_ok"] else ["行数"]) +
                                            [f"第{i + 1}行{f}" for i, x in enumerate(r["lines"]) for f, v in x.items() if not v]))
        if any((x["known"] and not x["hit"]) or (not x["known"] and not x["top3"]) for x in r["mapping"]):
            why.append("料号映射")
        if not r["kind_ok"]:
            why.append(f"新单/变更/重复：得到 {r['kind']}，应为 {r['want_kind']}")
        if r["change"]["is_change"] and not r["change"]["exact"]:
            why.append("变更明细")
        if not r["flags"]["exact"]:
            why.append(f"规则标记：得到 {sorted(r['flags']['got'])}，应为 {sorted(r['flags']['want'])}")
        if r["should_draft"] and not r["draft_ok"]:
            why.append("ERP 草稿和标准订单不一致")
        if r["unexpected_draft"]:
            why.append("不该生成 ERP 草稿")
        if why:
            failures.append({"key": r["key"], "layout": r["layout"], "method": r["method"], "why": why})
    return {"summary": summary, "failures": failures, "rows": rows}
