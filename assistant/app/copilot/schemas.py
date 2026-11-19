"""
工具的参数定义（Pydantic）。模型通过 Function Calling 说"调用哪个工具、参数是什么"，
程序先用这里的模型做校验，校验不过就把错误还给模型重填，不会带着错参数往下执行。
类名就是工具名，类的说明文字和字段说明会原样发给模型。
"""
from typing import Literal

from pydantic import BaseModel, Field


class policy_question(BaseModel):
    """用户在问公司制度、规定、流程、标准（考勤、请假、加班、薪酬规则、报销、入离职、社保、审批权限）"""
    query: str = Field(description="补全指代后的完整问题")


class run_template(BaseModel):
    """用分析模板回答经营或人力数据问题。只要有合适的模板就优先用它"""
    template_id: str = Field(pattern=r"^T\d{2}$", description="模板编号，如 T07")
    group_by: list[str] = Field(default_factory=list, description="分组维度，只能从该模板的「分组」里选。问总数、合计、某个对象的值时留空；问「各…」「每个…」「哪些…」「分布」「排名」时才填")
    filters: dict[str, str | list[str]] = Field(default_factory=dict, description="过滤条件，键只能是该模板的「过滤」项，值用用户的原话或编码")
    period: str | None = Field(default=None, description="用户原话里的时间短语，如 上个月、三季度、2026年8月、最近半年；没说就留空，不要自己换算日期")
    top_n: int | None = Field(default=None, ge=1, le=200, description="只要前几名时填")
    order_by: str | None = Field(default=None, description="按哪个结果列排序，如 报废数、加班工时；不填就用模板默认的排序")
    ascending: bool | None = Field(default=None, description="问「最低、最少」填 true，问「最高、最多」填 false，否则留空")
    inherit: bool = Field(default=False, description="这一句是不是在追问上一轮（如「那步进线呢」「换成按季度看」）")


class free_query(BaseModel):
    """模板接不住的数据问题（需要模板没有的分组、过滤、指标组合或明细），交给 SQL 生成"""
    question: str = Field(description="补全指代和省略后的完整问题")
    period: str | None = Field(default=None, description="用户原话里的时间短语；没说就留空")
    inherit: bool = False


class attribution(BaseModel):
    """用户在问毛利、毛利率、利润为什么变了、下降或上升的原因"""
    period: str | None = Field(default=None, description="要分析的期间，用户原话里的时间短语")
    compare: str | None = Field(default=None, description="和谁比：用户原话，如 去年同期、同比、上个季度、二季度；没说就留空（默认环比）")
    by: Literal["产品系列", "事业部", "客户", "行业", "区域", "产品"] = "产品系列"
    filters: dict[str, str | list[str]] = Field(default_factory=dict, description="过滤条件，可用：事业部、产品系列、客户、行业、区域")
    inherit: bool = False


class what_if(BaseModel):
    """用户在做假设测算：如果……调整 x%，会怎样"""
    scenario: Literal["计件单价", "基本工资", "销售价格", "单位成本"]
    change_pct: float = Field(ge=-50, le=100, description="调整幅度的百分数，上调 5% 填 5，下降 3% 填 -3")
    period: str | None = Field(default=None, description="以哪个期间的数据为基础测算；没说就留空")
    filters: dict[str, str | list[str]] = Field(default_factory=dict)


class explain_metric(BaseModel):
    """用户在问某个指标是怎么算的、口径是什么"""
    metric: str = Field(description="指标名称")


class clarify(BaseModel):
    """问题缺少必要信息（不知道要看哪个指标或哪个对象）时，向用户追问"""
    question: str = Field(description="要问用户的话")
    options: list[str] = Field(default_factory=list, max_length=4, description="给用户的 2 到 4 个可选项")


class chitchat(BaseModel):
    """问候、感谢，或者和企业数据、制度无关的问题"""
    reply: str = Field(description="简短的回复，并说明自己能帮什么")


TOOLS = [policy_question, run_template, free_query, attribution, what_if, explain_metric, clarify, chitchat]
TOOL_MAP = {t.__name__: t for t in TOOLS}


class SqlDraft(BaseModel):
    """SQL 生成的结构化输出"""
    sql: str = Field(min_length=10)
    metrics: list[str] = Field(default_factory=list, description="用到的指标名称，来自口径库")
    explanation: str = Field(default="", description="一句话说明这条 SQL 在算什么")
