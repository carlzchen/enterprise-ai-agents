"""
非标评估多 Agent 的测试。用「按剧本回答的假模型」代替大模型，验证的是编排本身：
并行、审查打回、工具权限、步数上限、报告里的数字来源。需要本机的 MySQL（读 ERP 数据），不需要大模型。
"""
import json
import uuid

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.workflow import erp
from app.workflow.agents import nonstd, tools as T
from app.workflow.agents.runtime import run_agent
from app.workflow.agents.schemas import TechPlan

try:
    erp.product("KL-SV-80-400W")
except Exception:                                            # 没有数据库就跳过
    pytest.skip("需要本机 MySQL", allow_module_level=True)

REQ = {"series": "SERVO", "voltage_v": 220, "power_w": 400, "annual_volume": 5000, "raw_text": "400W 伺服，轴伸要 40mm（标准是 30mm），出线改航空插头"}
SEL = {"grade": "NEAR", "candidates": [{"model": "KL-SV-80-400W", "name": "交流伺服电机 80法兰 400W", "satisfied": 2, "total": 3, "rows": []}]}
SHAFT = {"type": "SHAFT", "description": "轴伸由 30mm 加长到 40mm", "material_from": "M-SH-14", "new_part": True}
CONN = {"type": "CONNECTOR", "description": "出线改航空插头", "material_from": "M-TERM", "new_part": True}


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex[:8]}])


def results(messages) -> dict:
    """从对话里取出之前工具调用的结果，按工具名索引（取最近一次）"""
    out, names = {}, {}
    for m in messages:
        if isinstance(m, AIMessage):
            for c in m.tool_calls or []:
                names[c["id"]] = c["name"]
        if isinstance(m, ToolMessage):
            out[names.get(m.tool_call_id)] = json.loads(m.content)
    return out


class Fake:
    """假模型：按系统提示词认出自己是哪个 Agent，再按剧本一步步调工具、最后交卷。可以注入「犯错」来测审查"""

    def __init__(self, mistakes=()):
        self.mistakes, self.calls = set(mistakes), {}

    def invoke(self, messages, tools):
        system = messages[0].content
        who = next(k for k, v in nonstd.PROMPTS.items() if system.startswith(v[:20]))
        n = self.calls[who] = self.calls.get(who, 0) + 1
        got, retry = results(messages), "审查意见" in messages[1].content
        return getattr(self, who)(n, got, retry)

    def tech(self, n, got, retry):
        if "tech_forbidden" in self.mistakes and n == 1:
            return call("recost", {"base_model": "KL-SV-80-400W", "changes": []})
        if "find_similar" not in got:
            return call("find_similar", {"series": "SERVO", "voltage_v": 220, "power_w": 400})
        if "product_detail" not in got:
            return call("product_detail", {"model": "KL-SV-80-400W"})
        if "never_submit" in self.mistakes:
            return call("product_detail", {"model": "KL-SV-80-400W"})
        e = [got["find_similar"]["evidence_id"], got["product_detail"]["evidence_id"]]
        return call("submit", {"base_model": "KL-SV-80-400W", "feasibility": "有条件可行", "risk": "中", "changes": [{**SHAFT, "evidence": e}, {**CONN, "evidence": e}],
                               "reasoning": "基于 KL-SV-80-400W 改转轴和出线"})

    def cost(self, n, got, retry):
        changes = [SHAFT] if ("cost_drops_change" in self.mistakes and not retry) else [SHAFT, CONN]
        if "recost" not in got or (retry and n == 3):
            return call("recost", {"base_model": "KL-SV-80-400W", "changes": changes})
        if "nre_fees" not in got:
            return call("nre_fees", {"change_types": ["SHAFT", "CONNECTOR"]})
        return call("submit", {"recost_evidence": got["recost"]["evidence_id"], "nre_evidence": got["nre_fees"]["evidence_id"], "notes": "新开转轴和插头按 1.2 倍估价"})

    def lead(self, n, got, retry):
        if "nre_fees" not in got:
            return call("nre_fees", {"change_types": ["SHAFT", "CONNECTOR"]})
        if "line_load" not in got:
            return call("line_load", {"series": "SERVO"})
        if "estimate_schedule" not in got or (retry and n == 4):
            days = 7 if ("lead_ignores_new_part" in self.mistakes and not retry) else 25
            return call("estimate_schedule", {"sample_days": got["nre_fees"]["sample_days"], "material_days": days, "busy": got["line_load"]["busy"]})
        return call("submit", {"schedule_evidence": got["estimate_schedule"]["evidence_id"], "load_evidence": got["line_load"]["evidence_id"], "risks": ["新开转轴的采购周期"]})

    def commercial(self, n, got, retry):
        uc = None
        if "price_for" not in got:
            # 从任务说明里读成本 Agent 的单位成本（剧本里直接查账本得到同样的值）
            return call("price_for", {"model": "KL-SV-80-400W", "unit_cost": self.unit_cost, "customer_id": 4, "annual_volume": 5000})
        text = "建议按阶梯价报价，一次性费用单独收取" + ("，可以再降 7%" if "made_up_number" in self.mistakes else "")
        return call("submit", {"price_evidence": got["price_for"]["evidence_id"], "nre_policy": "单独收取", "recommendation": text})


