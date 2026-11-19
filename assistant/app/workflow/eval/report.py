"""
把评测结果写成 Markdown 报告：每次运行一个目录 data/workflow_eval/reports/<时间戳>/

  summary.md   一页总览：每套评测的核心指标（各数据集 + 留出集合计）
  router.md / po.md / agents.md   全部指标 + 失败案例清单
  raw.json     原始结果（每封邮件、每个附件、每份审核样本），报告里的每个数字都能在这里追到

LATEST.md 指向最近一次运行。报告头写明：时间、模式（真实模型 / 假模型自检）、模型名、代码指纹、数据规模、命令。
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.shared.runtime.logger import PROJECT_ROOT
from app.workflow.eval import metrics as M

REPORTS = PROJECT_ROOT / "data" / "workflow_eval" / "reports"
CODE = PROJECT_ROOT / "app" / "workflow"


def code_fingerprint() -> str:
    """app/workflow 下全部 .py 的 sha256 前 12 位：报告和代码版本对应"""
    h = hashlib.sha256()
    for p in sorted(CODE.rglob("*.py")):
        h.update(str(p.relative_to(CODE)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _get(d: dict, path: str):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def cell(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, dict) and {"n", "d", "rate"} <= set(v):
        return M.fmt(v)
    if isinstance(v, dict) and {"precision", "recall", "f1"} <= set(v):
        f = lambda x: "—" if x is None else f"{x * 100:.1f}%"
        return f"P {f(v['precision'])} / R {f(v['recall'])} / F1 {f(v['f1'])}"
    if isinstance(v, dict):
        return "、".join(f"{k}: {x}" for k, x in v.items()) or "—"
    return str(v)


def table(results: dict[str, dict], rows: list[tuple[str, str]], held: list[str]) -> str:
    """results: 数据集名 → summary；rows: [(指标中文名, summary 里的路径)]"""
    names = list(results)
    merged = M.merge([results[n] for n in held]) if len(held) > 1 else None
    head = ["指标"] + names + (["留出集合计"] if merged else [])
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for label, path in rows:
        vals = [cell(_get(results[n], path)) for n in names]
        if merged:
            mv = _get(merged, path)
            vals.append(cell(mv) if isinstance(mv, dict) or (isinstance(mv, int) and not isinstance(mv, bool)) else "")
        out.append(f"| {label} | " + " | ".join(vals) + " |")
    return "\n".join(out)


def header(title: str, meta: dict) -> str:
    sets = "；".join(f"{n}（{'留出集' if n in meta['held_out'] else '开发集'}：{s['emails']} 封邮件，{s['po_documents']} 个 PO 附件，邮件种子 {s['seed']}）"
                    for n, s in meta["datasets"].items())
    mode = "**假模型自检**：模型换成按标准答案作答的假模型，数字只说明评测代码能跑通，**不是效果指标**" if meta["fake"] else \
        f"真实模型：主模型 `{meta['model']}`" + (f"，备用 `{meta['fallback_model']}`" if meta["fallback_model"] else "")
    return (f"# {title}\n\n- 时间：{meta['time']}\n- 模式：{mode}\n- 代码指纹：`{meta['code']}`（app/workflow 全部源码的 sha256 前 12 位）\n"
            f"- 数据：{sets}\n- 命令：`{meta['command']}`\n- 耗时：{meta.get('seconds', '—')} 秒\n")


def _list(items: list, fmt, limit: int = 60) -> str:
    if not items:
        return "无。\n"
    lines = [fmt(x) for x in items[:limit]]
    more = f"\n……另有 {len(items) - limit} 条，见 raw.json\n" if len(items) > limit else "\n"
    return "\n".join(lines) + more


ROUTER_ROWS = [("邮件数", "emails"), ("子任务数（标准答案）", "subtasks"), ("客户识别准确率", "customer_id"), ("意图集合完全一致", "intent_exact"),
               ("拆分完全一致", "split_exact"), ("其中：复合邮件拆分完全一致", "split_exact_compound"), ("子任务（按类型）", "subtask_micro"),
               ("  新订单 po_entry", "per_type.po_entry"), ("  订单变更 po_change", "per_type.po_change"), ("  交期询问 delivery_query", "per_type.delivery_query"),
               ("  加急 expedite", "per_type.expedite"), ("  规格询价 spec_inquiry", "per_type.spec_inquiry"), ("干扰邮件识别", "spam"),
               ("交期 / 加急引用的订单正确", "order_ref"), ("加急要求日期正确", "expedite_date"), ("格式不合格重试的邮件数", "retried"), ("调用出错", "errors"), ("耗时（秒）", "seconds")]

PO_ROWS = [("PO 附件数", "documents"), ("明细行数", "lines"), ("PO 号", "header.po_no"), ("下单日期", "header.order_date"), ("币种", "header.currency"),
           ("PO 总额", "header.total_amount"), ("行数一致", "line_count"), ("行级完全正确（料号+数量+单价+交期）", "line_exact"),
           ("  客户料号", "line_field.customer_part_no"), ("  数量", "line_field.qty"), ("  单价", "line_field.unit_price"), ("  交期", "line_field.delivery_date"),
           ("整单完全正确", "doc_exact"), ("  模板抽取（中文版式）", "by_method.template.doc_exact"), ("  大模型抽取（英文版式）", "by_method.llm.doc_exact"),
           ("料号映射命中率（已登记）", "mapping_hit"), ("未登记料号：候选第 1 正确", "unknown_top1"), ("未登记料号：候选前 3 含正确", "unknown_top3"),
           ("新单 / 变更 / 重复 + 版本", "kind"), ("变更逐项（行、字段、新值）", "change_items"), ("变更单完全正确", "change_exact"),
           ("规则标记（规则、行）", "flags_micro"), ("规则标记整单一致", "flags_doc_exact"), ("分流（一键确认 / 人工看）", "triage"),
           ("ERP 草稿 = 标准销售订单", "erp_draft"), ("不该生成却生成了 ERP 草稿", "unexpected_draft"), ("大模型抽取重试次数", "llm_retried"),
           ("出错", "errors"), ("耗时（秒）", "seconds")]

AGENT_ROWS = [("邮件数", "emails"), ("端到端成功率（按邮件）", "case_success"), ("  新 PO", "case_success_by_scenario.new_po"),
              ("  PO 变更", "case_success_by_scenario.po_change"), ("  重复发送", "case_success_by_scenario.duplicate"),
              ("  交期询问", "case_success_by_scenario.delivery_query"), ("  加急", "case_success_by_scenario.expedite"),
              ("  规格询价", "case_success_by_scenario.spec_inquiry"), ("  复合邮件", "case_success_by_scenario.compound"), ("  干扰邮件", "case_success_by_scenario.spam"),
              ("子任务成功率", "subtask_success"), ("交付 Agent 工具调用准确率", "tool_accuracy"), ("  交期询问", "tool_accuracy_by_type.delivery_query"),
              ("  加急", "tool_accuracy_by_type.expedite"), ("单次工具调用出错率", "tool_call_errors"), ("每次平均工具调用数", "tool_calls_avg"),
              ("答卷格式不合格重交次数", "submit_retries"), ("草稿初稿通过审核", "review_first_pass"), ("最终通过审核（其余转人工）", "review_final_pass"),
              ("重写轮次分布", "review_rounds"), ("审核拦下的问题（按规则）", "review_issue_rules"), ("单封耗时 P50（秒）", "latency_p50"),
              ("单封耗时 P90（秒）", "latency_p90"), ("出错", "errors"), ("耗时（秒）", "seconds")]

REVIEW_ROWS = [("来源案例数", "cases"), ("样本数", "samples"), ("拦截率（违规草稿被拦下）", "intercept"), ("拦对规则", "right_rule"),
               ("误拦截率（正常草稿被拦下）", "false_intercept"), ("  代码构造的违规：拦截率", "by_source.code.intercept"),
               ("  大模型写入的违规：拦截率", "by_source.llm.intercept"), ("  大模型写入的违规：拦对规则", "by_source.llm.right_rule"),
               ("  模板正常草稿：误拦截率", "false_by_source.template"), ("  大模型改写的正常草稿：误拦截率", "false_by_source.paraphrase"),
               ("大模型样本作废数", "dropped_llm"), ("耗时（秒）", "seconds")]


def write(meta: dict, results: dict) -> Path:
    """results: {suite: {dataset: {...}}}；返回报告目录"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + ("-fake" if meta["fake"] else "")
    out = REPORTS / stamp
    out.mkdir(parents=True, exist_ok=True)
    held = [n for n in meta["datasets"] if n in meta["held_out"]]
    parts = []
    if "router" in results:
        r = results["router"]
        s = {n: x["summary"] for n, x in r.items()}
        body = header("路由评测（Supervisor）", meta) + "\n## 指标\n\n" + table(s, ROUTER_ROWS, held) + "\n\n## 失败案例\n"
        for n, x in r.items():
            body += f"\n### {n}\n\n" + _list(x["failures"], lambda f: f"- {f['id']}（{f['scenario']}）" + (f" 出错：{f['error']}" if f.get("error") else
                                                                                                            f" 得到 {f['got']}，应为 {f['want']}" + ("" if f["customer_ok"] else "；客户认错") +
                                                                                                            (f"；引用不对 {f['refs']}" if f["refs"] else "")))
        (out / "router.md").write_text(body, encoding="utf-8")
        parts.append(("路由（Supervisor）", "router.md", s, [("拆分完全一致", "split_exact"), ("复合邮件拆分完全一致", "split_exact_compound"),
                                                         ("意图集合完全一致", "intent_exact"), ("子任务 P / R / F1", "subtask_micro"), ("单号引用正确", "order_ref")]))
    if "po" in results:
        r = results["po"]
        s = {n: x["summary"] for n, x in r.items()}
        body = header("PO 评测（订单 Agent）", meta) + "\n## 指标\n\n" + table(s, PO_ROWS, held)
        body += "\n\n## 规则标记（按规则）\n\n" + table(s, [(k, f"flags_per_rule.{k}") for k in sorted({k for x in s.values() for k in x["flags_per_rule"]})], held)
        body += "\n\n## 按版式\n\n" + table(s, [(f"版式 {k} 整单完全正确", f"by_layout.{k}.doc_exact") for k in sorted({k for x in s.values() for k in x["by_layout"]})], held)
        body += "\n\n## 失败案例\n"
        for n, x in r.items():
            body += f"\n### {n}\n\n" + _list(x["failures"], lambda f: f"- `{f['key']}`" + (f" 出错：{f['error']}" if f.get("error") else f"（版式 {f['layout']}，{f['method']}）：" + "；".join(f["why"])))
        (out / "po.md").write_text(body, encoding="utf-8")
        parts.append(("PO（订单 Agent）", "po.md", s, [("行级完全正确", "line_exact"), ("整单完全正确", "doc_exact"), ("料号映射命中率", "mapping_hit"),
                                                     ("新单 / 变更 / 重复识别", "kind"), ("变更逐项", "change_items"), ("规则标记", "flags_micro"),
                                                     ("ERP 草稿 = 标准销售订单", "erp_draft")]))
    if "agents" in results:
        r = results["agents"]
        s = {n: x["summary"] for n, x in r.items()}
        rv = {n: x["reviewer"]["summary"] for n, x in r.items() if "reviewer" in x}
        body = header("Agent 评测（整张图 + 审核 Agent）", meta) + "\n## 端到端和工具调用\n\n" + table(s, AGENT_ROWS, held)
        if rv:
            body += "\n\n## 审核 Agent：拦截率和误拦截率\n\n" + table(rv, REVIEW_ROWS, [n for n in held if n in rv])
            cats = sorted({k for x in rv.values() for k in x["by_category"]})
            body += "\n\n### 按违规类别的拦截率\n\n" + table(rv, [(k, f"by_category.{k}.intercept") for k in cats], [n for n in held if n in rv])
            body += ("\n\n说明：「代码构造」的违规句子是我写的，和审核规则同一个作者，结果偏乐观；「大模型写入」是让模型把违规内容自然地改写进草稿（不告诉它审核规则），"
                     "更接近真实情况。正常草稿里「模板」是各子 Agent 段落直接拼接，「大模型改写」是让模型换个说法重写（要求不增删信息，模型偶尔会违反，误拦清单里可以逐条看）。\n")
        body += "\n\n## 端到端失败案例\n"
        for n, x in r.items():
            body += f"\n### {n}\n\n" + _list(x["failures"], lambda f: f"- {f['id']}（{f['scenario']}）：" + "；".join(f["why"]))
        body += "\n\n## 工具调用不完整的交付 Agent 运行\n"
        for n, x in r.items():
            body += f"\n### {n}\n\n" + _list(x["tool_failures"], lambda f: f"- {f['id']}（{f['type']}）缺 {f['missing']}，实际调用 {f['steps']}")
        if rv:
            body += "\n\n## 审核漏拦的违规草稿\n"
            for n, x in r.items():
                body += f"\n### {n}\n\n" + _list(x["reviewer"]["misses"], lambda f: f"- {f['case']} [{f['source']}/{f['category']}] 加入：{f['inserted']}；审核结果 {f['rules'] or '通过'}"
                                                 + (f"\n  > {f['body'][-200:].replace(chr(10), ' ')}" if f["source"] == "llm" else ""))
            body += "\n\n## 审核误拦的正常草稿\n"
            for n, x in r.items():
                body += f"\n### {n}\n\n" + _list(x["reviewer"]["false_alarms"], lambda f: f"- {f['case']} [{f['source']}] " + "；".join(f["issues"])
                                                 + f"\n  > {f['body'][:200].replace(chr(10), ' ')}")
        (out / "agents.md").write_text(body, encoding="utf-8")
        parts.append(("Agent（整张图）", "agents.md", s, [("端到端成功率", "case_success"), ("子任务成功率", "subtask_success"),
                                                       ("交付 Agent 工具调用准确率", "tool_accuracy"), ("初稿通过审核", "review_first_pass")]))
        if rv:
            parts.append(("审核 Agent", "agents.md", rv, [("拦截率（全部）", "intercept"), ("拦截率（大模型写入的违规）", "by_source.llm.intercept"),
                                                       ("误拦截率（全部）", "false_intercept"), ("误拦截率（大模型改写的正常稿）", "false_by_source.paraphrase")]))
    summary = header("跟单多 Agent 评测总览", meta)
    summary += "\n正式指标看「留出集合计」：开发集是阶段 1–3 调规则和提示词时一直在用的数据，留出集开发时没看过。\n"
    for title, f, s, rows in parts:
        summary += f"\n## {title}（详见 [{f}]({f})）\n\n" + table(s, rows, [n for n in held if n in s]) + "\n"
    (out / "summary.md").write_text(summary, encoding="utf-8")
    raw = {"meta": meta, "results": results}
    (out / "raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (REPORTS / "LATEST.md").write_text(f"最近一次评测：[{stamp}/summary.md]({stamp}/summary.md)（{meta['time']}，{'假模型自检' if meta['fake'] else '真实模型'}）\n",
                                       encoding="utf-8")
    return out
