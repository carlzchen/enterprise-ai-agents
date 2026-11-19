"""
适配器的输入输出。业务代码和 Agent 只和这些结构打交道，不管背后是 Mock 还是真实系统。
金额一律 Decimal 的字符串形式不好用，这里用 float 保留两位（和 MiniERP 现有规则层一致）；日期用 date。
"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- 邮件

class RawEmail(BaseModel):
    """从邮箱取到的一封原始邮件（还没解析）"""
    source: str                                        # 来源标识：本地文件路径 / imap:序号 / graph:邮件 ID，用于排查
    mailbox: str = ""                                  # 从哪个邮箱取的（订单专用邮箱之一）
    raw: bytes                                         # 完整 MIME 原文；解析、Message-ID 幂等都基于它


class EmailDraft(BaseModel):
    """要发出去的一封邮件。只有经过人工确认的草稿才会交给 send()"""
    to: list[str]
    cc: list[str] = []
    subject: str
    body: str
    in_reply_to: str | None = None                     # 回复哪封邮件（Message-ID），保证客户那边能串成一个会话
    from_mailbox: str | None = None                    # 用哪个邮箱发；空 = 默认订单邮箱
    attachments: list[str] = []                        # 附件的本地路径
    ref: str = ""                                      # 业务引用（外发动作 ID），用来对账和幂等


class SentReceipt(BaseModel):
    message_id: str
    location: str                                      # Mock：存下来的 eml 路径；Real：邮件系统里的 ID
    sent_at: datetime


# ---------------------------------------------------------------- ERP

class Customer(BaseModel):
    id: int
    code: str
    name: str
    currency: str | None = None
    country: str | None = None
    credit_limit: float | None = None


class Material(BaseModel):
    code: str
    name: str
    series: str | None = None
    revision: str | None = None
    status: str = "ACTIVE"
    params: dict[str, float] = {}


class PriceReference(BaseModel):
    """价格依据：报价单 / 价目表 / 最近成交价，按优先级给"""
    material: str
    customer_id: int
    source: Literal["quote", "price_list", "last_deal", "none"]
    unit_price: float | None = None
    ref_no: str | None = None                          # 报价单号或订单号
    valid_until: date | None = None


class OrderStatus(BaseModel):
    order_no: str
    customer_id: int | None = None
    status: str
    lines: list[dict] = []                             # 每行：型号、数量、已发数量、计划交期


class SalesOrderLine(BaseModel):
    material: str                                      # 内部物料号（客户料号已经映射过）
    qty: float = Field(gt=0)
    unit_price: float = Field(ge=0)                    # 含税单价
    delivery_date: date
    customer_part_no: str | None = None
    note: str = ""


class SalesOrderDraft(BaseModel):
    """销售订单草稿。真实环境对应金蝶云·星空的销售订单（SAL_SaleOrder），只调 Save 不调 Submit / Audit"""
    customer_id: int
    customer_code: str
    customer_po: str                                   # 客户 PO 号
    order_date: date
    currency: str = "CNY"
    lines: list[SalesOrderLine]
    source_case: str = ""                              # 来自哪个处理单（邮件 case），审计用

    def to_kingdee_model(self, bill_type: str = "XSDD01_SYS", sale_org: str = "100") -> dict:
        """星空 WebAPI Save 接口的 Model 结构（字段名按星空标准销售订单；上线前要和 IT 核对单据类型、组织、币别编码）"""
        return {
            "FBillTypeID": {"FNUMBER": bill_type}, "FDate": self.order_date.isoformat(), "FSaleOrgId": {"FNumber": sale_org},
            "FCustId": {"FNumber": self.customer_code}, "F_CustomerPO": self.customer_po,
            "FSaleOrderFinance": {"FSettleCurrId": {"FNumber": self.currency}},
            "FSaleOrderEntry": [{"FMaterialId": {"FNumber": ln.material}, "FQty": ln.qty, "FTaxPrice": ln.unit_price,
                                 "FDeliveryDate": ln.delivery_date.isoformat(), "FEntryNote": ln.note} for ln in self.lines],
        }


class ERPSaveResult(BaseModel):
    ok: bool
    bill_no: str | None = None                         # ERP 生成的单号（Mock：草稿编号）
    message: str = ""


# ---------------------------------------------------------------- 任务

class TaskRequest(BaseModel):
    kind: str                                          # 待办类型，如 EXPEDITE（加急评估）、PO_ISSUE（订单差异）
    title: str
    role: str                                          # 派给哪个角色：SALES / PRODUCTION / FINANCE / ENGINEER …
    assignee: str | None = None
    ref_type: str = ""
    ref_id: str = ""
    customer: str | None = None
    payload: dict = {}


# ---------------------------------------------------------------- 知识库

class KBSource(BaseModel):
    title: str
    section: str | None = None
    snippet: str = ""


class KBAnswer(BaseModel):
    answer: str
    sources: list[KBSource] = []
    status: str = "OK"                                 # OK / EMPTY（没查到）/ DEGRADED（降级）
