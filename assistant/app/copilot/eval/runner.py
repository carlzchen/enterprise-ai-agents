"""
问数评测：uv run python -m app.copilot.eval.runner [--limit N] [--workers 4] [--intent 考勤工时]

逐条跑评测集（直接调用主流程，不经过 HTTP），从这些维度打分，报告写到 output/eval/copilot_*.md 和 .json：
  意图识别准确率   识别出的意图大类和标注一致（应拒绝的题：被拒绝即算对）
  文档召回准确率   制度题：标注的制度出现在检索结果里
  SQL 执行成功率   问数题：SQL 通过校验并执行成功
  指标口径一致率   问数题：命中了标注的模板（模板 SQL 就是口径库的定义），或走 SQL 生成且结果和标准 SQL 一致
  结果准确率       标准答案的数字出现在结果的目标行里；制度题看回答是否包含全部关键内容；权限题看是否被拒绝、有没有泄露
  响应耗时         P50 / P95
"""
import argparse
import json
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime

os.environ.setdefault("COPILOT_TODAY", "2026-10-06")     # 固定"今天"，评测结果可复现（要在读配置之前设置）

from app.copilot import memory, pipeline, policy, retriever                     # noqa: E402
from app.copilot.eval.dataset import FIRST_ROW, Case, build, summary                    # noqa: E402
from app.copilot.permission import load_user                                 # noqa: E402
from app.copilot.semantic.loader import semantic                             # noqa: E402
from app.shared.runtime.logger import PROJECT_ROOT, logger                   # noqa: E402

ROLES = {"hr01": "HR", "gm": "MANAGEMENT", "fin01": "FINANCE", "prod01": "PRODUCTION", "sales01": "SALES", "eng01": "ENGINEER"}
EXPECT_INTENT = {"policy": "制度问答", "value": "经营问数", "rowcount": "经营问数", "clarify": "追问澄清"}
OUT_DIR = PROJECT_ROOT / "output" / "eval"


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.00011, 0.002 * abs(b))


def find_value(payload: dict, expected: float, match: list[str]) -> bool:
    """标准答案的数字是否出现在目标行里。没有指定目标行时，只接受一两行的结果，避免在大表里碰巧撞上"""
    tables = [payload.get("result")] + [e["result"] for e in payload.get("extra", [])]
    for t in tables:
        if not t:
            continue
        rows = t["rows"][:1] if match == [FIRST_ROW] else t["rows"]
        for row in rows:
            text = " ".join(str(v) for v in row.values())
            if match and match != [FIRST_ROW] and not all(m in text for m in match):
                continue
            if not match and len(t["rows"]) > 2:
                continue
            if any(isinstance(v, (int, float)) and not isinstance(v, bool) and _close(float(v), expected) for v in row.values()):
                return True
    return False


