"""
性能对照：uv run python -m app.copilot.eval.bench

同一批问题分别用两种方式跑，量出首字响应时间和 Token 消耗：
  优化前（baseline）  不用规则路由、把全部模板和全部视图结构都发给模型、不压缩历史、不用缓存、不做流式（等完整回答生成完才返回）
  优化后              规则路由 + 模板筛选 + Schema 裁剪 + 单行结果不调模型 + 流式输出；再跑第二遍看缓存命中后的表现
首字响应时间 = 从收到问题到第一段回答文字推给用户；不做流式时就是整个回答生成完的时间。
"""
import json
import os
import statistics
import threading
import time
import uuid
from datetime import datetime

os.environ.setdefault("COPILOT_TODAY", "2026-10-06")

from app.copilot import memory, pipeline, policy, retriever                     # noqa: E402
from app.copilot.permission import load_user                                 # noqa: E402
from app.copilot.semantic.loader import semantic                             # noqa: E402
from app.shared.runtime.logger import PROJECT_ROOT                           # noqa: E402
from app.shared.utils.sse_utils import SSEEvent, close_session, create_sse_queue  # noqa: E402

QUESTIONS = [
    ("hr01", "HR", ["上个月伺服线的加班率是多少？", "那步进线呢？", "换成按班组看"]),
    ("hr01", "HR", ["上个月各产线的加班率"]), ("hr01", "HR", ["今年每个月的离职率"]), ("hr01", "HR", ["各部门现在分别有多少人？"]),
    ("hr01", "HR", ["上个月全公司的出勤率是多少？", "按部门看"]), ("hr01", "HR", ["2026年8月各产线计件工资和保底补差"]),
    ("hr01", "HR", ["今年大家为什么离职？"]), ("hr01", "HR", ["2026年8月各部门人均工资"]), ("hr01", "HR", ["上个月谁加班最多？"]),
    ("hr01", "HR", ["迟到多久算旷工？"]), ("hr01", "HR", ["病假工资怎么发，需要什么证明？"]), ("hr01", "HR", ["报销超过 5000 元要谁审批？"]),
    ("hr01", "HR", ["司龄不到三个月就离职的人里，各产线分别有多少？"]), ("hr01", "HR", ["上个月加班超过 30 小时的有多少人？"]),
    ("gm", "MANAGEMENT", ["三季度各产品系列的收入、成本和毛利率", "只看智能驱控事业部"]), ("gm", "MANAGEMENT", ["三季度毛利为什么比去年同期低？"]),
    ("gm", "MANAGEMENT", ["今年每个月的毛利率"]), ("gm", "MANAGEMENT", ["上个月各产线产量和良率"]), ("gm", "MANAGEMENT", ["各产线上月 OEE"]),
    ("fin01", "FINANCE", ["哪些客户回款逾期了？"]), ("fin01", "FINANCE", ["今年每个月回款金额"]), ("fin01", "FINANCE", ["2026年8月的直接人工成本率是多少？"]),
    ("fin01", "FINANCE", ["如果计件单价上调 5%，每月人工成本增加多少？"]), ("fin01", "FINANCE", ["三季度外销收入占总收入的比例是多少？"]),
    ("fin01", "FINANCE", ["今年销售额最大的十个客户"]), ("prod01", "PRODUCTION", ["上个月各产线出勤率"]),
    ("sales01", "SALES", ["今年各月销售额"]), ("eng01", "ENGINEER", ["我最近三个月的工资是多少？"]),
]


def ask(q: str, ctx, sid: str, baseline: bool, no_cache: bool) -> dict:
    """跑一次提问。优化后用流式并记下第一段文字的时间；优化前不做流式"""
    t0 = time.time()
    if baseline:
        p = pipeline.run(q, ctx, sid, stream=False, options={"baseline": True, "no_history": True, "no_audit": True})
        ms = int((time.time() - t0) * 1000)
        return {"first_token_ms": ms, "total_ms": ms, "tokens": p["usage"]["total"], "calls": p["usage"]["calls"], "status": p["status"]}
    rid = uuid.uuid4().hex
    queue, first, box = create_sse_queue(rid), [], {}

    def work():
        try:
            box["p"] = pipeline.run(q, ctx, sid, stream=True, request_id=rid, options={"no_history": True, "no_audit": True, "no_cache": no_cache})
        finally:
            close_session(rid)

    th = threading.Thread(target=work)
    th.start()
    while True:
        msg = queue.get()
        if msg["event"] == SSEEvent.CLOSE:
            break
        if msg["event"] == SSEEvent.DELTA and not first:
            first.append(int((time.time() - t0) * 1000))
    th.join()
    total = int((time.time() - t0) * 1000)
    p = box["p"]
    return {"first_token_ms": first[0] if first else total, "total_ms": total, "tokens": p["usage"]["total"], "calls": p["usage"]["calls"],
            "status": p["status"], "route_by": p["route_by"], "cache_hit": p["cache_hit"]}


