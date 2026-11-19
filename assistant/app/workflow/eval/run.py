"""
一条命令跑全部评测并生成报告：

    PYTHONPATH=. .venv/bin/python -m app.workflow.eval.run                      # 真实模型，三个数据集，三套评测
    PYTHONPATH=. .venv/bin/python -m app.workflow.eval.run --fake               # 假模型自检（几十秒，不调大模型）
    PYTHONPATH=. .venv/bin/python -m app.workflow.eval.run --sets test_2027 --suites router,po --limit 20

需要本机 MiniERP 的 MySQL（docker compose up -d mysql）和生成好的数据集（见 datasets.py）。
评测期间流程写入的 PO 版本、审计会在结束时清掉；外发邮件、ERP、待办换成记录器，不进跟单工作台。
"""
import argparse
import sys
import time
from datetime import datetime

from app.infra.config.settings import settings
from app.workflow.eval import datasets, eval_agents, eval_po, eval_router, fakes, report
from app.workflow.graph import build as B


def main(argv: list[str] | None = None) -> str:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="dev,test_2027,test_2028")
    ap.add_argument("--suites", default="router,po,agents")
    ap.add_argument("--fake", action="store_true", help="假模型自检：数字不是效果指标")
    ap.add_argument("--limit", type=int, default=0, help="每个数据集只跑前 N 封邮件（调试用）")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-reviewer", action="store_true", help="跳过审核 Agent 的构造样本评测")
    a = ap.parse_args(argv)
    names, suites = a.sets.split(","), a.suites.split(",")
    t0 = time.time()
    sets = {n: datasets.load(n) for n in names}
    meta = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "fake": a.fake, "model": settings.llm_model, "fallback_model": settings.llm_fallback_model,
            "code": report.code_fingerprint(), "held_out": list(datasets.HELD_OUT), "command": "python -m app.workflow.eval.run " + " ".join(argv if argv is not None else sys.argv[1:]),
            "datasets": {n: {"emails": len(ds.emails[: a.limit or None]), "po_documents": len([k for k in ds.pos if not a.limit or k.split("/")[0] in
                                                                                               {e["id"] for e in ds.emails[: a.limit]}]),
                             "seed": ds.manifest.get("seed")} for n, ds in sets.items()}}
    results: dict = {s: {} for s in suites}
    for n, ds in sets.items():
        split = fakes.GTSplit(ds) if a.fake else B._default_json_llm
        if "router" in suites:
            print(f"[{n}] 路由评测……", flush=True)
            results["router"][n] = eval_router.run(ds, split, a.workers, a.limit)
        if "po" in suites:
            print(f"[{n}] PO 评测……", flush=True)
            results["po"][n] = eval_po.run(ds, fakes.GTExtract(ds) if a.fake else None, a.workers, a.limit)
        if "agents" in suites:
            print(f"[{n}] Agent 评测……", flush=True)
            deps = B.Deps(llm_json=fakes.Writer(split), order_llm=fakes.GTExtract(ds), agent_model=fakes.DeliveryModel(), tone_llm=None) if a.fake else B.Deps()
            r = eval_agents.run(ds, deps, a.workers, a.limit)
            if not a.no_reviewer:
                print(f"[{n}] 审核 Agent 评测……", flush=True)
                r["reviewer"] = eval_agents.run_reviewer(r["cases"], None if a.fake else B._default_json_llm, tone_llm=deps.tone_llm, workers=a.workers)
            results["agents"][n] = r
    meta["seconds"] = round(time.time() - t0, 1)
    out = report.write(meta, results)
    print(f"报告：{out / 'summary.md'}")
    return str(out)


if __name__ == "__main__":
    main()
