"""
Copilot 主流程：一次提问从头到尾怎么走，都在本文件最下面的 _pipeline 里，普通的 if / while，没有用流程框架。

  读记忆 → 意图路由 ─┬─ 制度问答 → 检索制度
                    ├─ 经营问数 → 模板填参 ─(接不住)→ 生成 SQL
                    │             → 校验 → 权限改写 →（不通过且可修正：回去重新生成，最多 2 次）
                    │             → 执行 → 选图表
                    ├─ 测算预警 → 归因 / 测算 → 选图表
                    └─ 口径说明 / 追问 / 换图表 / 拒绝
                  → 回答 → 收尾（记忆、会话历史、审计）

每一步是一个函数：读 state、返回要更新的字段。步骤内用 emit 推送进度、表格、图表和增量文本（SSE）。
"""
import time
import uuid
from datetime import datetime
from typing import TypedDict

from app.copilot import analysis, audit, chart, db, labels, llm, memory, policy, result as R, router, sqlguard, template_engine, text2sql
from app.platform import metrics, tracing
from app.platform import resilience as RS
from app.copilot.permission import UserContext
from app.copilot.semantic.loader import semantic
from app.copilot.timeparse import Period, parse_period, previous, same_period_last_year, today, _month, _shift_month
from app.infra.persistence.history_repository import history_repository
from app.shared.runtime.logger import logger
from app.shared.utils.sse_utils import SSEEvent, push_to_session

MAX_REPAIRS = 2
_SELF = __import__("re").compile(r"我|本人|自己|个人")
_ASK_ME = __import__("re").compile(r"(给|帮|替|告诉|发给|让)我")      # "查出来给我"里的"我"不是在问本人的数据
STEP_CN = {"load_memory": "读取对话记忆", "route_intent": "理解问题", "search_policy": "检索制度文档", "prepare_template": "匹配分析模板",
           "generate_sql": "生成查询", "validate_sql": "校验查询", "apply_permission": "权限处理", "execute_sql": "查询数据",
           "run_analysis": "分析计算", "build_chart": "生成图表", "answer": "生成回答", "finalize": "保存记录"}
INTENT_OF = {"policy_question": "制度问答", "run_template": "经营问数", "free_query": "经营问数", "attribution": "测算预警",
             "what_if": "测算预警", "explain_metric": "口径说明", "clarify": "追问澄清", "chitchat": "其他", "switch_chart": "换图表", "reset": "其他"}


class CopilotState(TypedDict, total=False):
    request_id: str
    session_id: str
    trace_id: str
    ctx: UserContext
    question: str
    stream: bool
    options: dict            # skip_answer 评测时跳过回答生成 / baseline 关闭全部优化 / no_cache
    usage: llm.Usage
    t0: float
    memory: dict
    call: dict               # {tool, args, by}
    intent: str
    sub_intent: str
    title: str
    period_label: str
    notes: list
    template_id: str
    sql: str
    sql_error: str
    repairs: int
    views: list
    guarded_sql: str
    scope_notes: list
    masked: list
    cache_hit: bool
    result: dict
    extra: list              # 附加表格 [{title, result}]
    chart: dict
    metric_defs: list
    sources: list
    options_list: list
    answer: str
    status: str              # OK / DENIED / FAILED / CLARIFY
    reason: str
    result_id: str
    audit_id: int


# ---------- 推送 ----------

def degrade(state: CopilotState, kind: str, note: str) -> None:
    """记一次降级：展示给用户，也进监控"""
    state.setdefault("degraded", [])
    if note not in state["degraded"]:
        state["degraded"].append(note)
    metrics.DEGRADED.labels("copilot", kind).inc()
    emit(state, "degraded", {"notes": state["degraded"]})


def emit(state: CopilotState, event: str, data: dict) -> None:
    if state.get("stream"):
        push_to_session(state["request_id"], event, data)


