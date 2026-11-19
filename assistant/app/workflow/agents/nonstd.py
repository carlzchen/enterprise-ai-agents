"""
非标询价评估（多 Agent）。选型结果是「近似匹配」或「无匹配」时触发，代替原来的「直接转给工程师」。

    技术 Agent ──→ 成本 Agent ──┐
       │        └→ 交期 Agent ──┴→ 商务 Agent ──→ 审查 ──→ 报告
       │               ↑                              │ ↑
       │               └──── 有矛盾：打回给负责的 Agent ┘ │（最多两轮）
       └─ 需要新开发 ──→ 预研 Agent（DeepAgents，research.py）─┘

  为什么先技术、再成本和交期并行：成本要先知道改哪些料才能算，交期要先知道改哪几类才能估。
  编排用 LangGraph：节点是 Agent，边是交接顺序；并行、打回都是图上的边，不靠模型自己决定流程。
  审查是一张规则清单（确定性检查），专门查「各部分对不上」：技术改了料成本没算、新开物料交期没考虑、报价低于底价等。
  报告里的数字一律从证据账本取（工具算出来的），Agent 写的说明文字里出现的数字也要能在账本里找到。

另外两种对照方案，用来证明多 Agent 是否真的更好（evaluate.py 里比较）：
  single_agent  一个 Agent 拿全部工具，一次交全部答卷
  single_call   不给工具，把查好的资料一次塞给模型，让它直接给结论和数字
"""
import json
import operator
import time
import uuid
from typing import Annotated, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel

from app.shared.runtime.logger import logger
from app.workflow import store
from app.workflow.agents import research as R
from app.workflow.agents import tools as T
from app.workflow.agents.runtime import AgentRun, run_agent
from app.workflow.agents.schemas import CHANGE_CN, CommercialResult, CostResult, LeadResult, ResearchPlan, ReviewIssue, TechPlan
from app.workflow.understand import unknown_numbers

MAX_ROUNDS = 2
_DDL = """CREATE TABLE IF NOT EXISTS wf_nonstd_eval (
  eval_id VARCHAR(32) PRIMARY KEY, inquiry_id VARCHAR(32), item_index INT, opp_id VARCHAR(32), mode VARCHAR(12) NOT NULL COMMENT 'multi / single_agent / single_call',
  status VARCHAR(10) NOT NULL COMMENT 'RUNNING / DONE / FAILED', report JSON, trace JSON, tokens INT, seconds DECIMAL(8,2), created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  decision VARCHAR(10), decided_by VARCHAR(60), decided_at DATETIME, KEY idx_inq (inquiry_id)) COMMENT '非标询价评估'"""

# ---------------------------------------------------------------- 各 Agent 的职责说明（系统提示词）

PROMPTS = {
    "tech": """你是电机企业的应用工程师。客户的需求在标准品里找不到完全匹配的型号，你要给出改型方案。
做法：先用 find_similar 找最接近的型号，用 product_detail 看它的参数和 BOM，必要时用 engineering_history 看以前同类改动、用 search_docs 查规格书和工艺文件。
然后决定：选哪个型号做基础，要改哪些地方。每项改动写清类型、改什么、替换 BOM 里的哪个物料（material_from 用 BOM 里的物料编码），
有现成物料可换就填 material_to，需要新开物料就 new_part=true，用量变化填 qty_factor；
不替换原有物料、而是新增一个零件（例如加一个插座、加一个彩盒），material_from 留空、new_part=true。
只列客户要求直接带来的改动，不要为了保险多列；查 2 到 4 次工具就应该交卷。每项改动在 evidence 里写上依据的证据编号。
如果差得太远（例如功率大一倍以上），feasibility 写「需要新开发」，base_model 可以为空。客户没说清楚、影响方案的地方写进 open_questions。""",
    "cost": """你是成本核算员。根据技术方案估算改型后的成本。
必须调用 recost，把技术方案里的改动清单原样传进去（不要增减改动项），得到单位成本；再调用 nre_fees 查这些改动类型的一次性费用。
交卷时 recost_evidence 填 recost 结果的证据编号，nre_evidence 填 nre_fees 的证据编号。成本数字全部由工具计算，不要自己算。
如果 recost 返回了 warning（某项改动没计入），在 notes 里写明。""",
    "lead": """你是计划员。估算打样周期和首批量产交期。
先用 nre_fees 查这些改动类型的打样天数，用 material_lead_time 查改动涉及的物料（新开物料写 NEW:被替换的物料编码）的采购周期，用 line_load 查产线负荷，
最后把打样天数、最长的物料周期、产线是否繁忙传给 estimate_schedule 得到交期。需要新开发时 new_development=true。
交卷时 schedule_evidence 填 estimate_schedule 的证据编号，load_evidence 填 line_load 的证据编号，risks 写交期风险。""",
    "commercial": """你是销售内勤。根据估算的单位成本给出报价建议。
用 price_for 按定价规则得到阶梯价和底价（unit_cost 用成本 Agent 算出的单位成本），用 deal_history 看历史成交价。
一次性费用决定怎么处理：金额大时建议「单独收取」，金额小、年用量大时可以「分摊进单价」（要调用 amortize 算每台加多少，并把分摊后的单位成本再走一次 price_for）。
交卷时 price_evidence 填最终采用的 price_for 证据编号；recommendation 写给销售的建议，不要写工具结果里没有的数字。""",
}


