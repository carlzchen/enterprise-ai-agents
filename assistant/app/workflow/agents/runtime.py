"""
一个 Agent 的运行循环（ReAct 式的「想 → 调工具 → 看结果 → 再想」）。

  1. 把任务和可用工具交给模型
  2. 模型要么调用工具（可以一次调几个），要么调用 submit 交卷
  3. 工具结果作为消息回给模型，回到第 2 步
  4. 交卷时用 Pydantic 校验答卷格式，不合格就把错误告诉模型让它改
  5. 用完步数还没交卷：返回失败，由上层决定怎么处理（不会无限循环）

模型接口只要求一个方法 invoke(messages, tools) -> AIMessage，所以测试时可以换成按剧本回答的假模型。
"""
import json
import time
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ValidationError

from app.workflow.agents import tools as T

SUBMIT = "submit"


@dataclass
class AgentRun:
    """一个 Agent 跑完之后的记录：答卷、调了哪些工具、用了多少步和 token"""
    agent: str
    output: BaseModel | None = None
    error: str | None = None
    steps: list[dict] = field(default_factory=list)
    tokens: int = 0
    seconds: float = 0.0


class LLMModel:
    """真实模型：复用问数项目的大模型入口（自带熔断、备用模型、并发闸门）。限流或偶发错误时等一下再试，最多三次"""

    def invoke(self, messages: list, tools: list[dict]) -> AIMessage:
        from app.copilot import llm
        last = None
        for attempt in range(3):
            try:
                model = llm.chat(False).bind_tools(tools, tool_choice="required")
                msg, _ = llm._guarded("workflow", lambda: model.invoke(messages), lambda: llm.chat(False, True).bind_tools(tools, tool_choice="auto").invoke(messages))
                return msg
            except Exception as e:                             # 备用模型不支持 required 时用 auto；还失败就退避重试
                last = e
                time.sleep(3 * (attempt + 1))
        raise last


# 【讲解】单个 Agent 的循环：模型每一步要么调工具、要么调 submit 交卷（tool_choice=required，不允许只说话）。
# 交卷就是一次带参数的工具调用，参数结构就是答卷的 Pydantic 模型，所以「结构化输出」和「工具调用」是同一套机制。
# 步数用完还没交卷就返回失败，防止 Agent 无限循环烧 token。
def run_agent(name: str, system: str, task: str, output: type[BaseModel], allowed: list[str], ledger: T.Ledger, model=None, max_steps: int = 8,
              registry: dict | None = None) -> AgentRun:
    """registry：自带的工具表（名字: (函数, 参数结构, 说明)），不给就用 tools.TOOLS。跟单图上的交付 Agent 用它传按客户限定范围的工具"""
    model = model or LLMModel()
    run, t0 = AgentRun(agent=name), time.time()
    submit_spec = {"type": "function", "function": {"name": SUBMIT, "description": "完成任务时调用，参数就是你的最终答卷", "parameters": output.model_json_schema()}}
    tool_specs = T.specs(allowed, registry) + [submit_spec]
    messages = [SystemMessage(system + "\n\n规则：数字只能来自工具的返回结果；交卷时引用证据编号（evidence_id）；不确定的写进说明里，不要猜。"), HumanMessage(task)]
    for step in range(max_steps):
        final = step >= max_steps - 2                          # 最后两步只给 submit 一个工具：查得再多也必须交卷
        if final and step == max_steps - 2:
            messages.append(HumanMessage("资料已经足够，请根据已有的工具结果直接调用 submit 交卷。"))
        msg = model.invoke(messages, [submit_spec] if final else tool_specs)
        run.tokens += int((getattr(msg, "usage_metadata", None) or {}).get("total_tokens", 0))
        messages.append(msg)
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:                                           # 模型只说话不调工具：提醒它必须调用工具或交卷
            messages.append(HumanMessage("请调用工具继续，或者调用 submit 交卷。"))
            continue
        for c in calls:
            if c["name"] == SUBMIT:
                try:
                    run.output = output.model_validate(c["args"])
                    run.steps.append({"step": step, "tool": SUBMIT, "ok": True})
                except ValidationError as e:
                    messages.append(ToolMessage(json.dumps({"error": f"答卷格式不对，请修正后重新提交：{str(e)[:400]}"}, ensure_ascii=False), tool_call_id=c["id"]))
                    run.steps.append({"step": step, "tool": SUBMIT, "ok": False, "error": str(e)[:200]})
                    continue
            else:
                result = T.call(c["name"], c["args"], name, ledger, allowed, registry)
                run.steps.append({"step": step, "tool": c["name"], "args": c["args"], "evidence_id": result.get("evidence_id"), "error": result.get("error")})
                messages.append(ToolMessage(json.dumps(result, ensure_ascii=False, default=str)[:3000], tool_call_id=c["id"]))
        if run.output is not None:
            break
    else:
        run.error = f"用完 {max_steps} 步还没有交卷"
    run.seconds = round(time.time() - t0, 2)
    return run