def _step(name: str):
    """步骤装饰器：推送进度、记录耗时；已经被拒绝或失败时跳过；步骤里抛出的异常统一转成 FAILED 状态"""
    def deco(fn):
        def wrapper(state: CopilotState) -> dict:
            if state.get("status") in ("DENIED", "FAILED") and name not in ("answer", "finalize"):
                return {}
            emit(state, SSEEvent.PROGRESS, {"step": name, "label": STEP_CN.get(name, name)})
            start, tr = time.time(), state.get("trace")
            if tr:
                tr.start(STEP_CN.get(name, name), {"question": state["question"]})
            try:
                out = fn(state) or {}
            except sqlguard.SqlRejected as e:
                out = {"status": "DENIED" if not e.repairable else "FAILED", "reason": e.message}
            except (RS.Unavailable, TimeoutError) as e:      # 依赖不可用：给用户一句能看懂的话，而不是报错堆栈
                logger.warning(f"[copilot] 节点 {name} 依赖不可用：{e}")
                degrade(state, "dependency", f"{getattr(e, 'name', '依赖服务')}暂时不可用")
                out = {"status": "FAILED", "reason": f"{getattr(e, 'name', '依赖服务')}暂时不可用（{getattr(e, 'reason', '超时')}），请稍后再试"}
            except Exception as e:
                logger.exception(f"[copilot] 节点 {name} 异常")
                out = {"status": "FAILED", "reason": f"{STEP_CN.get(name, name)}时出错：{str(e)[:200]}"}
            if tr:
                view = {k: out[k] for k in ("call", "intent", "sub_intent", "status", "reason", "template_id", "sql", "guarded_sql", "notes", "scope_notes", "repairs", "cache_hit") if k in out}
                if out.get("result"):
                    view["rows"] = len(out["result"].get("rows", []))
                if "answer" in out:
                    view["answer"] = str(out["answer"])[:1500]
                tr.end(STEP_CN.get(name, name), view, warning=out.get("reason") if out.get("status") in ("FAILED", "DENIED") else None)
            metrics.STAGE.labels("copilot", name).observe(time.time() - start)
            logger.info(f"[copilot] {name} {int((time.time() - start) * 1000)}ms")
            return out
        wrapper.__name__ = name
        return wrapper
    return deco


def _period(text: str | None) -> tuple[Period | None, str | None]:
    """解析时间短语。返回（期间, 解析失败时的提示）"""
    if not text:
        return None, None
    p = parse_period(text)
    return (p, None) if p else (None, f"没有看懂时间「{text}」")


def _metric_defs(names: list[str]) -> list[str]:
    out = []
    for n in names:
        m = semantic.metric(n)
        if m and m.text() not in out:
            out.append(m.text())
    return out


# ---------- 节点 ----------

@_step("load_memory")
def load_memory(state: CopilotState) -> dict:
    return {"memory": memory.load(state["session_id"])}


@_step("route_intent")
def route_intent(state: CopilotState) -> dict:
    q, mem, baseline = state["question"], state.get("memory", {}), state["options"].get("baseline")
    call = None if baseline else router.rule_route(q, mem)
    if call is None:
        # 同一个角色、同样的上下文下问同一句话，意图识别的结果是一样的：缓存 10 分钟，命中就不再调用模型
        key = router.route_cache_key(state["ctx"], q, mem)
        call = None if (baseline or state["options"].get("no_cache")) else memory.route_get(key)
        if call is None:
            try:
                call = router.llm_route(q, state["ctx"], mem, state["usage"], baseline=bool(baseline))
            except RS.Unavailable:                           # 大模型不可用：规则能接住的问题照常回答，接不住的如实告知
                degrade(state, "route", "大模型暂时不可用，只能回答固定句式的问题")
                call = {"tool": "chitchat", "by": "degraded", "args": {"reply": "大模型服务暂时不可用，这个问题需要它来理解。常见的问法现在仍然可以用，例如「上个月各产线出勤率」「今年各月销售额」「年假有几天」，或者过一会儿再试。"}}
            if not baseline and call["tool"] not in ("clarify", "chitchat"):
                memory.route_set(key, call)
        else:
            call = {**call, "by": "cache"}
    call, notes = router.merge_followup(call, mem.get("last_call"))
    intent = INTENT_OF.get(call["tool"], "其他")
    sub = ""
    if call["tool"] == "refuse_write":
        emit(state, "meta", {"intent": "拒绝", "tool": "refuse_write", "route_by": "rule"})
        return {"call": call, "intent": "拒绝", "status": "DENIED", "notes": [],
                "reason": "Copilot 只能查询和分析，不能新增、修改、删除数据，也不能代为审批；这类操作请在 ERP 对应的功能里办理"}
    if call["tool"] == "run_template":
        tpl = semantic.templates.get(call["args"].get("template_id"))
        sub = tpl.intent if tpl else ""
    elif call["tool"] == "attribution":
        sub = "归因"
    elif call["tool"] == "what_if":
        sub = "测算"
    emit(state, "meta", {"intent": intent, "sub_intent": sub, "tool": call["tool"], "route_by": call["by"]})
    return {"call": call, "intent": intent, "sub_intent": sub, "notes": notes}




@_step("search_policy")
def search_policy(state: CopilotState) -> dict:
    hits = policy.search(state["call"]["args"]["query"], top_k=5)
    sources = [{"index": i, "doc_key": h["doc_key"], "doc_title": h["doc_title"], "section": h["section"], "version": h["version"],
                "content": h["content"], "score": h["score"]} for i, h in enumerate(hits, start=1)]
    return {"sources": sources, "title": "制度问答"}


