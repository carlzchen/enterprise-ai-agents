"""
Mock 实现：不依赖任何外部系统，跑通全流程。全部是对 MiniERP 现有代码的包装，行为和改造前一致。

  FolderEmail     收信：读 WORKFLOW_MAIL_DIR 里的 eml；发信：存成 eml 到 output/workflow/sent/
  MiniErpERP      MiniERP 自己的库当作「模拟的金蝶星空」：查询走 erp.py 的只读查询；
                  保存销售订单草稿 → 写成 JSON 文件到 output/workflow/erp_drafts/（不碰 MiniERP 的销售订单表）
  WorkbenchTask   派到跟单工作台的待办中心（flows.create_task）
  MasterDataKB    在产品主数据里按关键词检索（项目一知识库的替身）
"""
import json
import re
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from app.shared.runtime.logger import PROJECT_ROOT
from app.workflow import erp, rules, store
from app.workflow.adapters.models import (Customer, EmailDraft, ERPSaveResult, KBAnswer, KBSource, Material, OrderStatus, PriceReference,
                                          RawEmail, SalesOrderDraft, SentReceipt, TaskRequest)

SENT_DIR = PROJECT_ROOT / "output" / "workflow" / "sent"
DRAFT_DIR = PROJECT_ROOT / "output" / "workflow" / "erp_drafts"
SENDER = "科朗销售部 <sales@kelang.example.com>"


class FolderEmail:
    name = "mock"

    def __init__(self, inbox: str | Path | None = None, sent_dir: Path | None = None, mailbox: str = ""):
        from app.infra.config.settings import settings
        p = Path(inbox or settings.workflow_mail_dir)
        self.inbox = p if p.is_absolute() else PROJECT_ROOT / p
        self.sent_dir = sent_dir or SENT_DIR
        self.mailbox = mailbox or (settings.workflow_order_mailboxes.split(",")[0].strip() if settings.workflow_order_mailboxes else "")

    def fetch_new(self) -> list[RawEmail]:
        if not self.inbox.exists():
            return []
        return [RawEmail(source=str(f), mailbox=self.mailbox, raw=f.read_bytes()) for f in sorted(self.inbox.glob("*.eml"))]

    def send(self, draft: EmailDraft) -> SentReceipt:
        msg = EmailMessage()
        msg["From"] = draft.from_mailbox or SENDER
        msg["To"] = ", ".join(draft.to)
        if draft.cc:
            msg["Cc"] = ", ".join(draft.cc)
        msg["Subject"] = draft.subject
        now = datetime.now().astimezone()
        msg["Date"], msg["Message-ID"] = format_datetime(now), make_msgid(domain="kelang.example.com")
        if draft.in_reply_to:
            msg["In-Reply-To"] = draft.in_reply_to
        msg.set_content(draft.body)
        for path in draft.attachments:
            p = Path(path)
            msg.add_attachment(p.read_bytes(), maintype="application", subtype="octet-stream", filename=p.name)
        self.sent_dir.mkdir(parents=True, exist_ok=True)
        dest = self.sent_dir / f"{re.sub(r'[^A-Za-z0-9_-]', '_', draft.ref) or msg['Message-ID'].strip('<>')}.eml"
        dest.write_bytes(bytes(msg))
        return SentReceipt(message_id=msg["Message-ID"], location=str(dest), sent_at=now.replace(tzinfo=None))