def _req_text(req: dict) -> str:
    keys = {"series": "产品线", "voltage_v": "电压(V)", "power_w": "功率(W)", "speed_rpm": "转速(rpm)", "torque_nm": "转矩(N·m)", "frame_size": "机座号",
            "annual_volume": "年用量", "target_price": "目标价"}
    parts = [f"{v}：{req[k]}" for k, v in keys.items() if req.get(k) not in (None, "")]
    if req.get("special_requirements"):
        parts.append("定制要求：" + "；".join(req["special_requirements"]))
    return "；".join(parts) + (f"\n客户原话：{req['raw_text']}" if req.get("raw_text") else "")


class S(TypedDict, total=False):
    req: dict                                  # 客户需求（结构化 + 原话）
    customer_id: int
    selection: dict                            # 规则选型的结果（分级和候选）
    ledger: T.Ledger
    model: object                              # 模型（测试时是假模型）
    research_model: object                     # 预研 Agent 用的 LangChain 聊天模型（测试时是假模型；空 = 用真实模型）
    tech: TechPlan
    cost: CostResult
    lead: LeadResult
    commercial: CommercialResult
    research: ResearchPlan
    dev_schedule: str                          # 新开发周期的证据编号（系统按规则表算，不经过模型）
    feedback: dict                             # 审查打回时给各 Agent 的意见
    issues: list
    round: int
    runs: Annotated[list, operator.add]        # 每个 Agent 每次运行的记录（并行节点各自追加）
    report: dict


def _feedback(state: S, who: str) -> str:
    fb = (state.get("feedback") or {}).get(who)
    return f"\n\n审查意见（上一版有问题，请修正）：\n{fb}" if fb else ""


def _run(state: S, who: str, task: str, output: type[BaseModel]) -> AgentRun:
    run = run_agent(who, PROMPTS[who], task + _feedback(state, who), output, T.SCOPES[who], state["ledger"], state.get("model"))
    if run.output is None:
        logger.warning(f"[nonstd] {who} Agent 没有交卷：{run.error}")
    return run


# ---------------------------------------------------------------- 节点

def node_tech(state: S) -> dict:
    sel = state["selection"]
    task = (f"客户需求：\n{_req_text(state['req'])}\n\n规则选型的结果：{sel['grade']}（{'近似匹配' if sel['grade'] == 'NEAR' else '无匹配'}），候选：\n" +
            json.dumps(sel["candidates"], ensure_ascii=False)[:3000])
    run = _run(state, "tech", task, TechPlan)
    return {"tech": run.output, "runs": [run]}


def _plan_text(state: S) -> str:
    return json.dumps(state["tech"].model_dump(), ensure_ascii=False)


def node_cost(state: S) -> dict:
    run = _run(state, "cost", f"技术方案：\n{_plan_text(state)}", CostResult)
    return {"cost": run.output, "runs": [run]}


def node_lead(state: S) -> dict:
    run = _run(state, "lead", f"技术方案：\n{_plan_text(state)}\n产品线：{state['req'].get('series')}", LeadResult)
    return {"lead": run.output, "runs": [run]}


