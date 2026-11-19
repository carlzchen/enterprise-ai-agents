"""
意图路由：两层。
  第一层 规则：能用关键词确定的（问制度、换图表、开新话题）直接分流，不调用模型，又快又稳。
  第二层 模型：其余问题交给模型做意图识别、槽位抽取和工具选择（Function Calling），并用 Few-shot 例题固定输出方式。
多轮追问：把上一轮的调用参数给模型，模型标记 inherit；没提到的条件由代码从上一轮继承。
"""
import json
import re

from pydantic import ValidationError

from app.copilot import labels, llm, retriever, template_engine
from app.copilot.chart import CHART_CN
from app.copilot.permission import ROLE_CN, UserContext
from app.copilot.schemas import TOOL_MAP, TOOLS
from app.copilot.semantic.loader import semantic
from app.copilot.timeparse import today
from app.shared.runtime.logger import logger

TEMPLATE_TOPK = 8

_POLICY = re.compile(r"制度|规定|管理办法|流程是|怎么申请|如何申请|怎么请假|需要什么(证明|材料|手续)|要提交什么|"
                     r"算旷工|算迟到|年假有几天|年假几天|有几天年假|婚假|产假|试用期(多久|多长|几个月)|提前(多久|几天)|"
                     r"谁审批|谁来批|找谁批|报销.*(标准|时限|要求)|住宿标准|出差补贴|缴费比例|返岗红包|推荐.*奖励|发薪日|几号发工资|什么时候发工资")
_DATA = re.compile(r"多少|几个人|几人|统计|排名|趋势|对比|占比|总额|合计|哪个|哪些|哪条|最高|最低|最多|最少|前\d|前[一二三四五六七八九十]|列出|名单|\d+月|上月|上个月|本月|今年|去年|季度|为什么|如果")
_CHART = re.compile(r"^(?:请|帮我|能不能)?(?:把它|把这个|把图)?(?:画成|换成|改成|切换成|切到|用|显示成|做成)?(?:一个|个)?(" + "|".join(CHART_CN) + r")(?:显示|展示|看看|吧|呢)?[。？?！!]*$")
_WRITE = re.compile(r"删除|删掉|清空数据|新增一[条个名]|添加一[条个名]|录入一|修改.{0,8}(工资|薪资|数据|记录|单价|档案|考勤)|"
                    r"把.{0,12}(工资|薪资|单价|数据|记录|档案|考勤).{0,6}(改成|改为|改到|调到|调成|设为|设成)|"
                    r"(帮我|替我|直接)(批准|审批|通过|驳回)|审批通过|drop\s+table|delete\s+from|update\s+\w+\s+set|insert\s+into|truncate", re.I)
_HYPOTHETICAL = re.compile(r"如果|假如|假设|要是|倘若")
_RESET = re.compile(r"^(新话题|换个话题|重新开始|清空|重置对话)[。！!]*$")