class MiniErpERP:
    name = "mock"

    def __init__(self, draft_dir: Path | None = None):
        self.draft_dir = draft_dir or DRAFT_DIR

    @staticmethod
    def _customer(row: dict | None) -> Customer | None:
        if not row:
            return None
        return Customer(id=row["id"], code=row["code"], name=row["name"], currency=row.get("currency"),
                        country=None if row.get("is_domestic") is None else ("中国" if row["is_domestic"] else row.get("region")),
                        credit_limit=float(row["credit_limit"]) if row.get("credit_limit") is not None else None)

    def find_customer_by_email(self, addr: str) -> Customer | None:
        c = erp.contact_by_email(addr)
        return self.get_customer(c["customer_id"]) if c else None

    def get_customer(self, customer_id: int) -> Customer | None:
        return self._customer(erp.customer(customer_id))

    def get_material(self, code: str) -> Material | None:
        p = erp.product(code)
        if not p:
            return None
        return Material(code=p["code"], name=p["name"], series=p.get("series"), revision=p.get("revision"), status=p.get("status") or "ACTIVE",
                        params=erp.product_params(code))

    def list_materials(self) -> list[Material]:
        return [Material(code=p["code"], name=p["name"], series=p.get("series"), revision=p.get("revision"), status=p.get("status") or "ACTIVE")
                for p in erp.products()]

    def get_price_reference(self, customer_id: int, material: str, qty: float | None = None) -> PriceReference:
        """优先级：该客户该型号最近一次已发出的报价（按数量取阶梯价）→ 最近成交价 → 没有"""
        q = store.one("SELECT quote_no, tiers, valid_until FROM wf_quote WHERE status = 'SENT' AND customer_id = %s AND model = %s ORDER BY sent_at DESC LIMIT 1",
                      (customer_id, material))
        if q:
            tiers = store.loads(q["tiers"])
            price = rules.tier_price(tiers, qty if qty is not None else min(t["qty"] for t in tiers))
            return PriceReference(material=material, customer_id=customer_id, source="quote", unit_price=price, ref_no=q["quote_no"],
                                  valid_until=q["valid_until"])
        d = erp.last_deal(customer_id, material)
        if d:
            return PriceReference(material=material, customer_id=customer_id, source="last_deal", unit_price=d["unit_price"], ref_no=d["order_no"])
        return PriceReference(material=material, customer_id=customer_id, source="none")

    def get_inventory(self, material: str) -> float:
        return erp.stock(material)

    def get_order_status(self, order_no: str) -> OrderStatus | None:
        o = erp.order_status(order_no)
        if not o:
            return None
        return OrderStatus(order_no=o["order_no"], customer_id=o["customer_id"], status=o["status"],
                           lines=[{**ln, "required_date": o["required_date"]} for ln in o["lines"]])

    def save_sales_order(self, draft: SalesOrderDraft) -> ERPSaveResult:
        """模拟星空的 Save：只落一份草稿文件（内容就是将来调 WebAPI 的 Model），同一个客户 + PO 号重复保存会覆盖同一个文件（幂等）"""
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        bill_no = f"MOCK-{draft.customer_code}-{re.sub(r'[^A-Za-z0-9_-]', '_', draft.customer_po)}"
        body = {"formid": "SAL_SaleOrder", "data": {"Model": draft.to_kingdee_model()}, "source_case": draft.source_case,
                "saved_at": datetime.now().isoformat(timespec="seconds")}
        (self.draft_dir / f"{bill_no}.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        return ERPSaveResult(ok=True, bill_no=bill_no, message="已保存为草稿（模拟星空，未提交、未审核）")


class WorkbenchTask:
    name = "mock"

    def create_task(self, req: TaskRequest) -> str:
        from app.workflow import flows
        return flows.create_task(req.kind, req.title, req.role, ref_type=req.ref_type, ref_id=req.ref_id, customer=req.customer,
                                 payload=req.payload, assignee=req.assignee)


class MasterDataKB:
    name = "mock"

    def search_product_kb(self, query: str, role: str = "SALES") -> KBAnswer:
        words = [w for w in re.split(r"[\s,，、]+", query.upper()) if len(w) >= 2]
        hits = [p for p in erp.products() if any(w in f"{p['code']} {p['name']} {p.get('series') or ''}".upper() for w in words)][:5]
        if not hits:
            return KBAnswer(answer="", status="EMPTY")
        lines = [f"{p['code']}：{p['name']}，电压 {p.get('voltage') or '-'}，功率 {p.get('power_w') or '-'} W，额定转速 {p.get('rated_speed_rpm') or '-'} rpm" for p in hits]
        return KBAnswer(answer="\n".join(lines), sources=[KBSource(title=f"产品主数据 {p['code']}", snippet=ln) for p, ln in zip(hits, lines)])