# 【讲解】预研节点：把 DeepAgents 关在图的一个节点里。进来之前技术 Agent 已经判定「需要新开发」；
# 预研 Agent 自己列计划、派子 Agent 查知识库和历史变更，交回固定结构的 ResearchPlan。
# 新开发周期不让模型估：系统直接按规则表调用 estimate_schedule，结果记进证据账本。
def node_research(state: S) -> dict:
    tech = state["tech"]
    task = (f"客户需求：\n{_req_text(state['req'])}\n\n技术 Agent 的判断：{tech.feasibility}，风险{tech.risk}。理由：{tech.reasoning}\n"
            f"技术 Agent 留下的待确认问题：{'；'.join(tech.open_questions) or '无'}\n"
            f"规则选型给出的最接近型号：{', '.join(c['model'] for c in state['selection'].get('candidates', [])[:5]) or '无'}" + _feedback(state, "research"))
    run = R.run(task, state["ledger"], state.get("research_model"))
    if run.output is None:
        logger.warning(f"[nonstd] 预研 Agent 没有交卷：{run.error}")
    out = {"research": run.output, "runs": [run]}
    if not state.get("dev_schedule"):
        sch = T.call("estimate_schedule", {"sample_days": 0, "material_days": 0, "busy": False, "new_development": True}, "system", state["ledger"], ["estimate_schedule"])
        out["dev_schedule"] = sch.get("evidence_id")
    return out


def node_commercial(state: S) -> dict:
    rc = state["ledger"].get(state["cost"].recost_evidence, "recost") if state.get("cost") else None
    nre = state["ledger"].get(state["cost"].nre_evidence, "nre_fees") if state.get("cost") else None
    task = (f"基础型号：{state['tech'].base_model}；客户编号：{state['customer_id']}；年用量：{state['req'].get('annual_volume')}；客户目标价：{state['req'].get('target_price')}\n"
            f"成本 Agent 算出的单位成本：{rc['result']['unit_cost'] if rc else '没有'}（证据 {state['cost'].recost_evidence if state.get('cost') else '-'}）\n"
            f"一次性费用合计：{nre['result']['nre_total'] if nre else '没有'}")
    run = _run(state, "commercial", task, CommercialResult)
    return {"commercial": run.output, "runs": [run]}


