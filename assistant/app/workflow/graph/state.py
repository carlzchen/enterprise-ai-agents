"""
图上流转的数据结构。Agent 之间只交这些固定结构，不自由聊天。

图的状态（CaseState）里只放能转成 JSON 的普通字典和列表：状态要写进 checkpointer（SQLite），
进程重启后从断点恢复时原样读回来。Pydantic 模型只在节点的入口和出口做校验，进状态前 model_dump(mode="json")。
"""
import operator
from datetime import date
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

SUBTASK_TYPES = Literal["po_entry", "po_change", "delivery_query", "expedite", "spec_inquiry", "other"]
AGENT_OF = {"po_entry": "order_agent", "po_change": "order_agent", "delivery_query": "delivery_agent", "expedite": "delivery_agent",
            "spec_inquiry": "tech_agent"}            # other 不分派
TYPE_CN = {"po_entry": "新订单", "po_change": "订单变更", "delivery_query": "交期询问", "expedite": "加急", "spec_inquiry": "规格询价", "other": "无需处理"}


# ---------------------------------------------------------------- Supervisor 的输出

class SubTaskRefs(BaseModel):
    """子任务引用的单号和要求。只填邮件里明确写着的，没写的为空"""
    po_no: str | None = None                 # 客户的 PO 号
    order_no: str | None = None              # 我方销售订单号（SO 开头）
    customer_part_no: str | None = None
    requested_date: date | None = None       # 加急：客户要求的新交期
    attachment: str | None = None            # PO 附件文件名（由代码按附件分配，不让模型填）
    product: str | None = None               # 规格询价：客户描述的产品
    params: dict = {}                        # 规格询价：电压、功率等
    annual_volume: int | None = None


class SubTask(BaseModel):
    id: str = ""                             # S1、S2……由代码编号
    type: SUBTASK_TYPES
    summary: str = ""                        # 一句话说清客户要什么
    refs: SubTaskRefs = SubTaskRefs()


class Split(BaseModel):
    """Supervisor 的答卷：这封邮件拆成哪几件事"""
    subtasks: list[SubTask] = Field(min_length=1)


# ---------------------------------------------------------------- 子 Agent 的输出

class ProposedAction(BaseModel):
    """待审批的写操作。Agent 只能提议，审批通过后由执行节点去做"""
    id: str                                  # 案例内唯一，如 S1:erp、S2:task1、reply
    kind: Literal["send_email", "erp_save_sales_order", "record_po_version", "create_task"]
    summary: str                             # 给审批人看的一句话
    payload: dict


class AgentOutput(BaseModel):
    """一个子任务处理完的结果：给客户回复里的一段 + 证据 + 提议的写操作"""
    subtask_id: str
    type: str
    agent: str
    ok: bool = True                          # False：没办法自动处理，需要人看（原因写在 note）
    section: str = ""                        # 回复里属于这个子任务的一段话（客户的语言）
    facts: dict = {}                         # 这一段里能说的事实（单号、日期、数量），合成和审核都以它为准
    evidence: list[dict] = []                # 工具调用记录：{id, agent, tool, args, result}
    actions: list[ProposedAction] = []
    note: str = ""
    steps: list[dict] = []                   # ReAct 的步骤记录（调了哪些工具）
    seconds: float = 0.0


# ---------------------------------------------------------------- 审核

REVIEW_RULES = Literal["floor_price_leak", "cost_leak", "unsupported_commitment", "unsupported_number", "data_inconsistency", "tone"]


class ReviewIssue(BaseModel):
    rule: REVIEW_RULES
    detail: str
    quote: str = ""                          # 草稿里出问题的那句话


class ReviewResult(BaseModel):
    passed: bool
    issues: list[ReviewIssue] = []
    round: int = 0


class Draft(BaseModel):
    to: list[str]
    subject: str
    body: str
    in_reply_to: str | None = None
    lang: str = "zh"
    written_by: str = "template"             # llm / template


# ---------------------------------------------------------------- 人工审批

class Approval(BaseModel):
    """审批人在审批页提交的结果（resume 的内容）"""
    decision: Literal["approve", "reject"]
    approver: str
    final_subject: str | None = None         # 改过的主题 / 正文，不改就为空
    final_body: str | None = None
    actions: list[str] | None = None         # 批准执行哪些动作；为空表示全部
    note: str = ""


# ---------------------------------------------------------------- 图的状态

class CaseState(TypedDict, total=False):
    case_id: str
    raw_b64: str                             # 原始邮件（附件要交给订单 Agent 解析）
    mail: dict                               # message_id、from_addr、subject、body、received_at、附件名
    customer: dict | None                    # {id, code, name}；认不出是 None
    lang: str
    as_of: str                               # 「今天」：按邮件收到的日期判断交期
    subtasks: list[dict]
    results: Annotated[list[dict], operator.add]     # 并行的子 Agent 各自追加
    drafts: list[dict]                       # 每一轮的草稿（第 0 轮是初稿，之后是重写）
    reviews: list[dict]
    draft: dict
    approval: dict
    executed: list[dict]
    status: str                              # IGNORED / WAITING_APPROVAL / DONE / REJECTED
