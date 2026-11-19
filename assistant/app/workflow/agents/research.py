"""
新开发预研 Agent（DeepAgents）。技术 Agent 判定「需要新开发」时进这里，代替原来的「直接转研发、什么都不给」。

    预研主 Agent ──write_todos 列计划──→ task 派活 ──→ 资料检索子 Agent（知识库：规格书、工艺、检验规范）
         │                                    └──→ 历史案例子 Agent（工程变更、相近型号）
         └── 汇总子 Agent 带证据编号的发现 ──→ 交 ResearchPlan 答卷 ──→ 回到图上的「规则审查」

  为什么这一步用 DeepAgents、别的 Agent 不用：
    成本、交期、商务的流程是固定的（查哪几个工具、按什么顺序），手写循环 + 图编排更可控；
    新开发预研是开放问题：要查什么资料、查几轮、先查案例还是先查规格，事先说不准，适合「自己列计划 + 派子 Agent」。
  用真实模型跑第一版时踩到的三个坑（DeepAgents 0.7）：
    write_todos 不再默认提供（要自己加 TodoListMiddleware）；默认给模型看文件系统和 execute 工具，模型会在空的虚拟盘上
    ls、grep 浪费调用次数（用 HideFilesystemTools 藏掉）；子 Agent 的调用次数按「每次被派」重算，主 Agent 反复派活时总量没有上限。
  它仍然被关在图里的一个节点中，外面的规矩不变：
    1. 工具还是那几个只读工具，按角色分（主 Agent 和两个子 Agent 各拿各的），调用照样记进证据账本
    2. 答卷是固定结构（ResearchPlan），没有金额和天数；新开发周期由规则表算，系统直接调工具记账，不让模型估
    3. 模型调用次数、工具调用次数都有上限；超了就停，交不出答卷由审查打回或带着问题出报告
    4. 虚拟文件系统用默认的 StateBackend（只在内存里），Agent 碰不到真实磁盘，也没有执行命令的工具
"""
import json
import time

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, TodoListMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.workflow.agents import tools as T
from app.workflow.agents.runtime import AgentRun
from app.workflow.agents.schemas import Finding, ResearchPlan

