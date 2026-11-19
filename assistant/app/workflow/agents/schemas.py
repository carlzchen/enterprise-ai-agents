"""
Agent 之间传递的数据结构。Agent 不自由聊天，只交这些固定结构的「答卷」，下一个 Agent 读的是结构化结果。
注意：答卷里没有任何「金额、天数」字段让模型自己填——这些数字都由工具算出来，系统从证据账本里取。
"""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CHANGE_TYPES = Literal["SHAFT", "WINDING", "CONNECTOR", "HOUSING", "MAGNET", "BEARING", "ENCODER", "PACKAGING", "OTHER"]
CHANGE_CN = {"SHAFT": "转轴（轴径、轴伸、键槽）", "WINDING": "绕组（电压、转速、功率调整）", "CONNECTOR": "出线方式或接插件", "HOUSING": "机壳或安装尺寸",
             "MAGNET": "磁钢", "BEARING": "轴承", "ENCODER": "编码器", "PACKAGING": "包装", "OTHER": "其他"}


class Change(BaseModel):
    """技术方案里的一项改动"""
    type: CHANGE_TYPES
    description: str                                   # 改什么，例如「轴伸由 30mm 加长到 40mm」
    material_from: str | None = None                   # 被替换的物料编码（基础型号 BOM 里的）；留空且 new_part=true 表示「新增一个零件」
    material_to: str | None = None                     # 换成哪个已有物料；需要新开料时留空
    new_part: bool = False                             # 是否需要新开一个物料（没有现成的）
    qty_factor: float | None = Field(default=None, gt=0, le=5)     # 用量变化倍数，例如绕组铜线用量 ×1.15
    evidence: list[str] = []                           # 依据：证据账本里的编号，例如 ["E3", "E5"]


class TechPlan(BaseModel):
    base_model: str | None                             # 选哪个现有型号做基础；完全找不到时为空（需要新开发）
    feasibility: Literal["可行", "有条件可行", "需要新开发", "不可行"]
    risk: Literal["低", "中", "高"]
    changes: list[Change] = []
    reasoning: str                                     # 为什么这么改
    open_questions: list[str] = []                     # 需要向客户确认的问题


class CostResult(BaseModel):
    recost_evidence: str                               # 用哪一次「按改动清单重算成本」的结果（证据编号）
    nre_evidence: str | None = None                    # 一次性费用的证据编号
    notes: str = ""                                    # 估算里不确定的地方


class LeadResult(BaseModel):
    schedule_evidence: str                             # 交期估算的证据编号
    load_evidence: str | None = None                   # 产线负荷的证据编号
    risks: list[str] = []


class CommercialResult(BaseModel):
    price_evidence: str                                # 定价工具结果的证据编号
    nre_policy: Literal["单独收取", "分摊进单价", "免收"]
    amortize_evidence: str | None = None               # 选「分摊进单价」时，分摊计算的证据编号
    recommendation: str                                # 给销售的建议


def evidence_ids(v: list[str]) -> list[str]:
    """模型常把证据写成「E8/选型手册/1 型号与参数」：只取开头的编号，取不到编号的丢掉（审查会因此报「没有有效证据」）"""
    out = [m.group(0) for e in v or [] if (m := re.match(r"\s*E\d+", str(e)))]
    return list(dict.fromkeys(e.strip() for e in out))


class Finding(BaseModel):
    """预研查到的一条依据"""
    point: str                                         # 查到了什么，例如「选型手册里 130 法兰最大做到 1.5kW」
    evidence: list[str] = Field(description="证据编号列表，只写编号，如 [\"E3\", \"E7\"]")

    @field_validator("evidence", mode="before")
    @classmethod
    def _ids(cls, v):
        return evidence_ids(v)


class DevTask(BaseModel):
    """新开发要做的一个工作包"""
    item: str                                          # 例如「重新设计 180 法兰机壳和端盖」
    type: CHANGE_TYPES
    evidence: list[str] = Field(default=[], description="证据编号列表，只写编号，如 [\"E3\"]")

    @field_validator("evidence", mode="before")
    @classmethod
    def _ids(cls, v):
        return evidence_ids(v)


class ResearchPlan(BaseModel):
    """新开发预研的答卷：只有依据和工作包，没有金额和天数（周期由规则表给）"""
    route: Literal["基于现有平台扩展", "全新平台开发", "建议不接"]
    reference_models: list[str] = []                   # 可以参考的现有型号
    findings: list[Finding]
    dev_tasks: list[DevTask] = []
    risks: list[str] = []
    open_questions: list[str] = []                     # 要向客户确认的问题
    summary: str                                       # 给研发负责人的一段话


class ReviewIssue(BaseModel):
    owner: Literal["tech", "cost", "lead", "commercial", "research"]
    rule: str
    message: str