@_step("prepare_template")
def prepare_template(state: CopilotState) -> dict:
    args, notes = state["call"]["args"], list(state.get("notes", []))
    period, warn = _period(args.get("period"))
    if warn:
        return {"status": "CLARIFY", "answer": f"{warn}。可以说「上个月」「三季度」「2026 年 8 月」「最近半年」这样的时间。", "call": {**state["call"], "tool": "clarify"}}
    explicit = period is not None
    try:
        r = template_engine.render(args["template_id"], state["ctx"], group_by=args.get("group_by") or None,
                                   filters=args.get("filters"), period=period, top_n=args.get("top_n"), ascending=args.get("ascending"),
                                   order_by=args.get("order_by"))
        # 用户没说时间、默认的上个月又还没有数据（比如工资表还没核算）：自动退到有数据的最近一个月
        if not explicit and r.template.time == "range" and any("还没有数据" in n for n in r.notes):
            until = min((semantic.data_until.get(v, "9999")[:7] for v in semantic.views if v in r.template.sql and semantic.data_until.get(v)), default=None)
            if until and len(until) == 7:
                y, m = map(int, until.split("-"))
                r = template_engine.render(args["template_id"], state["ctx"], group_by=args.get("group_by") or None, filters=args.get("filters"),
                                           period=_month(y, m), top_n=args.get("top_n"), ascending=args.get("ascending"),
                                           order_by=args.get("order_by"))
                r.notes.insert(0, f"没有说明时间，按有数据的最近一个月（{until}）统计")
    except template_engine.TemplateDenied as e:
        return {"status": "DENIED", "reason": str(e)}
    except template_engine.TemplateMiss as e:                       # 模板接不住：改走 SQL 生成
        logger.info(f"[copilot] 模板未命中，改走 SQL 生成：{e}")
        return {"call": {"tool": "free_query", "args": {"question": state["question"], "period": args.get("period")},
                         "by": state["call"]["by"], "fallback_from": args.get("template_id")}, "template_id": ""}
    emit(state, "meta", {"template": r.template.id, "title": r.title, "group_by": r.group_by, "filters": r.filters})
    return {"sql": r.sql, "template_id": r.template.id, "title": r.title, "period_label": r.period.label if r.period else "",
            "notes": notes + r.notes, "metric_defs": _metric_defs(r.template.metrics),
            "call": {**state["call"], "args": {**args, "group_by": r.group_by, "filters": r.filters, "period": args.get("period") or (r.period.label if r.period and r.template.time == "range" else None)},
                     "chart": r.template.chart, "prefer": r.template.metrics}}




@_step("generate_sql")
def generate_sql(state: CopilotState) -> dict:
    args = state["call"]["args"]
    period, _ = _period(args.get("period"))
    question = args.get("question") or state["question"]
    draft, views = text2sql.generate(question, state["ctx"], period, state["usage"], error=state.get("sql_error", ""),
                                     prev_sql=state.get("sql", "") if state.get("sql_error") else "",
                                     baseline=bool(state["options"].get("baseline")))
    return {"sql": draft.sql, "title": draft.explanation or "查询结果", "period_label": period.label if period else "",
            "metric_defs": _metric_defs(draft.metrics), "views": views, "sql_error": "",
            "call": {**state["call"], "prefer": draft.metrics}}


@_step("validate_sql")
def validate_sql(state: CopilotState) -> dict:
    """语法解析、只读检查、表白名单、敏感列、危险函数"""
    try:
        tree = sqlguard.parse(state["sql"])
        return {"views": sqlguard.check(tree, state["ctx"]), "sql_error": ""}
    except sqlguard.SqlRejected as e:
        return _rejected(state, e)


@_step("apply_permission")
def apply_permission(state: CopilotState) -> dict:
    """行级过滤、列裁剪、打码、行数上限，然后试运行"""
    try:
        g = sqlguard.guard(state["sql"], state["ctx"])
        # 这个人对某张视图只有"仅本人"的权限，却在问部门或公司范围的统计：拿他一个人的数据去回答会误导，直接说明没有权限
        if g.self_only and not _SELF.search(_ASK_ME.sub("", state["question"])):
            return {"status": "DENIED", "sql_error": "", "guarded_sql": g.sql,
                    "reason": f"你只能查看本人的{'、'.join(g.self_only)}，不能查看部门或全公司范围的数据；可以问「我的…」来查本人的记录"}
        return {"guarded_sql": g.sql, "scope_notes": g.scope_notes, "masked": g.masked, "sql_error": ""}
    except sqlguard.SqlRejected as e:
        return _rejected(state, e)


def _rejected(state: CopilotState, e: sqlguard.SqlRejected) -> dict:
    can_repair = e.repairable and state["call"]["tool"] == "free_query" and state.get("repairs", 0) < MAX_REPAIRS
    if can_repair:
        logger.info(f"[copilot] SQL 校验未通过，交给模型修正：{e.message[:120]}")
        return {"sql_error": e.message, "repairs": state.get("repairs", 0) + 1}
    return {"status": "FAILED" if e.repairable else "DENIED", "reason": e.message, "sql_error": ""}