# 【讲解】审查清单（多 Agent 里「互相挑错」的那一环），全部是确定性规则：
#   技术：每项改动有没有证据；成本：是不是按技术方案的全部改动算的、一次性费用齐不齐；
#   交期：新开物料的采购周期、打样天数、产线忙闲有没有考虑；商务：定价用的成本和成本 Agent 的结果是否一致。
# 每个问题标明归谁，after_review 只把问题打回给负责的 Agent，不全部重做。
def review(state: S) -> list[ReviewIssue]:
    """审查清单：逐项检查各部分是否对得上。每一项都是确定性的规则，不靠模型判断"""
    led, out = state["ledger"], []
    tech, cost, lead, com = state.get("tech"), state.get("cost"), state.get("lead"), state.get("commercial")

    def add(owner, rule, msg):
        out.append(ReviewIssue(owner=owner, rule=rule, message=msg))
    if tech is None:
        add("tech", "交卷", "技术 Agent 没有给出方案")
        return out
    if tech.feasibility != "需要新开发" and not tech.base_model:
        add("tech", "基础型号", "方案可行，但没有指定基础型号")
    for ch in tech.changes:
        if not ch.evidence or any(e not in led.items for e in ch.evidence):
            add("tech", "证据", f"改动「{ch.description}」没有引用有效的证据编号")
    if tech.feasibility == "需要新开发":                 # 新开发不估成本和报价，审查预研结论后转研发
        _review_research(state, add)
        return out
    # 成本：必须基于技术方案的全部改动重算
    rc = led.get(cost.recost_evidence, "recost") if cost else None
    if not rc:
        add("cost", "成本依据", "没有引用 recost 的计算结果")
    else:
        if rc["args"]["base_model"] != tech.base_model:
            add("cost", "基础型号一致", f"成本按 {rc['args']['base_model']} 算的，技术方案的基础型号是 {tech.base_model}")
        need = {(c.type, c.material_from or "新增") for c in tech.changes if c.material_from or c.new_part}
        got = {(c["type"], c.get("material_from") or "新增") for c in rc["args"]["changes"] if c.get("material_from") or c.get("new_part")}
        if need - got:
            add("cost", "改动全部计入", "技术方案里这些换料没有计入成本：" + "、".join(f"{CHANGE_CN[t]}（{m}）" for t, m in need - got))
        for a in rc["result"].get("applied", []):
            if a.get("warning"):
                add("tech", "物料编码", f"「{a['description']}」：{a['warning']}")
    nre = led.get(cost.nre_evidence, "nre_fees") if cost else None
    if cost and not nre:
        add("cost", "一次性费用", "没有查一次性费用")
    elif nre:
        missing = {c.type for c in tech.changes} - {i["type"] for i in nre["result"]["items"]}
        if missing:
            add("cost", "一次性费用齐全", "这些改动类型没有算一次性费用：" + "、".join(CHANGE_CN[m] for m in missing))
    # 交期：新开物料的采购周期、各改动的打样天数、产线是否繁忙都要考虑到
    sch = led.get(lead.schedule_evidence, "estimate_schedule") if lead else None
    if not sch:
        add("lead", "交期依据", "没有引用 estimate_schedule 的计算结果")
    else:
        rule = T.nre_rule()
        if any(c.new_part for c in tech.changes) and sch["args"]["material_days"] < rule["new_part_lead_days"]:
            add("lead", "新开物料周期", f"方案里有新开物料（默认采购周期 {rule['new_part_lead_days']} 天），交期估算只按 {sch['args']['material_days']} 天算")
        if nre and sch["args"]["sample_days"] < nre["result"]["sample_days"]:
            add("lead", "打样天数", f"改动类型要求打样至少 {nre['result']['sample_days']} 天，交期估算只按 {sch['args']['sample_days']} 天")
        load = led.get(lead.load_evidence, "line_load")
        if load and load["result"]["busy"] and not sch["args"]["busy"]:
            add("lead", "产线负荷", "产线已经很忙，交期估算却按不忙算")
    # 商务：报价必须基于成本 Agent 的单位成本；分摊时要加上分摊额；任何一档不能低于底价（由定价规则保证，这里复核）
    pr = led.get(com.price_evidence, "price_for") if com else None
    if not pr:
        add("commercial", "定价依据", "没有引用 price_for 的定价结果")
    elif rc:
        expect = rc["result"]["unit_cost"]
        if com.nre_policy == "分摊进单价":
            am = led.get(com.amortize_evidence, "amortize")
            if not am:
                add("commercial", "分摊", "选择了分摊进单价，但没有分摊计算")
            else:
                expect = round(expect + am["result"]["per_unit"], 4)
                if nre and abs(am["result"]["nre_total"] - nre["result"]["nre_total"]) > 0.01:
                    add("commercial", "分摊金额", f"分摊的金额 {am['result']['nre_total']} 和一次性费用 {nre['result']['nre_total']} 不一致")
        if abs(pr["args"]["unit_cost"] - expect) > 0.01:
            add("commercial", "成本一致", f"定价用的单位成本 {pr['args']['unit_cost']} 和成本 Agent 的结果 {expect} 不一致")
    return out


# 【讲解】预研的审查清单，同样是确定性规则：每条发现的证据编号要真实存在；查到了知识库资料就必须引用；
# 参考型号要在工具结果里出现过（防止编造型号）；路线是开发就必须列出研发工作包。
def _review_research(state: S, add) -> None:
    led, res = state["ledger"], state.get("research")
    if res is None:
        add("research", "交卷", "预研 Agent 没有交卷")
        return
    for f in res.findings:
        if not f.evidence or any(e not in led.items for e in f.evidence):
            add("research", "证据", f"发现「{f.point[:40]}」没有引用有效的证据编号")
    for t in res.dev_tasks:
        if any(e not in led.items for e in t.evidence):
            add("research", "证据", f"工作包「{t.item[:40]}」引用了不存在的证据编号")
    kb = [i["id"] for i in led.items.values() if i["tool"] == "search_docs" and i["result"].get("docs")]
    cited = {e for f in res.findings for e in f.evidence} | {e for t in res.dev_tasks for e in t.evidence}
    if kb and not cited & set(kb):
        add("research", "知识库依据", "知识库查到了资料，但结论没有引用任何一条")
    known = led.numbers_text()
    for m in res.reference_models:
        if m not in known:
            add("research", "参考型号", f"参考型号 {m} 在工具结果里找不到，可能是编的")
    if res.route != "建议不接" and not res.dev_tasks:
        add("research", "工作包", f"路线是「{res.route}」，但没有列研发工作包")