_SYSTEM = """你是一家微特电机制造企业的"经营与人力数据分析 Copilot"的调度员。你的任务是根据用户的问题选择一个工具并填好参数，不要直接回答问题。

今天是 {today}。当前用户：{name}，角色 {role}，部门 {dept}。

选择规则：
1. 问制度、规定、流程、标准 → policy_question
2. 问数据 → 优先 run_template，从"可用模板"里选最合适的一个，分组和过滤只能用模板列出的项
3. 模板接不住的情况 → free_query：需要模板没有的分组或过滤；带数值门槛的条件（"超过 90 天""大于 30 小时"）；要按员工、按订单这类模板没有的粒度统计或找"最多的那个人"；要查明细
   group_by 的填法：问总数、合计、某个对象的值（"…是多少"）时留空，结果就是一个合计；问"各…""每个…""哪些…""分布""排名"时才填
   问"某一类占总数的比例、占比"：按那个维度分组（不要用过滤），占比列才有意义
   问题里点名了具体的客户、部门、产线、产品：一定要放进 filters
   问"最多、最高、最低的是哪个"：填上对应的分组，用 order_by 指定按哪个结果列排，并设 top_n
4. 问毛利、毛利率、利润为什么变化 → attribution
5. "如果……调整百分之几" → what_if
6. 问某个指标怎么算 → explain_metric
7. 看不出要查哪个指标或对象 → clarify，给出 2 到 4 个选项
8. period 只填用户原话里的时间短语，不要换算成日期；用户没说时间就留空
9. 过滤值尽量用下面"维度取值"里的编码
10. 问"我的工资、我的考勤、我的请假"这类本人数据 → free_query（系统会自动只返回本人的记录）
11. 追问（"那……呢""换成……""只看……"）：把 inherit 设为 true，只填这一句里提到的变化，没提到的条件系统会自动沿用上一轮

维度取值：
{dictionary}

可用模板（编号 名称｜说明｜分组｜过滤｜结果列）：
{templates}

示例：
问：上个月伺服线的加班率是多少？
调用：run_template {{"template_id": "T07", "filters": {{"产线": "L5"}}, "period": "上个月"}}
问：各部门现在分别有多少人？
调用：run_template {{"template_id": "T01", "group_by": ["部门"]}}
问：今年每个月的离职率
调用：run_template {{"template_id": "T03", "period": "今年"}}
问：三季度销售收入是多少？
调用：run_template {{"template_id": "T20", "period": "三季度"}}
问：三季度伺服系列的毛利率是多少？
调用：run_template {{"template_id": "T19", "filters": {{"产品系列": "SERVO"}}, "period": "三季度"}}
问：三季度外销收入占总收入的比例是多少？
调用：run_template {{"template_id": "T20", "group_by": ["内外销"], "period": "三季度"}}
问：佛山顺德宏达电器现在逾期多少钱？
调用：run_template {{"template_id": "T23", "filters": {{"客户": "佛山顺德宏达电器"}}}}
问：智能驱控车间现在有多少人？
调用：run_template {{"template_id": "T01", "filters": {{"部门": "智能驱控车间"}}}}
问：去年年底公司有多少在职员工？
调用：run_template {{"template_id": "T01", "period": "去年年底"}}
问：上个月报废数最多的是哪个型号？
调用：run_template {{"template_id": "T15", "group_by": ["产品"], "period": "上个月", "order_by": "报废数", "top_n": 1}}
问：现在有多少张发票逾期超过 90 天？
调用：free_query {{"question": "目前未收清且逾期超过 90 天的发票有多少张"}}
问：上个月迟到次数最多的人迟到了几次？
调用：free_query {{"question": "上个月按员工统计迟到次数，找出迟到次数最多的员工和次数", "period": "上个月"}}
问：三季度各产品系列的收入、成本和毛利率
调用：run_template {{"template_id": "T19", "group_by": ["产品系列"], "period": "三季度"}}
问：哪个班组上月出勤率最低？
调用：run_template {{"template_id": "T06", "group_by": ["班组"], "period": "上月", "top_n": 1, "ascending": true}}
问：（上一轮查的是伺服线上月加班率）那步进线呢？
调用：run_template {{"template_id": "T07", "filters": {{"产线": "L4"}}, "inherit": true}}
问：（上一轮按月份看毛利）换成按季度看
调用：run_template {{"template_id": "T19", "group_by": ["季度"], "inherit": true}}
问：三季度毛利率为什么比去年同期低？
调用：attribution {{"period": "三季度", "compare": "去年同期"}}
问：如果计件单价上调 5%，每月人工成本增加多少？
调用：what_if {{"scenario": "计件单价", "change_pct": 5}}
问：司龄不到三个月就离职的人里，各产线分别有多少？
调用：free_query {{"question": "司龄不到 3 个月就离职的员工，按产线统计人数"}}
问：我最近三个月的工资是多少？
调用：free_query {{"question": "我本人最近三个月每个月的应发工资、实发工资和个税", "period": "最近三个月"}}
问：加班率是怎么算的？
调用：explain_metric {{"metric": "加班率"}}
问：上个月的数据怎么样？
调用：clarify {{"question": "你想看上个月的哪类数据？", "options": ["出勤率和加班", "入离职人数", "销售收入和毛利", "产量和良率"]}}
"""


def rule_route(question: str, memory: dict) -> dict | None:
    """第一层：规则。命中返回 {tool, args, by: 'rule'}，否则 None"""
    q = question.strip()
    if _RESET.match(q):
        return {"tool": "reset", "args": {}, "by": "rule"}
    if _WRITE.search(q) and not _HYPOTHETICAL.search(q):
        return {"tool": "refuse_write", "args": {}, "by": "rule"}
    m = _CHART.match(q)
    if m and memory.get("last_result_id"):
        return {"tool": "switch_chart", "args": {"chart": CHART_CN[m.group(1)]}, "by": "rule"}
    if _POLICY.search(q) and not _DATA.search(q):
        return {"tool": "policy_question", "args": {"query": q}, "by": "rule"}
    return None