@_step("execute_sql")
def execute_sql(state: CopilotState) -> dict:
    sql, ctx = state["guarded_sql"], state["ctx"]
    use_cache = not state["options"].get("baseline") and not state["options"].get("no_cache")
    hit = memory.cache_get(ctx.scope_key, sql) if use_cache else None
    if hit:
        columns, rows = hit["columns"], hit["rows"]
    else:
        columns, rows = db.ro_query(sql)
        if use_cache:
            memory.cache_set(ctx.scope_key, sql, columns, rows)
    res = R.build(columns, rows)
    # 结果里实际出现了打码内容的列（视图里有打码规则但这次没查到那一列的，不算）
    masked = [c for c in columns if any(isinstance(r.get(c), str) and "****" in r[c] for r in rows[:50])]
    emit(state, "table", {"title": state.get("title"), "result": res})
    emit(state, "sql", {"sql": sql, "metric_defs": state.get("metric_defs", []), "scope_notes": state.get("scope_notes", [])})
    return {"result": res, "cache_hit": bool(hit), "masked": masked}


@_step("run_analysis")
def run_analysis(state: CopilotState) -> dict:
    call, ctx = state["call"], state["ctx"]
    args, notes = call["args"], list(state.get("notes", []))
    period, warn = _period(args.get("period"))
    if warn:
        return {"status": "CLARIFY", "answer": f"{warn}。可以说「上个月」「三季度」「2026 年 8 月」这样的时间。"}
    now = today()
    where = ""
    if args.get("filters"):
        alias = "s" if call["tool"] == "what_if" and args["scenario"] in ("计件单价", "基本工资") else "f"
        cols = ({"部门": "s.dept_name", "产线": "s.line_code"} if alias == "s" else
                {"事业部": "f.business_unit", "产品系列": "f.series", "客户": "f.customer_name", "行业": "f.industry", "区域": "f.region", "产品": "f.product_code"})
        for k, v in args["filters"].items():
            if k not in cols:
                notes.append(f"这类分析不支持按{k}过滤，已忽略")
                continue
            values = template_engine._filter_values(k, v)
            where += f" AND {cols[k]} IN ({', '.join(template_engine._lit(x) for x in values)})"
    if call["tool"] == "attribution":
        if period is None:
            period = _month(*_shift_month(now.year, now.month, -1))
            notes.append(f"没有说明时间，按上个月（{period.label}）分析")
        cmp_text = args.get("compare") or ""
        if any(k in cmp_text for k in ("同比", "去年同期", "上年同期")):
            base = same_period_last_year(period)
        elif not cmp_text or any(k in cmp_text for k in ("环比", "上期", "上一期")):
            base = previous(period)
        else:
            base = parse_period(cmp_text) or previous(period)
        a = analysis.attribution(ctx, period, base, args.get("by", "产品系列"), where)
        s = a["summary"]
        summary = R.build(list(s), [s])
        factors = R.build(["因素", "影响金额", "占变动比例"], a["factors"])
        groups = R.build(list(a["groups"][0]) if a["groups"] else [a["by"]], a["groups"])
        extra = [{"title": f"按{a['by']}拆分的影响（从拖累最大到贡献最大）", "result": groups},
                 {"title": "两期对比", "result": summary}]
        clue = analysis.material_clue(ctx, period, base)
        if clue:
            extra.append({"title": "参考：两期原材料采购价差", "result": R.build(list(clue[0]), clue)})
        title = f"毛利变动归因（{period.label} 对比 {base.label}）"
        emit(state, "table", {"title": title, "result": factors})
        for e in extra:
            emit(state, "table", e)
        emit(state, "sql", {"sql": a["sql"], "metric_defs": _metric_defs(["毛利", "毛利率"]), "scope_notes": a["scope_notes"]})
        return {"result": factors, "extra": extra, "title": title, "period_label": period.label, "notes": notes, "guarded_sql": a["sql"],
                "scope_notes": a["scope_notes"], "metric_defs": _metric_defs(["毛利", "毛利率"]) + [
                    "归因方法：把两期毛利差拆成 价格（单价变化 × 本期销量）、成本（单位成本变化 × 本期销量，取负）、销量（总销量变化 × 上期平均单位毛利）、"
                    "结构（各产品销量占比变化 × 本期总销量 × 上期单位毛利），四项相加等于毛利变动"],
                "call": {**call, "chart": "bar", "prefer": ["影响金额"]}}
    # what_if
    if period is None:
        base_view = "v_payslip" if args["scenario"] in ("计件单价", "基本工资") else "v_sales_fact"
        until = semantic.data_until.get(base_view, "")[:7]
        y, m = (map(int, until.split("-")) if len(until) == 7 else _shift_month(now.year, now.month, -1))
        period = _month(y, m)
        notes.append(f"没有说明时间，以有数据的最近一个月（{period.label}）为基础测算")
    w = analysis.what_if(ctx, args["scenario"], args["change_pct"], period, where)
    res = R.build(w["columns"], w["rows"])
    delta_col = next((c["name"] for c in res["columns"] if "变动" in c["name"]), None)
    if delta_col and res["rows"]:
        total = {c["name"]: ("合计" if c["type"] == "text" else (sum(r[c["name"]] or 0 for r in res["rows"]) if "率" not in c["name"] else None))
                 for c in res["columns"]}
        res["rows"].append(total)
    title = f"测算：{args['scenario']}调整 {args['change_pct']:+g}%（以 {period.label} 为基础）"
    emit(state, "table", {"title": title, "result": res})
    emit(state, "sql", {"sql": w["sql"], "metric_defs": w["assumptions"], "scope_notes": w["scope_notes"]})
    return {"result": res, "title": title, "period_label": period.label, "notes": notes + ["测算假设：" + "；".join(w["assumptions"])],
            "guarded_sql": w["sql"], "scope_notes": w["scope_notes"], "metric_defs": w["assumptions"],
            "call": {**call, "chart": "bar", "prefer": [delta_col or ""]}}