def node_review(state: S) -> dict:
    issues = review(state)
    fb = {}
    for i in issues:
        fb.setdefault(i.owner, []).append(f"- {i.rule}：{i.message}")
    return {"issues": [i.model_dump() for i in issues], "feedback": {k: "\n".join(v) for k, v in fb.items()}, "round": state.get("round", 0) + 1}


# 【讲解】打回路由：技术方案有问题 → 回到技术（后面全部重做）；只是成本或交期有问题 → 只重做那一个；最多两轮，超过就带着问题出报告让人判断。
def after_review(state: S) -> list[str] | str:
    """没问题 → 出报告；有问题且没超轮数 → 打回给负责的 Agent（技术被打回时，后面的都要重做）"""
    if not state["issues"] or state["round"] > MAX_ROUNDS:
        return "node_report"
    owners = {i["owner"] for i in state["issues"]}
    if "tech" in owners:
        return "node_tech"
    if "research" in owners:
        return "node_research"
    nodes = [n for o, n in (("cost", "node_cost"), ("lead", "node_lead")) if o in owners]
    return nodes or "node_commercial"


# 【讲解】返回两个节点名 = LangGraph 并行执行：成本和交期互不依赖，同时跑；两者都完成后才进入商务（图上的汇合）。
def after_tech(state: S) -> list[str] | str:
    if state.get("tech") is not None and state["tech"].feasibility == "需要新开发":
        return "node_research"                            # 新开发：交给预研 Agent（DeepAgents）
    if state.get("tech") is None or state["tech"].feasibility == "不可行":
        return "node_review"
    return ["node_cost", "node_lead"]                     # 成本和交期并行


def node_report(state: S) -> dict:
    return {"report": build_report(state)}


# 【讲解】报告里的数字一律从证据账本取（工具算的），不取 Agent 写的；Agent 写的说明文字里如果出现账本里没有的数字，标成提醒让人核对。
def build_report(state: S) -> dict:
    """组装报告：数字全部从证据账本取；说明文字里的数字再核对一遍来源"""
    led, tech = state["ledger"], state.get("tech")
    rep = {"requirement": state["req"], "selection_grade": state["selection"]["grade"], "issues": state.get("issues", []), "rounds": state.get("round", 0),
           "tech": tech.model_dump() if tech else None, "warnings": []}
    cost, lead, com = state.get("cost"), state.get("lead"), state.get("commercial")
    rc = led.get(cost.recost_evidence, "recost") if cost else None
    nre = led.get(cost.nre_evidence, "nre_fees") if cost else None
    sch = led.get(lead.schedule_evidence, "estimate_schedule") if lead else None
    pr = led.get(com.price_evidence, "price_for") if com else None
    am = led.get(com.amortize_evidence, "amortize") if com and com.amortize_evidence else None
    rep["cost"] = {"evidence": cost.recost_evidence, **{k: rc["result"][k] for k in ("base_unit_cost", "unit_cost", "delta", "applied", "is_estimate", "estimated_parts")},
                   "nre": nre["result"] if nre else None, "notes": cost.notes} if rc else None
    rep["lead"] = {"evidence": lead.schedule_evidence, **sch["result"], "risks": lead.risks} if sch else None
    rep["commercial"] = {"evidence": com.price_evidence, "tiers": pr["result"]["tiers"], "floor_price": pr["result"]["floor_price"], "level": pr["result"]["level"],
                         "unit_cost_used": pr["args"]["unit_cost"], "nre_policy": com.nre_policy, "amortize": am["result"] if am else None,
                         "recommendation": com.recommendation} if pr else None
    res, dev = state.get("research"), led.get(state.get("dev_schedule"), "estimate_schedule")
    if res or dev:
        cited = {e for f in res.findings for e in f.evidence} if res else set()
        docs = [{"evidence": i["id"], **{k: d.get(k) for k in ("title", "doc_key", "version", "section")}} for i in led.items.values()
                if i["tool"] == "search_docs" and i["id"] in cited for d in i["result"].get("docs", [])]
        rep["research"] = {**(res.model_dump() if res else {}), "dev_schedule": {"evidence": dev["id"], **dev["result"]} if dev else None, "kb_docs": docs}
        if not any(i["tool"] == "search_docs" and i["result"].get("docs") for i in led.items.values()):
            rep["warnings"].append("知识库没有查到相关资料（或暂时不可用），预研结论只基于历史变更和型号数据")
    allowed = led.numbers_text() + " " + json.dumps(state["req"], ensure_ascii=False, default=str)
    for who, text in (("技术说明", tech.reasoning if tech else ""), ("成本说明", cost.notes if cost else ""), ("交期风险", " ".join(lead.risks) if lead else ""),
                      ("商务建议", com.recommendation if com else ""),
                      ("预研结论", " ".join([res.summary, *res.risks, *(f.point for f in res.findings)]) if res else "")):
        bad = unknown_numbers(text, {}, allowed)
        if bad:
            rep["warnings"].append(f"{who}里有数字在工具结果里找不到：{'、'.join(bad[:5])}，请人工核对")
    if state.get("issues"):
        rep["warnings"].append(f"审查经过 {state.get('round')} 轮后仍有 {len(state['issues'])} 个问题没有解决，见问题清单")
    rep["evidence"] = list(led.items.values())
    return rep


