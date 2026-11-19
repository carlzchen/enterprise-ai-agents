"""
订单 Agent：内部是固定流程，不让大模型决定步骤。大模型只出现在「没配模板的客户」的抽取这一步。

  识别客户（发件人邮箱 / 域名）→ 解析附件 → 抽取（模板或大模型）→ 查已收版本：新单 / 变更 / 重复
  → 规则校验 → 变更比对 → 置信度分流（可一键确认 / 需要人看）→ ERP 销售订单草稿 + 订单确认邮件草稿 → 记录这一版

只产出草稿，不执行任何写操作（写 ERP、发邮件要等人工审批，见 tools.py 的 W 工具）。记录 PO 版本是本系统内部的状态，用邮件 Message-ID 去重，
同一封邮件重跑不会被当成重复下单。
"""
import email
import email.policy
from collections.abc import Callable

from app.workflow import adapters, erp, mailbox, store
from app.workflow.adapters.models import EmailDraft, SalesOrderDraft, SalesOrderLine
from app.workflow.order_pipeline import diff as D, extract as X, tools as T
from app.workflow.order_pipeline.schemas import OrderAgentResult
from app.workflow.order_pipeline.validate import CheckContext, validate

PO_EXT = (".pdf", ".xlsx", ".xlsm")


def attachments_of(raw: bytes) -> list[tuple[str, bytes]]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return [(p.get_filename(), p.get_payload(decode=True) or b"") for p in msg.iter_attachments() if (p.get_filename() or "").lower().endswith(PO_EXT)]


def _context(customer_id: int, po, duplicate_of: str | None) -> CheckContext:
    rule = store.rule("order_check")
    prices, deals = T.get_price_reference(customer_id, po.header.order_date)
    ext = {r["product_code"]: (r["moq"], r["pack_qty"], r["std_lead_days"]) for r in store.query("SELECT product_code, moq, pack_qty, std_lead_days FROM wf_material_ext")}
    catalog = [{"code": p["code"], "name": p["name"], "series": p["series"], "voltage": p.get("voltage"), "power_w": p.get("power_w")} for p in erp.products()]
    return CheckContext(customer_id=customer_id, part_map=T.map_customer_part(customer_id), catalog=catalog, prices=prices, last_deals=deals, ext=ext,
                        price_tolerance=float(rule.get("price_tolerance", 0.03)), amount_tolerance=float(rule.get("amount_tolerance", 0.01)), duplicate_of=duplicate_of)


# 确认邮件里只告诉客户「哪一行的哪一项在核对」，不写参考价、阈值、起订量这些内部数字
CHECK_CN = {"extraction_incomplete": "内容", "unknown_part": "料号", "price_deviation": "单价", "no_price_reference": "单价", "below_moq": "数量（起订量）",
            "pack_multiple": "数量（整箱倍数）", "early_delivery": "交期", "amount_mismatch": "金额", "total_mismatch": "总额"}
CHECK_EN = {"extraction_incomplete": "details", "unknown_part": "part number", "price_deviation": "unit price", "no_price_reference": "unit price",
            "below_moq": "quantity (MOQ)", "pack_multiple": "quantity (carton multiple)", "early_delivery": "delivery date", "amount_mismatch": "line amount",
            "total_mismatch": "order total"}


def _confirm_email(lang: str, to: str, po, kind: str, change, issues, in_reply_to: str | None) -> EmailDraft:
    """订单确认邮件草稿：只用 PO 自己的内容（料号、数量、要求交期），不写价目表、成本这类内部数据；有问题的只说「正在核对」"""
    no = po.header.po_no or ""
    rows = "\n".join(f"  {ln.customer_part_no or ln.description}  ×{ln.qty:g}  交期 {ln.delivery_date}" if lang == "zh"
                     else f"  {ln.customer_part_no or ln.description}  x{ln.qty:g}  req. {ln.delivery_date}" for ln in po.lines if ln.qty is not None)
    checking = sorted({(f"第 {i.line_no} 行" if i.line_no else "订单") + CHECK_CN.get(i.rule, "内容") for i in issues if i.rule != "duplicate_po"})
    if lang == "zh":
        head = {"new": f"已收到贵司采购订单 {no}，明细如下：", "change": f"已收到贵司订单 {no} 的变更（第 {change.new_version if change else ''} 版），按新版本明细如下：",
                "duplicate": f"贵司订单 {no} 我们此前已经收到并在处理中，本次邮件不重复下单。"}[kind]
        tail = ("\n\n以下事项我们正在内部核对，确认后另行回复：\n" + "\n".join(f"  - {c}" for c in checking)) if checking and kind != "duplicate" else ""
        body = f"您好，\n\n{head}\n{rows if kind != 'duplicate' else ''}{tail}\n\n科朗销售部"
        subject = f"Re: 采购订单 {no} 确认"
    else:
        head = {"new": f"We have received your PO {no}:", "change": f"We have received revision {change.new_version if change else ''} of PO {no}. Updated lines:",
                "duplicate": f"PO {no} was already received and is being processed; this email will not create a duplicate order."}[kind]
        tail = ("\n\nWe are checking the following internally and will revert separately:\n" + "\n".join(sorted({f"  - {'item ' + str(i.line_no) if i.line_no else 'order'}: {CHECK_EN.get(i.rule, 'details')}"
                                                                                                             for i in issues if i.rule != "duplicate_po"}))) if checking and kind != "duplicate" else ""
        body = f"Hi,\n\n{head}\n{rows if kind != 'duplicate' else ''}{tail}\n\nBest regards,\nKelang Sales"
        subject = f"Re: PO {no} acknowledgement"
    return EmailDraft(to=[to], subject=subject, body=body, in_reply_to=in_reply_to)


