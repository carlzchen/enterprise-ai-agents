"""
订单和技术两个子任务的处理（交付 Agent 在 delivery.py）。

  订单：调阶段 2 的订单 Agent（内部固定流程），把结果整理成回复里的一段 + 证据 + 待审批的写操作。
        这里调用时不记 PO 版本（record=False），记版本也是审批后才做的写操作。
  技术：规格询价的技术 Agent 在阶段 6 接项目一的知识库；这一阶段先派给工程师，回复里只说「技术同事在确认」，不给任何型号和参数。
"""
import base64
import email
import email.policy
from collections.abc import Callable

from app.workflow.graph.state import AgentOutput, ProposedAction, SubTask
from app.workflow.order_pipeline import agent as order_agent, diff as D


def attachment_data(raw_b64: str, name: str) -> bytes | None:
    msg = email.message_from_bytes(base64.b64decode(raw_b64), policy=email.policy.default)
    return next((p.get_payload(decode=True) for p in msg.iter_attachments() if p.get_filename() == name), None)


def _section(body: str) -> str:
    """订单确认邮件草稿去掉称呼和落款，留中间的正文作为合并回复里的一段"""
    parts = body.split("\n\n")
    return "\n\n".join(parts[1:-1]).strip() if len(parts) > 2 else body


def run_order(st: SubTask, raw_b64: str, mail: dict, customer: dict | None, lang: str, llm_call: Callable[[str], str] | None) -> AgentOutput:
    name = st.refs.attachment
    data = attachment_data(raw_b64, name) if name else None
    if not data:
        return AgentOutput(subtask_id=st.id, type=st.type, agent="order", ok=False, note="邮件里没有这个子任务对应的 PO 附件，需要人工处理",
                           actions=[ProposedAction(id=f"{st.id}:task1", kind="create_task", summary=f"派给销售：PO 没有附件，请人工录入（{st.summary}）",
                                                   payload={"kind": "PO_ISSUE", "title": f"PO 没有附件：{st.summary}"[:120], "role": "SALES", "ref_type": "case",
                                                            "customer": (customer or {}).get("name")})])
    r = order_agent.run(name, data, customer["id"] if customer else None, lang=lang, reply_to=mail["from_addr"], message_id=mail["message_id"],
                        llm_call=llm_call, record=False)
    po = r.extraction.po
    lines = [{"customer_part_no": ln.customer_part_no, "qty": ln.qty, "unit_price": ln.unit_price, "delivery_date": str(ln.delivery_date) if ln.delivery_date else None}
             for ln in po.lines]
    facts = {"po_no": po.header.po_no, "kind": r.kind, "version": r.version, "triage": r.triage, "lines": lines,
             "issues": [{"rule": i.rule, "line_no": i.line_no} for i in r.report.issues]}     # 问题说明里有参考价、起订量、阈值，不进证据：草稿写出这些数字会被审核拦下
    evidence = [{"id": f"{st.id}.PO", "agent": "order", "tool": "order_pipeline", "kind": "R", "args": {"attachment": name},
                 "result": {**facts, "order_date": str(po.header.order_date) if po.header.order_date else None, "currency": po.header.currency,
                            "change": r.change.model_dump(mode="json") if r.change else None}}]
    actions: list[ProposedAction] = []
    if customer and po.header.po_no and r.kind != "duplicate":
        actions.append(ProposedAction(id=f"{st.id}:po_version", kind="record_po_version", summary=f"记下 PO {po.header.po_no} 第 {r.version} 版",
                                      payload={"customer_id": customer["id"], "po_no": po.header.po_no, "version": r.version, "content": D.as_content(po),
                                               "message_id": mail["message_id"]}))
    if r.erp_draft:
        n = len(r.report.issues)
        actions.append(ProposedAction(id=f"{st.id}:erp", kind="erp_save_sales_order",
                                      summary=f"在 ERP 保存销售订单草稿（PO {po.header.po_no}，{len(r.erp_draft.lines)} 行）" + (f"，有 {n} 项问题待核对" if n else ""),
                                      payload=r.erp_draft.model_dump(mode="json")))
    if r.report.issues and r.kind != "duplicate":
        actions.append(ProposedAction(id=f"{st.id}:task1", kind="create_task", summary=f"派给销售：核对 PO {po.header.po_no} 的 {len(r.report.issues)} 项问题",
                                      payload={"kind": "PO_ISSUE", "title": f"核对 PO {po.header.po_no}：" + "、".join(sorted({i.rule for i in r.report.issues})),
                                               "role": "SALES", "ref_type": "case", "customer": (customer or {}).get("name"),
                                               "detail": "\n".join(i.message for i in r.report.issues)[:1000]}))
    note = "；".join(i.message for i in r.report.issues)[:500]
    return AgentOutput(subtask_id=st.id, type=st.type, agent="order", ok=r.triage == "one_click", section=_section(r.confirm_email.body) if r.confirm_email else "",
                       facts=facts, evidence=evidence, actions=actions, note=note)


def run_tech(st: SubTask, customer: dict | None, lang: str) -> AgentOutput:
    """阶段 6 换成真正的技术 Agent（查知识库、推荐型号）；现在只派工程师、回复里不给型号和参数"""
    section = ("关于您询问的产品规格，我们的技术同事正在确认匹配的型号和规格资料，确认后会尽快回复您。" if lang == "zh"
               else "Regarding your enquiry on product specifications, our engineers are checking the matching models and datasheets and will revert shortly.")
    return AgentOutput(subtask_id=st.id, type=st.type, agent="tech", ok=False, section=section, facts={"product": st.refs.product, "params": st.refs.params},
                       note="技术 Agent 在阶段 6 实现，现在转工程师",
                       actions=[ProposedAction(id=f"{st.id}:task1", kind="create_task", summary=f"派给工程师：规格询价（{st.summary}）",
                                               payload={"kind": "SPEC_INQUIRY", "title": f"规格询价：{st.summary}"[:120], "role": "ENGINEER", "ref_type": "case",
                                                        "customer": (customer or {}).get("name"), "detail": st.refs.model_dump_json()})])
