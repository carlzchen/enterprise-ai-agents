"""
订单 Agent 演示：按时间顺序处理 data/workflow_mock/inbox 里带 PO 附件的邮件（真实大模型，英文版式走大模型抽取），逐个打印结果。

    PYTHONPATH=. .venv/bin/python scripts/workflow_order_demo.py [--limit N]

每次运行前清掉流程写入的 PO 版本（模拟数据预置的第一版保留），所以可以反复跑。
结果写到 data/workflow_mock/order_agent_run.json。末尾和标准答案做一次开发自查——这不是正式评测，正式指标以阶段 4 的评测报告为准。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.shared.runtime.logger import PROJECT_ROOT  # noqa: E402
from app.workflow.order_pipeline import agent  # noqa: E402

ROOT = PROJECT_ROOT / "data" / "workflow_mock"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    emails = json.loads((ROOT / "ground_truth" / "emails.json").read_text(encoding="utf-8"))
    pos = json.loads((ROOT / "ground_truth" / "pos.json").read_text(encoding="utf-8"))
    todo = [e for e in emails if e["attachments"]][: a.limit or None]
    agent.reset_pipeline_versions()
    rows, t0 = [], time.time()
    for e in todo:
        t = time.time()
        for r in agent.run_email((ROOT / e["file"]).read_bytes()):
            key = f"{e['id']}/{r.attachment}"
            gt = pos[key]
            po = r.extraction.po
            want_lines = [(ln["customer_part_no"], ln["qty"], ln["unit_price"], ln["delivery_date"]) for ln in gt["lines"]]
            got_lines = [(ln.customer_part_no, ln.qty, ln.unit_price, ln.delivery_date.isoformat() if ln.delivery_date else None) for ln in po.lines]
            want_flags = sorted({(f["rule"], next((x["customer_part_no"] for x in gt["lines"] if x["line_no"] == f["line_no"]), None)) for f in gt["flags"]})
            got_flags = sorted({(i.rule, next((x.customer_part_no for x in po.lines if x.line_no == i.line_no), None)) for i in r.report.issues})
            kind = "duplicate" if gt["duplicate_of"] else ("change" if gt["change"] else "new")
            row = {"key": key, "layout": gt["layout"], "method": r.extraction.method, "attempts": r.extraction.attempts, "kind": r.kind, "version": r.version,
                   "triage": r.triage, "issues": [i.rule for i in r.report.issues], "seconds": round(time.time() - t, 1),
                   "ok_header": (po.header.po_no, po.header.order_date.isoformat() if po.header.order_date else None) == (gt["po_no"], gt["order_date"]),
                   "ok_lines": got_lines == want_lines, "ok_kind": (r.kind, r.version) == (kind, gt["version"]), "ok_flags": got_flags == want_flags,
                   "ok_triage": (r.triage == "one_click") == (not gt["flags"]), "erp_draft": bool(r.erp_draft)}
            if not row["ok_lines"]:
                row["diff_lines"] = {"got": got_lines, "want": want_lines}
            if not row["ok_flags"]:
                row["diff_flags"] = {"got": got_flags, "want": want_flags}
            rows.append(row)
            mark = "✓" if all(row[k] for k in ("ok_header", "ok_lines", "ok_kind", "ok_flags", "ok_triage")) else "✗"
            print(f"{mark} {key:48s} {gt['layout']} {r.extraction.method:8s} {r.kind:9s} v{r.version} {r.triage:9s} {','.join(row['issues']) or '-'}", flush=True)
    summary = {"documents": len(rows), "seconds": round(time.time() - t0, 1)}
    for k in ("ok_header", "ok_lines", "ok_kind", "ok_flags", "ok_triage"):
        summary[k] = f"{sum(r[k] for r in rows)}/{len(rows)}"
    for m in ("template", "llm"):
        part = [r for r in rows if r["method"] == m]
        summary[f"{m}_lines_exact"] = f"{sum(r['ok_lines'] for r in part)}/{len(part)}"
    (ROOT / "order_agent_run.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