def run_case(c: Case) -> dict:
    ctx = load_user(c.user, ROLES[c.user])
    sid, payload, t0 = "eval-" + uuid.uuid4().hex[:12], None, time.time()
    try:
        for i, q in enumerate(c.turns):
            last = i == len(c.turns) - 1
            payload = pipeline.run(q, ctx, sid, options={"no_history": True, "no_audit": True, "no_cache": True,
                                                      "skip_answer": not (last and c.kind == "policy")})
            if last:
                elapsed = payload["elapsed_ms"]
    except Exception as e:
        logger.exception(f"评测题 {c.id} 执行异常")
        return {"id": c.id, "intent": c.intent, "kind": c.kind, "path": c.path, "question": " → ".join(c.turns), "user": c.user,
                "error": str(e)[:200], "intent_ok": False, "ok": False}
    finally:
        memory.clear(sid)
    p = payload
    out = {"id": c.id, "intent": c.intent, "kind": c.kind, "path": c.path, "user": c.user, "question": " → ".join(c.turns),
           "status": p["status"], "got_intent": p["intent"], "tool": p["tool"], "route_by": p["route_by"], "template_id": p["template_id"],
           "repairs": p["repairs"], "elapsed_ms": elapsed, "tokens": p["usage"]["total"], "llm_calls": p["usage"]["calls"],
           "rows": len(p["result"]["rows"]) if p.get("result") else 0, "sql": p.get("sql"), "reason": p.get("reason"),
           "expected": c.expected, "match": c.match, "total_ms": int((time.time() - t0) * 1000)}
    if c.kind == "policy":
        titles = [s["doc_title"] for s in p.get("sources", [])]
        out["intent_ok"] = p["intent"] == "制度问答"
        out["doc_ok"] = (c.doc in titles) if c.doc else True
        answer = p["answer"].replace(" ", "")
        out["ok"] = out["intent_ok"] and all(k.replace(" ", "") in answer for k in c.keywords)
        out["answer"] = p["answer"][:300]
    elif c.kind == "denied":
        out["intent_ok"] = out["ok"] = p["status"] == "DENIED"
    elif c.kind == "noleak":                        # 被拒绝、追问，或者返回的不是全量的那个数，都算没有泄露
        leaked = p["status"] == "OK" and c.expected is not None and c.expected >= 0 and find_value(p, c.expected, [])
        if c.expected is not None and c.expected < 0:           # 没有具体数字可比：只要没有返回任何数据行就算没泄露
            leaked = p["status"] == "OK" and bool(p.get("result")) and len(p["result"]["rows"]) > 0
        out["intent_ok"] = out["ok"] = not leaked
    elif c.kind == "clarify":
        out["intent_ok"] = out["ok"] = p["status"] == "CLARIFY"
    else:
        want = "测算预警" if c.path == "analysis" else "经营问数"
        out["intent_ok"] = p["intent"] == want
        out["sql_ok"] = p["status"] == "OK" and bool(p.get("sql"))
        if c.kind == "rowcount":
            out["ok"] = out["sql_ok"] and out["rows"] == int(c.expected)
        else:
            out["ok"] = out["sql_ok"] and find_value(p, c.expected, c.match)
        if c.path != "analysis":
            out["metric_ok"] = (p["template_id"] in c.templates) if p["template_id"] else out["ok"]
            out["template_hit"] = bool(p["template_id"])
    return out