def run(mistakes=()):
    fake = Fake(mistakes)
    fake.unit_cost = T.recost("KL-SV-80-400W", [SHAFT, CONN])["unit_cost"]
    return nonstd.evaluate_multi(REQ, 4, SEL, model=fake), fake


def test_happy_path_numbers_come_from_tools():
    rep, fake = run()
    assert rep["issues"] == [] and rep["rounds"] == 1
    base = rules_cost = T.recost("KL-SV-80-400W", [])["unit_cost"]
    assert rep["cost"]["unit_cost"] > base and rep["cost"]["is_estimate"] is True          # 新开物料按系数估价，单位成本上升
    assert rep["cost"]["nre"]["nre_total"] == 3000 + 5000 + 2000 + 3000
    assert rep["lead"]["sample_days"] == 25 and rep["lead"]["first_batch_days"] >= 45      # 新开物料 25 天 + 客户测试 20 天（产线忙再加）
    assert min(t["unit_price"] for t in rep["commercial"]["tiers"]) >= rep["commercial"]["floor_price"]
    assert {a["agent"] for a in rep["agents"]} == {"tech", "cost", "lead", "commercial"}
    assert rep["warnings"] == []
    assert rules_cost == base


def test_review_sends_cost_back_when_a_change_is_missing():
    rep, fake = run(["cost_drops_change"])
    assert rep["issues"] == [] and rep["rounds"] == 2                                       # 第一轮被打回，第二轮通过
    assert [a["agent"] for a in rep["agents"]].count("cost") == 2
    assert [a["agent"] for a in rep["agents"]].count("tech") == 1                           # 只重做成本，不重做技术


def test_review_sends_lead_back_when_new_part_lead_time_ignored():
    rep, _ = run(["lead_ignores_new_part"])
    assert rep["issues"] == [] and [a["agent"] for a in rep["agents"]].count("lead") == 2


def test_agent_cannot_use_tools_outside_its_scope():
    rep, _ = run(["tech_forbidden"])
    tech = next(a for a in rep["agents"] if a["agent"] == "tech")
    assert tech["steps"][0]["tool"] == "recost" and "没有权限" in tech["steps"][0]["error"]


def test_step_limit_stops_a_runaway_agent():
    r = run_agent("tech", nonstd.PROMPTS["tech"], "x", TechPlan, T.SCOPES["tech"], T.Ledger(), Fake(["never_submit"]), max_steps=4)
    assert r.output is None and "用完 4 步" in r.error


def test_made_up_number_in_text_is_flagged():
    rep, _ = run(["made_up_number"])
    assert any("7" in w and "找不到" in w for w in rep["warnings"])


def test_review_checklist_directly():
    led = T.Ledger()
    rc = led.add("cost", "recost", {"base_model": "KL-SV-80-400W", "changes": [SHAFT]}, T.recost("KL-SV-80-400W", [SHAFT]))
    plan = TechPlan.model_validate({"base_model": "KL-SV-80-400W", "feasibility": "可行", "risk": "低", "changes": [{**SHAFT, "evidence": ["E1"]}, {**CONN, "evidence": ["E1"]}], "reasoning": ""})
    from app.workflow.agents.schemas import CostResult
    issues = nonstd.review({"tech": plan, "cost": CostResult(recost_evidence=rc["evidence_id"]), "ledger": led})
    rules = {i.rule for i in issues}
    assert "改动全部计入" in rules and "一次性费用" in rules and "交期依据" in rules and "定价依据" in rules


def test_recost_supports_adding_a_part():
    r = T.recost("KL-SV-80-400W", [{"type": "CONNECTOR", "description": "加一个航空插座", "new_part": True}])
    assert r["delta"] == 15.0 and r["is_estimate"] and "新增零件" in r["applied"][0]["effect"]