MAIN_CALLS, SUB_CALLS, TOOL_CALLS = 10, 8, 12      # 主 Agent 最多调 10 次模型，子 Agent 每次最多 8 次；每个 Agent 最多 12 次工具调用
MAX_TASKS, MAX_SEARCH = 3, 3                       # 主 Agent 最多派 3 次子 Agent；子 Agent 每次每个查询类工具最多 3 次
PER_TOOL = {"search_docs": MAX_SEARCH, "engineering_history": MAX_SEARCH, "find_similar": 2, "product_detail": 4}
HIDDEN = {"ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "execute"}
# 【讲解】为什么要单独限制派活次数：子 Agent 每被派一次，调用次数就重新计算；只限每个 Agent 自己的次数，主 Agent 反复派活时总成本没有上限。
# 真实模型跑第一版时就出过这个问题（一次预研跑了十几分钟），所以加了「派活次数」和「知识库检索次数」两道总量闸门。

PROMPT = """你是电机企业研发部的预研工程师。技术 Agent 已经判定：客户这个需求在现有标准品上改型做不出来，需要新开发。
你要给研发负责人一份预研结论：走哪条路线（基于现有平台扩展 / 全新平台开发 / 建议不接）、可以参考哪些现有型号、研发要做哪些工作包、有什么风险、要向客户确认什么。

做法：
1. 先用 write_todos 列出要查的事（3 到 5 条就够）。
2. 用 task 把查资料的活派给子 Agent（最多派 3 次，一般每个子 Agent 派一次就够）：
   - doc-researcher：查知识库里的规格书、选型手册、工艺和检验规范（例如最大法兰和功率做到多少、绝缘等级、编码器选项）
   - case-researcher：查历史工程变更和最接近的现有型号（以前有没有做过同类扩展、改了什么）
   两个子 Agent 可以同时派。子 Agent 返回的每条发现都带证据编号，你汇总时原样引用。
3. 你自己也可以用 find_similar、product_detail 核对型号。
4. 交卷：findings 每条都要带证据编号；dev_tasks 写工作包和改动类型；不要写任何金额、天数（这些由规则表算），也不要写工具结果里没有的参数值。"""

SUB_PROMPTS = {   # 子 Agent 名: (工具权限, 给主 Agent 看的说明, 子 Agent 的提示词)
    "doc-researcher": ("research/docs", "在企业知识库里查规格书、选型手册、工艺规程、检验规范，回答预研需要的具体问题，每条发现都带证据编号",
                       """你在企业知识库里查资料。用 search_docs 检索（可以换 2 到 3 种说法），需要时用 product_detail 看型号参数。
最多检索 3 次，查到能回答问题的内容就交卷。只报告检索结果里真实出现的内容；每条发现的 evidence 写检索结果的证据编号。查不到就如实说查不到。"""),
    "case-researcher": ("research/cases", "查历史工程变更和最接近的现有型号，看以前有没有做过同类扩展、改了什么，每条发现都带证据编号",
                        """你查历史案例。用 find_similar 找最接近的现有型号，用 engineering_history 查同类改动以前怎么做的（它返回最近的全部变更，查 1 到 2 次就够），需要时用 product_detail 看 BOM。
工具调用总共不要超过 6 次，然后交卷。
只报告工具结果里真实出现的内容；每条发现的 evidence 写工具结果的证据编号。"""),
}


class SubFindings(BaseModel):
    findings: list[Finding]


def _tools(agent: str, ledger: T.Ledger) -> list[StructuredTool]:
    """把账本工具包成 LangChain 工具：调用照样走 tools.call（权限检查 + 参数校验 + 记账），返回结果带证据编号"""
    allowed = T.RESEARCH_SCOPES[agent]

    def make(name: str) -> StructuredTool:
        _, schema, desc = T.TOOLS[name]

        def run(**kwargs) -> str:
            return json.dumps(T.call(name, kwargs, agent, ledger, allowed), ensure_ascii=False, default=str)[:3000]
        return StructuredTool.from_function(run, name=name, description=desc, args_schema=schema)
    return [make(n) for n in allowed]


class HideFilesystemTools(AgentMiddleware):
    """不给模型看文件系统和 execute 工具：预研只需要查知识库和 ERP，虚拟盘上什么都没有"""

    def wrap_model_call(self, request, handler):
        keep = [t for t in request.tools if (t.get("name") if isinstance(t, dict) else getattr(t, "name", None)) not in HIDDEN]
        return handler(request.override(tools=keep))


def _limits(model_calls: int, per_tool: dict[str, int]) -> list:
    """调用上限：模型调用次数、工具调用总数（到了就停）、单个工具的次数（到了只拦这个工具，Agent 可以接着交卷）"""
    return [HideFilesystemTools(), ModelCallLimitMiddleware(run_limit=model_calls, exit_behavior="end"),
            ToolCallLimitMiddleware(run_limit=TOOL_CALLS, exit_behavior="end"),
            *(ToolCallLimitMiddleware(tool_name=t, run_limit=n, exit_behavior="continue") for t, n in per_tool.items())]


# 【讲解】组装 DeepAgents：主 Agent + 两个子 Agent。子 Agent 也是结构化输出（SubFindings），发现和证据编号一起交回来；
# 每个 Agent 各配一套调用上限中间件；文件系统用默认的 StateBackend（只在内存里）。
def build(ledger: T.Ledger, model):
    from deepagents import create_deep_agent
    subagents = [{"name": name, "description": desc, "system_prompt": prompt, "tools": _tools(scope, ledger), "response_format": ToolStrategy(SubFindings),
                  "middleware": _limits(SUB_CALLS, {t: n for t, n in PER_TOOL.items() if t in T.RESEARCH_SCOPES[scope]})}
                 for name, (scope, desc, prompt) in SUB_PROMPTS.items()]
    return create_deep_agent(model=model, tools=_tools("research", ledger), system_prompt=PROMPT, subagents=subagents,
                             response_format=ToolStrategy(ResearchPlan), middleware=[TodoListMiddleware(), *_limits(MAIN_CALLS, {"task": MAX_TASKS})], name="research")


def _chat_model():
    from app.copilot import llm
    return llm.chat(False)


def run(task: str, ledger: T.Ledger, model=None) -> AgentRun:
    """跑一次预研。model 是 LangChain 的聊天模型（测试时换成假模型）；任何异常都不往外抛，记成没交卷"""
    run_, t0 = AgentRun(agent="research"), time.time()
    usage = UsageMetadataCallbackHandler()
    before = set(ledger.items)
    try:
        agent = build(ledger, model or _chat_model())
        out = agent.invoke({"messages": [{"role": "user", "content": task}]}, {"recursion_limit": 120, "callbacks": [usage]})
        run_.output = out.get("structured_response")
        if run_.output is None:
            run_.error = "预研 Agent 没有交卷（可能用完了调用次数）"
        run_.steps = [{"tool": "write_todos", "todos": out.get("todos", [])}] if out.get("todos") else []
        calls = [c for m in out.get("messages", []) if isinstance(m, AIMessage) for c in (m.tool_calls or [])]
        for c in calls:
            if c["name"] == "task":
                run_.steps.append({"tool": "task", "subagent": c["args"].get("subagent_type"), "description": str(c["args"].get("description", ""))[:200]})
    except Exception as e:
        run_.error = f"{type(e).__name__}: {str(e)[:200]}"
    run_.steps += [{"tool": i["tool"], "agent": i["agent"], "evidence_id": i["id"], "error": i["result"].get("error")} for k, i in ledger.items.items() if k not in before]
    run_.tokens = sum(int(u.get("total_tokens", 0)) for u in usage.usage_metadata.values())
    run_.seconds = round(time.time() - t0, 2)
    return run_
