"""
新开发预研节点（DeepAgents）的测试。主 Agent 和两个子 Agent 都换成按剧本回答的假聊天模型，验证的是：
图上「需要新开发」会走到预研节点、主 Agent 列计划并派子 Agent、工具调用记进证据账本、审查能打回编造的型号、
交不出答卷时不会卡死、新开发周期由系统按规则表算。需要本机的 MySQL（读 ERP 数据），不需要大模型和向量库。
"""
import json
import re
import uuid
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.workflow import erp
from app.workflow.agents import nonstd, research, tools as T
from tests.test_agents import Fake, call

try:
    erp.product("KL-SV-80-400W")
except Exception:                                            # 没有数据库就跳过
    pytest.skip("需要本机 MySQL", allow_module_level=True)

REQ = {"series": "SERVO", "voltage_v": 380, "power_w": 3000, "annual_volume": 800, "raw_text": "伺服电机 3kW 380V，年用量 800"}
SEL = {"grade": "NONE", "candidates": [{"model": "KL-SV-130-2000W", "name": "交流伺服电机 130法兰 2000W", "satisfied": 0, "total": 2, "rows": []}]}
DOCS = {"docs": [{"title": "KL系列交流伺服电机选型手册", "doc_key": "MAN-SV-SELECT", "version": "2", "section": "1 型号与参数", "text": "130 法兰最大 2000W，电压 220VAC"}]}


class NewDevFake(Fake):
    """技术 Agent 判定「需要新开发」，其余沿用 test_agents 的剧本"""

    def tech(self, n, got, retry):
        if "find_similar" not in got:
            return call("find_similar", {"series": "SERVO", "voltage_v": 380, "power_w": 3000})
        return call("submit", {"base_model": None, "feasibility": "需要新开发", "risk": "高", "changes": [], "reasoning": "现有伺服最大 2000W、只有 220V",
                               "open_questions": ["是否需要抱闸"]})


def ids(messages, tool=None) -> list[str]:
    """从工具结果里取出证据编号（tool 为空时取全部）"""
    names, out = {}, []
    for m in messages:
        if isinstance(m, AIMessage):
            names.update({c["id"]: c["name"] for c in m.tool_calls or []})
        if isinstance(m, ToolMessage) and (tool is None or names.get(m.tool_call_id) == tool):
            out += re.findall(r"\bE\d+\b", m.content)
    return list(dict.fromkeys(out))


def tc(name, args):
    return {"name": name, "args": args, "id": uuid.uuid4().hex[:8], "type": "tool_call"}


class Script:
    """三个 Agent 的剧本：按系统提示词认出是谁；mistakes 用来注入「编造型号」「一直不交卷」"""

    def __init__(self, mistakes=()):
        self.mistakes, self.main_runs, self.subagents, self.main_tools = set(mistakes), 0, [], set()

    def __call__(self, messages, tools) -> AIMessage:
        system = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
        system = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
        if "在企业知识库里查资料" in system:
            return self.docs(messages)
        if "你查历史案例" in system:
            return self.cases(messages)
        return self.main(messages, tools)

    def docs(self, messages):
        got = ids(messages, "search_docs")
        if not got:
            self.subagents.append("doc-researcher")
            return AIMessage("", tool_calls=[tc("search_docs", {"query": "伺服 最大功率 法兰", "series": "SERVO"})])
        return AIMessage("", tool_calls=[tc("SubFindings", {"findings": [{"point": "选型手册里 130 法兰最大做到 2000W，只有 220V", "evidence": got[:1]}]})])

    def cases(self, messages):
        sim, hist = ids(messages, "find_similar"), ids(messages, "engineering_history")
        if not sim:
            self.subagents.append("case-researcher")
            return AIMessage("", tool_calls=[tc("find_similar", {"series": "SERVO", "voltage_v": 380, "power_w": 3000})])
        if not hist:
            return AIMessage("", tool_calls=[tc("engineering_history", {"keyword": "功率", "series": "SERVO"})])
        return AIMessage("", tool_calls=[tc("SubFindings", {"findings": [{"point": "最接近的是 KL-SV-130-2000W", "evidence": sim[:1]},
                                                                          {"point": "历史上伺服只改过编码器连接", "evidence": hist[:1]}]})])

    def main(self, messages, tools):
        self.main_tools |= set(tools)
        done = {m.name for m in messages if isinstance(m, ToolMessage)}
        if "write_todos" not in done:
            self.main_runs += 1
            return AIMessage("", tool_calls=[tc("write_todos", {"todos": [{"content": "查选型手册的功率上限", "status": "in_progress"},
                                                                          {"content": "查历史扩展案例", "status": "pending"}]})])
        if "never_submit" in self.mistakes:
            return AIMessage("", tool_calls=[tc("product_detail", {"model": "KL-SV-130-2000W"})])
        if "spam_tasks" in self.mistakes:                       # 一直派活：看总量闸门能不能拦住
            sent = sum(1 for m in messages if isinstance(m, AIMessage) for c in m.tool_calls or [] if c["name"] == "task")
            if sent < 5:
                return AIMessage("", tool_calls=[tc("task", {"description": f"第 {sent + 1} 次查知识库", "subagent_type": "doc-researcher"})])
        elif "task" not in done:
            return AIMessage("", tool_calls=[tc("task", {"description": "查知识库：伺服最大功率和电压等级", "subagent_type": "doc-researcher"}),
                                             tc("task", {"description": "查历史案例和最接近型号", "subagent_type": "case-researcher"})])
        ev = ids(messages)
        ref = "KL-SV-999-9KW" if ("made_up_model" in self.mistakes and self.main_runs == 1) else "KL-SV-130-2000W"
        plan = {"route": "全新平台开发", "reference_models": [ref], "findings": [{"point": "现有 130 法兰平台最大 2000W、只有 220V", "evidence": ev[:1]},
                                                                                {"point": "最接近的现有型号是 KL-SV-130-2000W", "evidence": ev[1:2]}],
                "dev_tasks": [{"item": "设计 180 法兰机壳和端盖", "type": "HOUSING", "evidence": ev[:1]}, {"item": "重新设计 380V 绕组", "type": "WINDING"}],
                "risks": ["380V 绝缘等级要重新验证"], "open_questions": ["是否需要抱闸"], "summary": "需要全新平台，可参考 130 法兰平台的编码器和出线方案"}
        return AIMessage("", tool_calls=[tc("ResearchPlan", plan)])