def _rate(rows: list[dict], key: str) -> tuple[int, int, float]:
    xs = [r for r in rows if key in r]
    hit = sum(1 for r in xs if r[key])
    return hit, len(xs), (hit / len(xs) if xs else 0.0)


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def report(rows: list[dict], cases: list[Case], seconds: float) -> tuple[str, dict]:
    lat = sorted(r["elapsed_ms"] for r in rows if "elapsed_ms" in r)
    p = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))] if lat else 0     # noqa: E731
    metrics = {
        "评测题数": len(rows),
        "意图识别准确率": _rate(rows, "intent_ok"), "文档召回准确率": _rate(rows, "doc_ok"), "SQL 执行成功率": _rate(rows, "sql_ok"),
        "指标口径一致率": _rate(rows, "metric_ok"), "结果准确率": _rate(rows, "ok"), "模板命中率": _rate(rows, "template_hit"),
    }
    data = [r for r in rows if "sql_ok" in r]
    stats = {
        "latency_ms": {"p50": p(0.5), "p95": p(0.95), "avg": int(statistics.mean(lat)) if lat else 0},
        "tokens_avg": int(statistics.mean(r["tokens"] for r in rows if "tokens" in r)),
        "route": {k: sum(1 for r in rows if r.get("route_by") == k) for k in ("rule", "llm", "cache")},
        "repaired": sum(1 for r in data if r.get("repairs")),
        "by_path": {k: _rate([r for r in data if r["path"] == k], "ok") for k in ("template", "sql", "analysis")},
    }
    lines = [f"# 问数评测报告（{datetime.now():%Y-%m-%d %H:%M}）", "",
             f"评测集 {len(rows)} 题，其中多轮追问 {sum(len(c.turns) > 1 for c in cases)} 题；用时 {seconds:.0f} 秒。固定今天为 {os.environ['COPILOT_TODAY']}。", "",
             "## 总体指标", "", "| 指标 | 通过 / 题数 | 比率 |", "|---|---|---|"]
    for name, v in metrics.items():
        if name != "评测题数":
            lines.append(f"| {name} | {v[0]} / {v[1]} | {_pct(v[2])} |")
    lines += ["", f"- 响应耗时（最后一轮，不含文字回答生成；制度题含回答）：P50 {stats['latency_ms']['p50']} ms，P95 {stats['latency_ms']['p95']} ms，平均 {stats['latency_ms']['avg']} ms",
              f"- 平均 Token：{stats['tokens_avg']}",
              f"- 意图路由方式：规则 {stats['route']['rule']} 题，模型 {stats['route']['llm']} 题",
              f"- SQL 被校验打回后由模型修正成功或失败的题数：{stats['repaired']}",
              "- 按路径的结果准确率：" + "，".join(f"{ {'template': '模板', 'sql': 'SQL 生成', 'analysis': '归因测算'}[k]} {v[0]}/{v[1]}（{_pct(v[2])}）" for k, v in stats["by_path"].items() if v[1]),
              "", "## 分意图", "", "| 意图 | 题数 | 意图识别 | 结果准确 | 结果准确率 |", "|---|---|---|---|---|"]
    intents = list(dict.fromkeys(c.intent for c in cases))
    by_intent = {}
    for it in intents:
        rs = [r for r in rows if r["intent"] == it]
        if not rs:
            continue
        i_ok, ok = sum(1 for r in rs if r["intent_ok"]), sum(1 for r in rs if r["ok"])
        by_intent[it] = {"n": len(rs), "intent_ok": i_ok, "ok": ok}
        lines.append(f"| {it} | {len(rs)} | {i_ok} | {ok} | {_pct(ok / len(rs))} |")
    bad = [r for r in rows if not r["ok"]]
    lines += ["", f"## 没通过的题（{len(bad)} 题）", ""]
    for r in bad:
        lines.append(f"- **{r['id']}**（{r['user']}）{r['question']}")
        detail = r.get("error") or f"状态 {r.get('status')}，意图 {r.get('got_intent')}，工具 {r.get('tool')}，模板 {r.get('template_id')}，期望 {r.get('expected')} {r.get('match') or ''}"
        if r.get("reason"):
            detail += f"，原因：{r['reason'][:120]}"
        if r.get("answer"):
            detail += f"，回答：{r['answer'][:120]}"
        lines.append(f"  - {detail}")
        if r.get("sql") and r.get("kind") in ("value", "rowcount"):
            lines.append(f"  - SQL：`{r['sql'][:300]}`")
    js = {"metrics": {k: (v if isinstance(v, int) else {"hit": v[0], "n": v[1], "rate": round(v[2], 4)}) for k, v in metrics.items()},
          "stats": stats, "by_intent": by_intent, "dataset": summary(cases)}
    return "\n".join(lines) + "\n", js


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--intent", default="")
    ap.add_argument("--ids", default="")
    args = ap.parse_args()
    semantic.load()
    retriever.ensure_index()
    policy.ingest_all()
    cases = build()
    print(json.dumps(summary(cases), ensure_ascii=False))
    if args.intent:
        cases = [c for c in cases if c.intent == args.intent]
    if args.ids:
        cases = [c for c in cases if any(c.id.startswith(x) for x in args.ids.split(","))]
    if args.limit:
        cases = cases[::max(1, len(cases) // args.limit)][:args.limit]
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(run_case, cases))
    md, js = report(rows, cases, time.time() - t0)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (OUT_DIR / f"copilot_{stamp}.md").write_text(md, encoding="utf-8")
    (OUT_DIR / f"copilot_{stamp}.json").write_text(json.dumps({"summary": js, "rows": rows, "cases": [asdict(c) for c in cases]}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(md[:6000])
    print(f"报告：{OUT_DIR / f'copilot_{stamp}.md'}")


if __name__ == "__main__":
    main()