@_step("build_chart")
def build_chart(state: CopilotState) -> dict:
    res = state.get("result")
    if not res or not res["rows"]:
        return {}
    call = state["call"]
    data = res
    if call["tool"] in ("what_if", "attribution"):                   # 合计行不画进图里
        data = {**res, "rows": [r for r in res["rows"] if not str(next(iter(r.values()))).startswith("合计")]}
        if call["tool"] == "attribution":
            data = {"columns": data["columns"][:2], "rows": data["rows"]}
    c = chart.build(data, hint=call.get("chart", "bar"), prefer=call.get("prefer"), title=state.get("title", ""))
    if c:
        emit(state, "chart", c)
    return {"chart": c}


_DATA_PROMPT = """你是企业数据分析助手。根据下面的查询结果回答用户的问题。

要求：
1. 第一句直接回答问题，给出关键数字
2. 再用一到三句点出最值得注意的地方（最高、最低、明显的变化或异常）
3. 只能使用表格里的数字，不要编造，也不要推算表格里没有的数
4. 比率写成百分数；金额超过 10 万写成「万元」
5. 不要复述整张表，不要写「根据查询结果」这类开场白，不要给和数据无关的建议

用户的问题：{question}
结果标题：{title}
统计口径：{defs}
补充说明：{notes}

{tables}"""

_POLICY_PROMPT = """你是公司的制度问答助手。只根据下面的制度原文回答用户的问题。

要求：
1. 直接回答，语言简洁
2. 每个结论后面用 [编号] 标出依据的原文，如 [1]
3. 原文里没有提到的内容，明确说「现有制度里没有找到相关规定」，不要推测，也不要用常识补充
4. 涉及金额、天数、比例时照原文写

制度原文：
{context}

用户的问题：{question}"""


def _delta(state: CopilotState, text: str) -> None:
    if state.get("stream"):
        if state["usage"].first_token_ms is None:
            state["usage"].first_token_ms = int((time.time() - state["t0"]) * 1000)
        push_to_session(state["request_id"], SSEEvent.DELTA, {"delta": text})