def run_mode(name: str, baseline: bool, no_cache: bool, sessions: dict) -> list[dict]:
    rows = []
    for i, (user, role, turns) in enumerate(QUESTIONS):
        ctx = load_user(user, role)
        sid = sessions.setdefault((name, i), f"bench-{uuid.uuid4().hex[:10]}")
        memory.clear(sid)
        for q in turns:
            r = ask(q, ctx, sid, baseline, no_cache)
            rows.append({"mode": name, "user": user, "question": q, **r})
            print(f"[{name}] {r['first_token_ms']:>6} ms 首字 {r['total_ms']:>6} ms 总 {r['tokens']:>6} tok {r['calls']} 次调用  {q}", flush=True)
        memory.clear(sid)
    return rows


def stat(rows: list[dict]) -> dict:
    ft = sorted(r["first_token_ms"] for r in rows)
    tk = [r["tokens"] for r in rows]
    q = lambda p: ft[min(len(ft) - 1, int(len(ft) * p))]     # noqa: E731
    return {"n": len(rows), "first_token_p50": q(0.5), "first_token_p95": q(0.95), "first_token_avg": int(statistics.mean(ft)),
            "total_avg": int(statistics.mean(r["total_ms"] for r in rows)), "tokens_avg": int(statistics.mean(tk)),
            "tokens_max": max(tk), "llm_calls_avg": round(statistics.mean(r["calls"] for r in rows), 2)}


def main():
    semantic.load()
    retriever.ensure_index()
    policy.ingest_all()
    sessions: dict = {}
    ask("各部门现在分别有多少人？", load_user("hr01", "HR"), "bench-warm", False, True)       # 预热连接
    out = {"baseline": run_mode("优化前", True, True, sessions), "optimized": run_mode("优化后", False, True, sessions),
           "warm": run_mode("优化后-缓存命中", False, False, sessions)}
    out["warm"] = run_mode("优化后-缓存命中", False, False, sessions)             # 第一遍写缓存，第二遍才是命中后的表现
    summary = {k: stat(v) for k, v in out.items()}
    lines = [f"# 性能对照（{datetime.now():%Y-%m-%d %H:%M}）", "", f"{len(out['baseline'])} 次提问（含多轮追问），同一批问题跑三种方式。", "",
             "| 方式 | 首字响应 P50 | 首字响应 P95 | 平均总耗时 | 平均 Token | 最大 Token | 平均模型调用次数 |", "|---|---|---|---|---|---|---|"]
    names = {"baseline": "优化前（不流式、不裁剪、不缓存）", "optimized": "优化后（缓存未命中）", "warm": "优化后（缓存命中）"}
    for k, s in summary.items():
        lines.append(f"| {names[k]} | {s['first_token_p50'] / 1000:.2f} s | {s['first_token_p95'] / 1000:.2f} s | {s['total_avg'] / 1000:.2f} s | "
                     f"{s['tokens_avg']} | {s['tokens_max']} | {s['llm_calls_avg']} |")
    lines += ["", "## 明细", "", "| 问题 | 优化前首字 | 优化后首字 | 缓存命中首字 | 优化前 Token | 优化后 Token |", "|---|---|---|---|---|---|"]
    for a, b, c in zip(out["baseline"], out["optimized"], out["warm"]):
        lines.append(f"| {a['question']} | {a['first_token_ms']} ms | {b['first_token_ms']} ms | {c['first_token_ms']} ms | {a['tokens']} | {b['tokens']} |")
    d = PROJECT_ROOT / "output" / "eval"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (d / f"bench_{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / f"bench_{stamp}.json").write_text(json.dumps({"summary": summary, "rows": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(lines[:9]))
    print(f"报告：{d / f'bench_{stamp}.md'}")


if __name__ == "__main__":
    main()
