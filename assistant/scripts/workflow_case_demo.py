"""
跟单图演示：按时间顺序把 data/workflow_mock/inbox 的邮件逐封跑完整张图（真实大模型），每个案例自动「批准」，打印拆分、各 Agent 结果、审核轮次。

    PYTHONPATH=. .venv/bin/python scripts/workflow_case_demo.py [--limit N] [--only E013,E022]

自动批准是为了让 PO 版本按顺序记下来（后面的变更 / 重复邮件要用）。外发邮件、ERP 草稿、待办都换成记录器，不进跟单工作台；
审计记在 actor = 'demo' 名下，每次运行前清掉。结果写到 data/workflow_mock/case_graph_run.json。
末尾和标准答案比一下拆分——这是开发自查，不是正式评测，正式指标以阶段 4 的评测报告为准。
"""
import argparse
import base64
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.types import Command  # noqa: E402

from app.shared.runtime.logger import PROJECT_ROOT  # noqa: E402
from app.workflow import adapters, store  # noqa: E402
from app.workflow.adapters.mock import FolderEmail, MiniErpERP  # noqa: E402
from app.workflow.graph import build as B  # noqa: E402
from app.workflow.order_pipeline import agent as order_agent  # noqa: E402

ROOT = PROJECT_ROOT / "data" / "workflow_mock"


class ListTask:
    def __init__(self):
        self.created = []

    def create_task(self, req) -> str:
        self.created.append(req.model_dump())
        return f"TK-DEMO-{len(self.created)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    emails = json.loads((ROOT / "ground_truth" / "emails.json").read_text(encoding="utf-8"))
    if a.only:
        emails = [e for e in emails if e["id"] in a.only.split(",")]
    emails = emails[: a.limit or None]
    order_agent.reset_pipeline_versions()
    store.execute("DELETE FROM wf_audit WHERE actor = 'demo'")
    tmp = Path(tempfile.mkdtemp(prefix="case_demo_"))
    db = ROOT / "case_demo.sqlite"
    db.unlink(missing_ok=True)
    g = B.build(B.Deps(), B.sqlite_saver(db))
    tasks = ListTask()
    rows, t0 = [], time.time()
    with adapters.override(email=FolderEmail(sent_dir=tmp / "sent"), erp=MiniErpERP(tmp / "erp"), task=tasks):
        for e in emails:
            t, cid = time.time(), f"DEMO-{e['id']}"
            cfg = {"configurable": {"thread_id": cid}}
            try:
                s = g.invoke({"case_id": cid, "raw_b64": base64.b64encode((ROOT / e["file"]).read_bytes()).decode()}, cfg)
                if s.get("__interrupt__"):
                    s = g.invoke(Command(resume={"decision": "approve", "approver": "demo"}), cfg)
            except Exception as ex:
                rows.append({"id": e["id"], "scenario": e["scenario"], "error": f"{type(ex).__name__}: {ex}"[:300]})
                print(f"✗ {e['id']} {e['scenario']:14s} 出错 {ex}", flush=True)
                continue
            want = sorted((st["type"], st["refs"].get("attachment", "").split("/", 1)[-1] or None) for st in e["subtasks"])
            got = sorted((st["type"], st["refs"].get("attachment")) for st in s["subtasks"])
            reviews = s.get("reviews") or []
            row = {"id": e["id"], "scenario": e["scenario"], "split_ok": got == want, "want": want, "got": got, "status": s.get("status"),
                   "agents": {r["subtask_id"]: {"agent": r["agent"], "ok": r["ok"], "tools": [x.get("tool") for x in r.get("steps", [])], "note": r["note"][:200]}
                              for r in s.get("results", [])},
                   "review_rounds": len(s.get("drafts") or []) - 1 if s.get("drafts") else None,
                   "review_passed": reviews[-1]["passed"] if reviews else None,
                   "review_issues": [[i["rule"] for i in rv["issues"]] for rv in reviews],
                   "executed": [(x["id"], x["ok"]) for x in s.get("executed") or []], "seconds": round(time.time() - t, 1),
                   "final_draft": (s.get("drafts") or [{}])[-1].get("body")}
            rows.append(row)
            mark = "✓" if row["split_ok"] else "✗"
            print(f"{mark} {e['id']} {e['scenario']:14s} {'+'.join(x[0] for x in got):32s} {row['status']:8s} 审核 {row['review_rounds']} 轮 "
                  f"{'通过' if row['review_passed'] else ('未通过→人工' if row['review_passed'] is False else '-')} {row['seconds']}s", flush=True)
    ok = [r for r in rows if "error" not in r]
    summary = {"emails": len(rows), "errors": len(rows) - len(ok), "seconds": round(time.time() - t0, 1),
               "split_exact": f"{sum(r['split_ok'] for r in ok)}/{len(ok)}",
               "split_exact_compound": f"{sum(r['split_ok'] for r in ok if r['scenario'] == 'compound')}/{sum(r['scenario'] == 'compound' for r in ok)}",
               "status": dict(Counter(r["status"] for r in ok)),
               "review_first_pass": sum(1 for r in ok if r["review_issues"] and not r["review_issues"][0]),
               "review_rounds": dict(Counter(r["review_rounds"] for r in ok if r["review_rounds"] is not None)),
               "review_failed_to_human": sum(1 for r in ok if r["review_passed"] is False),
               "review_issue_rules": dict(Counter(x for r in ok for rv in r["review_issues"] for x in rv)),
               "tasks_created": len(tasks.created)}
    (ROOT / "case_graph_run.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