@_step("answer")
def answer(state: CopilotState) -> dict:
    call = state.get("call") or {"tool": "", "args": {}}
    tool, args, status = call["tool"], call.get("args", {}), state.get("status")
    out: dict = {}
    use_cache = not state["options"].get("baseline") and not state["options"].get("no_cache")

    def say(text: str) -> str:
        _delta(state, text)
        return text

    if status == "DENIED":
        text = say(f"这个问题我不能回答：{state.get('reason')}。")
    elif status == "FAILED":
        text = say(f"没能完成这次查询：{state.get('reason')}。可以换个说法，或者把指标、对象、时间说得更具体一些。")
    elif status == "CLARIFY":
        text = say(state.get("answer", "请再说得具体一些。"))
    elif tool == "reset":
        memory.clear(state["session_id"])
        out["memory"] = {}
        text = say("好的，已经清空之前的条件，我们重新开始。")
    elif tool == "switch_chart":
        payload = memory.result_get(state.get("memory", {}).get("last_result_id", "")) or {}
        if not payload.get("result"):
            text = say("上一轮的结果已经过期了，请重新提问。")
        else:
            c = chart.switch(None, payload["result"], args["chart"], payload.get("title", ""))
            out.update(result=payload["result"], title=payload.get("title", ""), chart=c)
            emit(state, "table", {"title": payload.get("title"), "result": payload["result"]})
            if c:
                emit(state, "chart", c)
            text = say("已经换成表格显示。" if args["chart"] == "table" else ("已经换了图表类型，数据没有变。" if c else "这份结果不适合画成图，仍然用表格显示。"))
    elif tool in ("clarify", "chitchat"):
        out["status"] = "CLARIFY" if tool == "clarify" else "OK"
        out["options_list"] = args.get("options", [])
        text = say(args.get("question") or args.get("reply") or "请再说得具体一些。")
    elif tool == "explain_metric":
        text = say(_explain(args.get("metric", "")))
    elif tool == "policy_question":
        sources = state.get("sources", [])
        if not sources:
            text = say("现有制度里没有找到相关规定。")
        elif state["options"].get("skip_answer"):
            text = ""
        else:
            context = "\n\n".join(f"[{s['index']}]（{s['doc_title']} · {s['section']}，版本 {s['version']}）\n{s['content']}" for s in sources)
            prompt = _POLICY_PROMPT.format(context=context, question=args.get("query") or state["question"])
            key = memory.answer_key("policy", "", args.get("query") or state["question"], "|".join(f"{s['doc_key']}{s['version']}{s['section']}" for s in sources))
            cached = memory.answer_get(key) if use_cache else None
            if cached:
                text = say(cached)
            else:
                try:
                    text = llm.stream(prompt, lambda d: _delta(state, d), state["usage"]) if state.get("stream") else _invoke(prompt, state)
                except RS.Unavailable:                       # 制度问答的降级：不整理了，直接给原文
                    degrade(state, "extractive", "大模型暂时不可用，下面是检索到的制度原文，没有经过整理")
                    text, use_cache = say("\n\n".join(f"**{s['doc_title']} · {s['section']}** [{s['index']}]\n{s['content'][:300]}" for s in sources[:3])), False
                if use_cache and text:
                    memory.answer_set(key, text)
    else:                                                             # 数据类回答
        res = state.get("result") or {"columns": [], "rows": []}
        notes = [*state.get("notes", []), *state.get("scope_notes", [])]
        if state.get("masked"):
            notes.append("以下字段已打码：" + "、".join(state["masked"]))
        prefix = "".join(f"> {n}\n" for n in notes) + ("\n" if notes else "")
        if not res["rows"]:
            text = say(prefix + "按这些条件没有查到数据。可以换个时间范围或放宽过滤条件再试。")
        elif state["options"].get("skip_answer"):
            text = ""
        else:
            single = None if state["options"].get("baseline") or tool != "run_template" else R.one_line(state.get("title", ""), res)
            if single:                                                # 只有一行：不调用模型
                text = say(prefix + single)
            else:
                key = memory.answer_key("data", state["ctx"].scope_key, state["question"], state.get("guarded_sql", ""))
                cached = memory.answer_get(key) if use_cache else None
                if cached:
                    text = say(prefix + cached)
                else:
                    # 第一句由代码给出（最高、最低或最新一期），查询结束就推给用户；模型只负责补充
                    lead = None if state["options"].get("baseline") else R.headline(res, call.get("prefer"))
                    say(prefix + (lead + "\n\n" if lead else ""))
                    tables = f"查询结果「{state.get('title', '')}」：\n{R.to_markdown(res)}"
                    for e in state.get("extra", [])[:3]:
                        tables += f"\n\n「{e['title']}」：\n{R.to_markdown(e['result'], max_rows=12)}"
                    prompt = _DATA_PROMPT.format(question=state["question"], title=state.get("title", ""),
                                                 defs="；".join(state.get("metric_defs", [])) or "无",
                                                 notes="；".join(notes) or "无", tables=tables)
                    if lead:
                        prompt += f"\n\n已经告诉用户的第一句话：{lead}\n不要重复这句话里的内容，直接补充一到两句其他值得注意的地方；如果没有别的值得说的，只回答「以上就是主要情况。」"
                    try:
                        body = llm.stream(prompt, lambda d: _delta(state, d), state["usage"]) if state.get("stream") else _invoke(prompt, state)
                    except RS.Unavailable:                   # 数据问答的降级：数是查出来的，照常给表格，只是少了文字解读
                        degrade(state, "no_narrative", "大模型暂时不可用，这次只给数据，没有文字解读")
                        body, use_cache = say("" if lead else "数据见下表。"), False
                    body = (lead + "\n\n" if lead else "") + body
                    if use_cache and body:
                        memory.answer_set(key, body)
                    text = prefix + body
    out["answer"] = text
    if not out.get("status") and not status:
        out["status"] = "OK"
    return out


def _invoke(prompt: str, state: CopilotState) -> str:
    return (llm.invoke(prompt, state["usage"]).content or "").strip()


def _explain(name: str) -> str:
    m = semantic.metric(name) or next((semantic.metric(n) for n in __import__("app.copilot.retriever", fromlist=["search"]).search(name, "metric", top_k=1)), None)
    if not m:
        return f"口径库里没有找到「{name}」。可以在「指标口径」页查看全部指标。"
    lines = [f"**{m.name}**（{'原子指标' if m.type == 'atomic' else '派生指标'}，单位：{m.unit or '—'}）", f"- 公式：{m.formula}"]
    if m.depends_on:
        lines.append(f"- 依赖的指标：{'、'.join(m.depends_on)}")
    if m.view:
        lines.append(f"- 数据来源：{semantic.views[m.view].title}（{m.view}）")
    if m.note:
        lines.append(f"- 说明：{m.note}")
    if m.dims:
        lines.append(f"- 可以按这些维度看：{'、'.join(m.dims)}")
    if m.sensitivity != "normal":
        lines.append("- 权限：" + ("薪酬类指标，只对人力资源、财务和管理层开放" if m.sensitivity == "salary" else "成本与毛利类指标，只对财务和管理层开放"))
    return "\n".join(lines)


