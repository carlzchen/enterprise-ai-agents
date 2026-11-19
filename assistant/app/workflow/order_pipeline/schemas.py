"""
订单 Agent 各步之间传递的结构。抽取结果、校验结果、变更报告、最终输出都是 Pydantic，不传自由文本。
"""
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.workflow.adapters.models import EmailDraft, SalesOrderDraft


# ---------------------------------------------------------------- 抽取

class POHeader(BaseModel):
    po_no: str | None = None
    order_date: date | None = None
    currency: str | None = None
    payment_terms: str | None = None
    buyer: str | None = None
    ship_to: str | None = None
    revision: int | None = None                        # PO 上写的版本号（有的客户写，有的不写）
    total_amount: float | None = None                  # PO 上印的总额，用来发现抽取错误或客户自己算错


class POLine(BaseModel):
    line_no: int
    customer_part_no: str | None = None
    description: str | None = None
    qty: float | None = None
    unit_price: float | None = None
    amount: float | None = None                        # PO 上印的行金额
    delivery_date: date | None = None


class POExtraction(BaseModel):
    header: POHeader
    lines: list[POLine] = Field(default_factory=list)


class ExtractResult(BaseModel):
    po: POExtraction
    method: Literal["template", "llm"]
    template: str | None = None
    missing: list[str] = []                            # 必填字段缺了哪些（po_no、order_date、lines[i].qty …）
    attempts: int = 1
    error: str | None = None


# ---------------------------------------------------------------- 料号映射

class Candidate(BaseModel):
    material: str
    name: str
    score: float                                       # 0~1，越大越像
    why: str = ""


class LineMapping(BaseModel):
    line_no: int
    customer_part_no: str | None
    material: str | None                               # 映射到的内部物料号；没找到为空
    how: Literal["map", "learned", "none"]             # map 映射表、learned 人工确认后学到的、none 没找到
    candidates: list[Candidate] = []


# ---------------------------------------------------------------- 校验

RULES = Literal["extraction_incomplete", "unknown_part", "price_deviation", "no_price_reference", "below_moq", "pack_multiple",
                "early_delivery", "amount_mismatch", "total_mismatch", "duplicate_po"]


class Issue(BaseModel):
    rule: RULES
    line_no: int | None = None
    field: str | None = None                           # 审批页面高亮哪个字段
    message: str
    expected: str | None = None
    actual: str | None = None


class ValidationReport(BaseModel):
    issues: list[Issue] = []
    mappings: list[LineMapping] = []

    @property
    def passed(self) -> bool:
        return not self.issues

    def rules(self) -> set[str]:
        return {i.rule for i in self.issues}


# ---------------------------------------------------------------- 变更比对

class FieldChange(BaseModel):
    line_no: int
    customer_part_no: str | None
    field: Literal["qty", "unit_price", "delivery_date"]
    old: str
    new: str


class POChangeReport(BaseModel):
    po_no: str
    base_version: int
    new_version: int
    changes: list[FieldChange] = []
    added_lines: list[int] = []                        # 新版的行号
    cancelled_lines: list[int] = []                    # 旧版的行号

    @property
    def empty(self) -> bool:
        return not (self.changes or self.added_lines or self.cancelled_lines)


# ---------------------------------------------------------------- 订单 Agent 输出

class OrderAgentResult(BaseModel):
    attachment: str
    customer_id: int | None
    customer_code: str | None
    extraction: ExtractResult
    report: ValidationReport
    kind: Literal["new", "change", "duplicate"]
    version: int
    change: POChangeReport | None = None
    triage: Literal["one_click", "review"]             # 一键确认 / 需要人看（高亮异常字段）
    highlights: list[str] = []                         # 需要人看的字段，如 lines[2].unit_price
    erp_draft: SalesOrderDraft | None = None           # 星空销售订单草稿（待审批后才保存）
    confirm_email: EmailDraft | None = None            # 订单确认邮件草稿（待审批后才发）