def _build():
    g = StateGraph(S)
    for fn in (node_tech, node_cost, node_lead, node_commercial, node_research, node_review, node_report):
        g.add_node(fn.__name__, fn)
    g.set_entry_point("node_tech")
    g.add_conditional_edges("node_tech", after_tech, ["node_cost", "node_lead", "node_research", "node_review"])
    g.add_edge("node_research", "node_review")
    g.add_edge("node_cost", "node_commercial")
    g.add_edge("node_lead", "node_commercial")
    g.add_edge("node_commercial", "node_review")
    g.add_conditional_edges("node_review", after_review, ["node_tech", "node_cost", "node_lead", "node_commercial", "node_research", "node_report"])
    g.add_edge("node_report", END)
    return g.compile()


GRAPH = _build()


def evaluate_multi(req: dict, customer_id: int, selection: dict, model=None, research_model=None) -> dict:
    t0, ledger = time.time(), T.Ledger()
    final = GRAPH.invoke({"req": req, "customer_id": customer_id, "selection": selection, "ledger": ledger, "model": model, "research_model": research_model,
                          "round": 0, "runs": [], "feedback": {}},
                         {"recursion_limit": 40})
    return _finish("multi", final["report"], final["runs"], t0)


def _finish(mode: str, report: dict, runs: list[AgentRun], t0: float) -> dict:
    report["mode"] = mode
    report["agents"] = [{"agent": r.agent, "ok": r.output is not None, "error": r.error, "steps": r.steps, "tokens": r.tokens, "seconds": r.seconds} for r in runs]
    report["tokens"] = sum(r.tokens for r in runs)
    report["seconds"] = round(time.time() - t0, 2)
    return report


# ---------------------------------------------------------------- 对照方案

class SingleAnswer(BaseModel):
    tech: TechPlan
    cost: CostResult
    lead: LeadResult
    commercial: CommercialResult


# 【讲解】对照方案：一个 Agent 拿全部工具。和多 Agent 用同一套审查清单检查，用来回答「拆成多个 Agent 到底有没有用」。
def evaluate_single_agent(req: dict, customer_id: int, selection: dict, model=None) -> dict:
    """对照方案 B：一个 Agent 拿全部工具，一次交出技术、成本、交期、商务全部答卷"""
    t0, ledger = time.time(), T.Ledger()
    system = "你要独自完成一份非标询价评估，包含技术方案、成本、交期、商务建议四部分。\n\n" + "\n\n".join(f"【{k}】{v}" for k, v in PROMPTS.items())
    task = (f"客户编号：{customer_id}\n客户需求：\n{_req_text(req)}\n\n规则选型结果：{selection['grade']}，候选：\n" + json.dumps(selection["candidates"], ensure_ascii=False)[:3000])
    run = run_agent("single", system, task, SingleAnswer, T.SCOPES["single"], ledger, model, max_steps=16)
    state = {"req": req, "selection": selection, "ledger": ledger, "round": 0}
    if run.output:
        state.update(tech=run.output.tech, cost=run.output.cost, lead=run.output.lead, commercial=run.output.commercial)
    state["issues"] = [i.model_dump() for i in review(state)] if run.output else [{"owner": "tech", "rule": "交卷", "message": run.error or "没有交卷"}]
    return _finish("single_agent", build_report(state), [run], t0)


class CallAnswer(BaseModel):
    tech: TechPlan
    unit_cost: float | None = None
    nre_total: float | None = None
    first_batch_days: int | None = None
    price: float | None = None
    recommendation: str = ""