def run(attachment: str, data: bytes, customer_id: int | None, *, lang: str = "zh", reply_to: str = "", message_id: str | None = None,
        llm_call: Callable[[str], str] | None = None, record: bool = True) -> OrderAgentResult:
    customer = adapters.erp().get_customer(customer_id) if customer_id else None
    doc = T.parse_document(attachment, data)
    template = (store.rule("po_templates").get("customers") or {}).get(str(customer_id)) if customer_id else None
    ex = X.extract(doc, template, llm_call)
    po = ex.po
    content = D.as_content(po)
    kind, version, change, duplicate_of = "new", 1, None, None
    history = T.find_existing_po(customer_id, po.header.po_no) if customer_id and po.header.po_no else []
    same_msg = next((h for h in history if message_id and h["message_id"] == message_id), None)
    earlier = [h for h in history if h is not same_msg and (same_msg is None or h["version"] < same_msg["version"])]
    if earlier:
        last = earlier[-1]
        if D.same_content(last["content"], content):
            kind, version, duplicate_of = "duplicate", last["version"], f"第 {last['version']} 版" + (f"，邮件 {last['message_id']}" if last["message_id"] else "，已录入 ERP")
        else:
            kind, version = "change", same_msg["version"] if same_msg else last["version"] + 1
            change = D.diff(last["content"], content, last["version"], version)
    elif same_msg:
        version = same_msg["version"]
    report = validate(po, _context(customer_id, po, duplicate_of), ex.missing) if customer_id else validate(po, _context(0, po, None), ex.missing + ["customer"])
    highlights = sorted({i.field for i in report.issues if i.field})
    triage = "one_click" if (customer_id and not ex.missing and report.passed and kind != "duplicate") else "review"
    draft = None
    if customer and kind != "duplicate" and po.lines and all(m.material for m in report.mappings) and not ex.missing:
        draft = SalesOrderDraft(customer_id=customer.id, customer_code=customer.code, customer_po=po.header.po_no, order_date=po.header.order_date,
                                currency=po.header.currency or customer.currency or "CNY", source_case=message_id or attachment,
                                lines=[SalesOrderLine(material=m.material, qty=ln.qty, unit_price=ln.unit_price, delivery_date=ln.delivery_date,
                                                      customer_part_no=ln.customer_part_no) for ln, m in zip(po.lines, report.mappings)])
    mail = _confirm_email(lang, reply_to, po, kind, change, report.issues, message_id) if reply_to else None
    if record and customer_id and po.header.po_no and kind != "duplicate" and not same_msg:
        T.insert_po_version(customer_id, po.header.po_no, version, content, message_id)
    return OrderAgentResult(attachment=attachment, customer_id=customer_id, customer_code=customer.code if customer else None, extraction=ex, report=report,
                            kind=kind, version=version, change=change, triage=triage, highlights=highlights, erp_draft=draft, confirm_email=mail)


def run_email(raw: bytes, llm_call: Callable[[str], str] | None = None, record: bool = True) -> list[OrderAgentResult]:
    """处理一封邮件里的全部 PO 附件"""
    m = mailbox.parse(raw)
    who = mailbox.identify(m["from_addr"])
    cid = who["id"] if who["type"] == "CUSTOMER" else None
    lang = "zh" if mailbox.language(m["body"] or m["subject"]) == "zh" else "en"
    return [run(name, data, cid, lang=lang, reply_to=m["from_addr"], message_id=m["message_id"], llm_call=llm_call, record=record)
            for name, data in attachments_of(raw)]


def reset_pipeline_versions() -> int:
    """评测前清掉流程写入的 PO 版本（模拟数据预置的第一版保留）"""
    return store.execute("DELETE FROM wf_po_version WHERE source = 'pipeline'")