def _template_lines(ctx: UserContext, question: str, baseline: bool) -> str:
    """模板清单：只给这个角色能用的；正常模式下再用检索挑出最相关的几个，减少提示词长度"""
    usable = [t for t in semantic.templates.values() if not t.roles or ctx.role in t.roles]
    if not baseline and len(usable) > TEMPLATE_TOPK:
        keep = set(retriever.search(question, "template", top_k=TEMPLATE_TOPK))
        picked = [t for t in usable if t.id in keep]
        usable = picked if len(picked) >= 3 else usable
    lines = []
    for t in usable:
        dims = "、".join(t.dims) or "—"
        fixed = f"（固定带 {'、'.join(t.fixed)}）" if t.fixed else ""
        outs = "、".join(re.findall(r"AS `([^`]+)`", t.sql))
        lines.append(f"{t.id} {t.name}｜{t.desc}｜分组：{dims}{fixed}｜过滤：{'、'.join(t.filters) or '—'}｜结果列：{outs}")
    return "\n".join(lines)


def llm_route(question: str, ctx: UserContext, memory: dict, usage: llm.Usage, baseline: bool = False) -> dict:
    """第二层：模型做工具选择和参数抽取，Pydantic 校验不过时带着错误重试一次"""
    system = _SYSTEM.format(today=today().isoformat(), name=ctx.name or ctx.username, role=ROLE_CN.get(ctx.role, ctx.role),
                            dept=ctx.dept_name or "未知", dictionary=labels.dictionary_prompt() + "\n" + template_engine.dept_dictionary(),
                            templates=_template_lines(ctx, question, baseline))
    messages = [("system", system)]
    if memory.get("summary"):
        messages.append(("system", "更早的对话摘要：" + memory["summary"]))
    turns = memory.get("turns", [])
    for t in (turns if baseline else turns[-4:]):
        messages.append(("human" if t["role"] == "user" else "ai", t["text"][:300]))
    if memory.get("last_call"):
        messages.append(("system", "上一轮的调用：" + json.dumps(memory["last_call"], ensure_ascii=False)))
    messages.append(("human", question))
    error = ""
    for _ in range(2):
        prompt = messages + ([("system", f"上一次填的参数校验失败：{error}。请修正后重新调用。")] if error else [])
        msg = llm.invoke(prompt, usage, tools=TOOLS, tool_choice="required")
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            return {"tool": "chitchat", "args": {"reply": (msg.content or "").strip() or "我可以帮你查制度和经营、人力数据。"}, "by": "llm"}
        name, args = calls[0]["name"], calls[0]["args"]
        model = TOOL_MAP.get(name)
        if model is None:
            error = f"没有工具 {name}"
            continue
        try:
            return {"tool": name, "args": model(**args).model_dump(), "by": "llm"}
        except ValidationError as e:
            error = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())[:300]
            logger.warning(f"工具参数校验失败，重试：{error}")
    return {"tool": "clarify", "args": {"question": "我没有完全理解这个问题，能换个说法或者说得具体一点吗？", "options": []}, "by": "llm"}


def merge_followup(call: dict, last: dict | None) -> tuple[dict, list[str]]:
    """追问时从上一轮继承没提到的条件。返回（合并后的调用, 说明）"""
    notes: list[str] = []
    args = call["args"]
    if not last or not args.get("inherit"):
        return call, notes
    old = last.get("args", {})
    if not args.get("period") and old.get("period"):
        args["period"] = old["period"]
    if call["tool"] == "attribution" and not args.get("compare") and old.get("compare"):
        args["compare"] = old["compare"]
    if "filters" in args:
        merged = {**(old.get("filters") or {}), **(args.get("filters") or {})}
        merged = {k: v for k, v in merged.items() if v not in (None, "", [], "全部", "所有")}
        if call["tool"] == "run_template":
            tpl = semantic.templates.get(args.get("template_id"))
            if tpl:
                dropped = [k for k in merged if k not in tpl.filters and k not in (args.get("filters") or {})]
                for k in dropped:
                    merged.pop(k)
                if dropped:
                    notes.append(f"上一轮的{'、'.join(dropped)}条件不适用于「{tpl.name}」，已去掉")
        args["filters"] = merged
    if call["tool"] == "run_template" and last.get("tool") == "run_template":
        if not args.get("group_by") and old.get("group_by") and args.get("template_id") == old.get("template_id"):
            args["group_by"] = old["group_by"]
        if args.get("top_n") is None and old.get("top_n") and args.get("template_id") == old.get("template_id") \
                and args.get("group_by") == old.get("group_by"):
            args["top_n"], args["ascending"] = old["top_n"], old.get("ascending")
    return call, notes


def route_cache_key(ctx: UserContext, question: str, memory: dict) -> str:
    """路由缓存的键：角色 + 问题 + 上一轮调用 + 日期（"上个月"隔天可能就变了）"""
    import hashlib
    raw = "|".join([ctx.role, re.sub(r"\s+", "", question), json.dumps(memory.get("last_call"), ensure_ascii=False, sort_keys=True),
                    today().isoformat()])
    return "copilot:route:" + hashlib.md5(raw.encode("utf-8")).hexdigest()