@_step("finalize")
def finalize(state: CopilotState) -> dict:
    """暂存结果、更新记忆、写会话历史、写审计"""
    ctx, call = state["ctx"], state.get("call") or {"tool": "", "args": {}, "by": ""}
    status = state.get("status") or "OK"
    elapsed = int((time.time() - state["t0"]) * 1000)
    result_id = None
    if state.get("result") and state["result"]["rows"] and status == "OK":
        result_id = uuid.uuid4().hex[:16]
        memory.result_put(result_id, {"username": ctx.username, "role": ctx.role, "title": state.get("title", ""), "result": state["result"],
                                      "extra": state.get("extra", []), "sql": state.get("guarded_sql", ""), "masked": state.get("masked", []),
                                      "scope_notes": state.get("scope_notes", []), "question": state["question"]})
    remember = call if call["tool"] in ("run_template", "free_query", "attribution", "what_if") and status == "OK" else None
    if call["tool"] != "reset" and not state["options"].get("no_memory"):
        memory.append_turn(state["session_id"], state.get("memory", {}), state["question"], state.get("answer", ""), remember,
                           result_id or (state.get("memory", {}).get("last_result_id") if call["tool"] == "switch_chart" else None))
    usage = state["usage"]
    audit_id = None
    if not state["options"].get("no_audit"):
        audit_id = audit.log(trace_id=state.get("trace_id"), session_id=state["session_id"], username=ctx.username, role=ctx.role,
                             question=state["question"], intent=state.get("intent"), tool=call["tool"], route_by=call.get("by"),
                             template_id=state.get("template_id") or None, sql_text=state.get("guarded_sql") or state.get("sql"),
                             row_count=len(state["result"]["rows"]) if state.get("result") else None, status=status,
                             reason=state.get("reason"), scope_note="；".join(state.get("scope_notes", [])) or None,
                             cache_hit=1 if state.get("cache_hit") else 0, repairs=state.get("repairs", 0), elapsed_ms=elapsed,
                             first_token_ms=usage.first_token_ms, tokens_in=usage.input, tokens_out=usage.output,
                             llm_calls=usage.calls, answer=(state.get("answer") or "")[:4000])
    return {"result_id": result_id or "", "audit_id": audit_id or 0, "status": status}


# ---------- 主流程 ----------

_STOP = ("DENIED", "FAILED", "CLARIFY")


def _pipeline(state: CopilotState) -> CopilotState:
    def step(fn) -> None:
        state.update(fn(state))

    def stopped() -> bool:
        return state.get("status") in _STOP

    step(load_memory)
    step(route_intent)
    tool = state["call"]["tool"]
    if not stopped():
        if tool == "policy_question":
            step(search_policy)
        elif tool in ("attribution", "what_if"):
            step(run_analysis)
            step(build_chart)
        elif tool in ("run_template", "free_query"):
            if tool == "run_template":
                step(prepare_template)                 # 模板接不住时，会把 call 改成 free_query
            while not stopped():
                if state["call"]["tool"] == "free_query" and (not state.get("sql") or state.get("sql_error")):
                    step(generate_sql)                 # 第一次生成，或带着上次的错误信息重写
                    if stopped():
                        break
                step(validate_sql)
                if not state.get("sql_error") and not stopped():
                    step(apply_permission)
                if not state.get("sql_error"):         # 通过了，或者已经被拒绝
                    break
            if not stopped():
                step(execute_sql)
                step(build_chart)
        # 其余情况（口径说明、追问、闲聊、换图表、新话题）不查数据，直接回答
    step(answer)
    step(finalize)
    return state


def payload_of(state: dict) -> dict:
    """返回给前端的完整结果"""
    call = state.get("call") or {}
    usage: llm.Usage = state["usage"]
    return {
        "session_id": state["session_id"], "status": state.get("status", "OK"), "reason": state.get("reason"),
        "intent": state.get("intent"), "sub_intent": state.get("sub_intent"), "tool": call.get("tool"), "route_by": call.get("by"),
        "args": call.get("args"), "template_id": state.get("template_id") or None, "title": state.get("title"),
        "period": state.get("period_label"), "answer": state.get("answer", ""), "result": state.get("result"),
        "extra": state.get("extra", []), "chart": state.get("chart"), "sql": state.get("guarded_sql") or None,
        "metric_defs": state.get("metric_defs", []), "notes": state.get("notes", []), "scope_notes": state.get("scope_notes", []),
        "masked": state.get("masked", []), "sources": [{k: v for k, v in s.items() if k != "content"} | {"snippet": s["content"][:160]}
                                                       for s in state.get("sources", [])],
        "options": state.get("options_list", []), "result_id": state.get("result_id") or None, "audit_id": state.get("audit_id") or None,
        "cache_hit": bool(state.get("cache_hit")), "repairs": state.get("repairs", 0), "usage": usage.as_dict(),
        "degraded": [*state.get("degraded", []), *(["主模型不可用，这次回答由备用模型生成"] if usage.fallback else [])],
        "elapsed_ms": int((time.time() - state["t0"]) * 1000), "first_token_ms": usage.first_token_ms,
    }