def evaluate_single_call(req: dict, customer_id: int, selection: dict, model=None) -> dict:
    """对照方案 A：不给工具。系统先把可能用到的资料查好，一次性交给模型，让它直接给方案和数字"""
    t0, ledger = time.time(), T.Ledger()
    top = selection["candidates"][0]["model"] if selection.get("candidates") else None
    ctx = {"候选": selection["candidates"], "基础型号详情": T.product_detail(top) if top else None, "基础成本": T.recost(top, []) if top else None,
           "一次性费用规则": T.nre_rule()["types"], "产线负荷": T.line_load(req.get("series") or "") if req.get("series") else None}
    from app.copilot import llm
    prompt = (f"你要完成一份非标询价评估。客户编号 {customer_id}。\n客户需求：\n{_req_text(req)}\n\n可用资料：\n{json.dumps(ctx, ensure_ascii=False, default=str)[:9000]}\n\n"
              f"请直接给出技术方案（tech），以及你估算的单位成本、一次性费用合计、首批交期天数、建议单价。只输出 JSON，结构：{json.dumps(CallAnswer.model_json_schema(), ensure_ascii=False)[:2500]}")
    tokens, ans, err = 0, None, None
    try:
        msg = (model.invoke([("human", prompt)], []) if model else llm.invoke(prompt, json_mode=True, app="workflow"))
        tokens = int((getattr(msg, "usage_metadata", None) or {}).get("total_tokens", 0))
        ans = CallAnswer.model_validate(json.loads(llm.strip_fence(msg.content)))
    except Exception as e:
        err = str(e)[:200]
    rep = {"requirement": req, "selection_grade": selection["grade"], "tech": ans.tech.model_dump() if ans else None, "warnings": [], "issues": []}
    if ans and ans.tech.base_model:                       # 用程序重算一遍，看模型自己给的数字差多少
        truth = T.recost(ans.tech.base_model, [c.model_dump() for c in ans.tech.changes])
        rep["claimed"] = {k: getattr(ans, k) for k in ("unit_cost", "nre_total", "first_batch_days", "price")}
        rep["recomputed_unit_cost"] = truth.get("unit_cost")
        if ans.unit_cost and truth.get("unit_cost"):
            gap = abs(ans.unit_cost - truth["unit_cost"]) / truth["unit_cost"]
            rep["unit_cost_gap"] = round(gap, 4)
            if gap > 0.02:
                rep["issues"].append({"owner": "cost", "rule": "成本自算", "message": f"模型自己估的单位成本 {ans.unit_cost} 和程序按同一方案算出的 {truth['unit_cost']} 相差 {gap:.1%}"})
    run = AgentRun(agent="single_call", output=ans, error=err, tokens=tokens)
    return _finish("single_call", rep, [run], t0)


MODES = {"multi": evaluate_multi, "single_agent": evaluate_single_agent, "single_call": evaluate_single_call}


# ---------------------------------------------------------------- 接到跟单流程里

def ensure() -> None:
    store.execute(_DDL)


def start(inquiry_id: str, item_index: int, opp_id: str, req: dict, customer_id: int, selection: dict, mode: str = "multi") -> str:
    ensure()
    eid = "NS" + uuid.uuid4().hex[:10].upper()
    store.execute("INSERT INTO wf_nonstd_eval (eval_id, inquiry_id, item_index, opp_id, mode, status) VALUES (%s,%s,%s,%s,%s,'RUNNING')", (eid, inquiry_id, item_index, opp_id, mode))
    try:
        rep = MODES[mode](req, customer_id, selection)
        store.execute("UPDATE wf_nonstd_eval SET status = 'DONE', report = %s, tokens = %s, seconds = %s WHERE eval_id = %s",
                      (store.dumps({k: v for k, v in rep.items() if k not in ("evidence", "agents")}), rep.get("tokens", 0), rep.get("seconds", 0), eid))
        store.execute("UPDATE wf_nonstd_eval SET trace = %s WHERE eval_id = %s", (store.dumps({"evidence": rep.get("evidence", []), "agents": rep.get("agents", [])}), eid))
    except Exception as e:
        logger.exception("[nonstd] 评估失败")
        store.execute("UPDATE wf_nonstd_eval SET status = 'FAILED', report = %s WHERE eval_id = %s", (store.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}"}), eid))
    return eid