class FakeChat(BaseChatModel):
    script: Any

    @property
    def _llm_type(self) -> str:
        return "fake-research"

    def bind_tools(self, tools, **kwargs):
        return self.bind(tool_names=[convert_to_openai_tool(t)["function"]["name"] for t in tools])

    def _generate(self, messages, stop=None, run_manager=None, tool_names=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.script(messages, tool_names or []))])


@pytest.fixture(autouse=True)
def stub_kb(monkeypatch):
    """知识库检索换成固定结果：测试的是编排，不依赖向量库和重排模型"""
    _, schema, desc = T.TOOLS["search_docs"]
    monkeypatch.setitem(T.TOOLS, "search_docs", (lambda query, model=None, series=None: DOCS, schema, desc))


def run(mistakes=()):
    script = Script(mistakes)
    return nonstd.evaluate_multi(REQ, 4, SEL, model=NewDevFake(), research_model=FakeChat(script=script)), script


def test_new_development_goes_to_deep_agent_and_passes_review():
    rep, script = run()
    assert [a["agent"] for a in rep["agents"]] == ["tech", "research"]           # 没有走成本、交期、商务
    assert rep["issues"] == [] and rep["rounds"] == 1
    assert sorted(script.subagents) == ["case-researcher", "doc-researcher"]     # 主 Agent 把活派给了两个子 Agent
    r = rep["research"]
    assert r["route"] == "全新平台开发" and r["reference_models"] == ["KL-SV-130-2000W"]
    assert r["kb_docs"][0]["doc_key"] == "MAN-SV-SELECT"                         # 知识库依据带文档编号和版本
    assert r["dev_schedule"]["first_batch_days"] == T.nre_rule()["new_development_days"]   # 周期由规则表算
    by = {e["id"]: e for e in rep["evidence"]}
    assert by[r["dev_schedule"]["evidence"]]["agent"] == "system"
    agents = {e["agent"] for e in rep["evidence"]}
    assert {"research/docs", "research/cases"} <= agents                         # 子 Agent 的工具调用都进了证据账本
    steps = next(a for a in rep["agents"] if a["agent"] == "research")
    assert steps["ok"]
    assert {"write_todos", "task"} <= script.main_tools                          # 列计划、派活的工具都在
    assert not script.main_tools & research.HIDDEN                               # 文件系统和 execute 工具不给模型看


def test_review_sends_research_back_when_model_is_made_up():
    rep, script = run(["made_up_model"])
    assert script.main_runs == 2                                                 # 第一次编了型号被打回，第二次改对
    assert rep["issues"] == [] and rep["rounds"] == 2
    assert rep["research"]["reference_models"] == ["KL-SV-130-2000W"]


def test_research_that_never_submits_stops_and_is_reported():
    rep, _ = run(["never_submit"])
    assert rep["research"]["dev_schedule"] is not None                           # 系统算的周期照样有
    assert any(i["owner"] == "research" and i["rule"] == "交卷" for i in rep["issues"])
    assert rep["rounds"] == nonstd.MAX_ROUNDS + 1                                # 打回两轮后带着问题出报告，不会无限循环


def test_research_tools_respect_scope():
    led = T.Ledger()
    main = {t.name: t for t in research._tools("research", led)}
    assert set(main) == {"find_similar", "product_detail"}                       # 主 Agent 拿不到知识库，必须派子 Agent
    docs = {t.name for t in research._tools("research/docs", led)}
    assert "engineering_history" not in docs and "search_docs" in docs
    out = json.loads(main["product_detail"].invoke({"model": "KL-SV-130-2000W"}))
    assert out["evidence_id"] in led.items and led.items[out["evidence_id"]]["agent"] == "research"


def test_dispatch_cap_stops_main_agent_from_spawning_without_limit():
    rep, script = run(["spam_tasks"])
    assert script.subagents.count("doc-researcher") == research.MAX_TASKS     # 派了 5 次，只执行了 3 次
    assert rep["research"]["route"] == "全新平台开发"                          # 被拦之后照样能交卷