def _history():
    col = history_repository.col.database["copilot_message"]
    col.create_index([("session_id", 1), ("ts", -1)])
    col.create_index([("username", 1), ("ts", -1)])
    return col


def run(question: str, ctx: UserContext, session_id: str | None = None, *, stream: bool = False, request_id: str | None = None,
        trace_id: str = "", options: dict | None = None) -> dict:
    """执行一次提问，返回完整结果。stream=True 时过程中的事件推送到 request_id 对应的 SSE 队列"""
    state: CopilotState = {
        "request_id": request_id or uuid.uuid4().hex, "session_id": session_id or uuid.uuid4().hex, "trace_id": trace_id, "ctx": ctx,
        "question": question.strip(), "stream": stream, "options": options or {}, "usage": llm.Usage(), "t0": time.time(),
        "notes": [], "repairs": 0,
    }
    if not (options or {}).get("no_history"):                # 评测不记链路
        state["trace_id"] = state["trace_id"] or state["request_id"]
        state["trace"] = tracing.Trace("copilot", "经营与人力问数", state["trace_id"], ctx.username, state["session_id"], state["question"], {"role": ctx.role, "dept": ctx.dept_name})
    final = _pipeline(state)
    payload = payload_of(final)
    if state.get("trace"):
        state["trace"].finish(payload["answer"], [payload.get("intent"), payload["status"], payload.get("route_by"), *(["降级"] if payload["degraded"] else []), *(["缓存"] if payload["cache_hit"] else [])],
                              {"tool": payload.get("tool"), "template": payload.get("template_id"), "sql": payload.get("sql"), "usage": payload["usage"], "degraded": payload["degraded"],
                               "audit_id": payload.get("audit_id")})
        payload["trace_url"] = tracing.url(state["trace_id"]) if state["trace"].on else None
    metrics.REQUESTS.labels("copilot", payload.get("intent") or "-", payload["status"]).inc()
    metrics.LATENCY.labels("copilot", payload.get("intent") or "-").observe(payload["elapsed_ms"] / 1000)
    if payload["first_token_ms"]:
        metrics.FIRST_TOKEN.labels("copilot").observe(payload["first_token_ms"] / 1000)
    metrics.TOKENS.labels("copilot", "input").inc(state["usage"].input)
    metrics.TOKENS.labels("copilot", "output").inc(state["usage"].output)
    if payload["cache_hit"]:
        metrics.CACHE.labels("copilot", "result", "hit").inc()
    if not (options or {}).get("no_history"):
        try:
            now = datetime.now().timestamp()
            _history().insert_many([
                {"session_id": state["session_id"], "username": ctx.username, "role": "user", "text": state["question"], "ts": now},
                {"session_id": state["session_id"], "username": ctx.username, "role": "assistant", "text": payload["answer"],
                 "payload": {k: payload[k] for k in ("status", "intent", "title", "period", "result", "extra", "chart", "sql", "metric_defs",
                                                     "notes", "scope_notes", "sources", "options", "result_id", "audit_id", "template_id", "degraded")},
                 "ts": now + 0.001}])
        except Exception:
            logger.exception("写会话历史失败")
    return payload


def list_sessions(username: str, limit: int = 30) -> list[dict]:
    pipeline = [{"$match": {"username": username, "role": "user"}}, {"$sort": {"ts": -1}},
                {"$group": {"_id": "$session_id", "last_ts": {"$first": "$ts"}, "title": {"$last": "$text"}}},
                {"$sort": {"last_ts": -1}}, {"$limit": limit}]
    return [{"session_id": r["_id"], "last_ts": r["last_ts"], "title": r["title"][:40]} for r in _history().aggregate(pipeline)]


def session_owner(session_id: str) -> str | None:
    doc = _history().find_one({"session_id": session_id}, {"username": 1})
    return doc.get("username") if doc else None


def history(session_id: str, limit: int = 100) -> list[dict]:
    rows = list(_history().find({"session_id": session_id}).sort("ts", 1).limit(limit))
    return [{k: v for k, v in r.items() if k != "_id"} for r in rows]


def clear_session(session_id: str) -> int:
    memory.clear(session_id)
    return _history().delete_many({"session_id": session_id}).deleted_count
