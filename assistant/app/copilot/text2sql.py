"""
SQL 生成（Text2SQL）：模板接不住的问题走这里。
先检索相关的指标口径和视图说明，按角色裁剪（只给有权访问的视图、去掉无权查看的列），
再让模型输出结构化的 SQL。校验不通过时把错误信息还给模型修正。
"""
import json

from pydantic import ValidationError

from app.copilot import labels, llm, retriever, template_engine
from app.copilot.permission import UserContext, access_level, visible_columns
from app.copilot.schemas import SqlDraft
from app.copilot.semantic.loader import ViewDef, semantic
from app.copilot.timeparse import Period, today

VIEW_TOPK = 4
METRIC_TOPK = 6

_PROMPT = """你是 MySQL 8 数据分析师。根据"可用的视图"和"指标口径"，为用户的问题写一条查询 SQL。

要求：
1. 只能写一条 SELECT（可以用 WITH），只能用下面列出的视图和字段，不要用别的表
2. 指标必须按"指标口径"里的公式计算，不要自己定义算法
3. 比率类结果保留小数形式（0.1234 表示 12.34%），用 ROUND(..., 4)，除数用 NULLIF(..., 0)，列别名里带"率"或"占比"
4. 每一列都起中文别名并用反引号括起来，如 AS `在职人数`
5. 编码列（产线、用工类型等）直接返回编码，不要翻译
6. 明细查询加 LIMIT 200；不要返回 id 这类内部编号
7. 问"最多、最高的是谁"时，把名称和数值都查出来，按数值倒序，LIMIT 1 到 10
8. 在职员工的条件是 status <> 'LEFT'；部门名称必须用"维度取值"里列出的部门全称
9. 今天是 {today}。{period}

维度取值：
{dictionary}

指标口径：
{metrics}

可用的视图：
{schema}

示例：
问题：司龄不到 3 个月就离职的员工，按产线统计人数
{{"sql": "SELECT e.line_code AS `产线`, COUNT(*) AS `离职人数` FROM v_employee e WHERE e.status = 'LEFT' AND e.tenure_months < 3 GROUP BY e.line_code ORDER BY `离职人数` DESC", "metrics": ["离职人数"], "explanation": "统计已离职且司龄不足 3 个月的员工，按产线分组"}}

只输出一个 JSON 对象，包含 sql、metrics、explanation 三个字段。

问题：{question}
{repair}"""


def schema_text(views: list[ViewDef], role: str) -> str:
    blocks = []
    for v in views:
        cols = []
        for c in visible_columns(v, role):
            if c == "id" or c.endswith("_id") and not v.columns.get(c):
                continue
            desc = v.columns.get(c, "")
            cols.append(f"  {c}" + (f"  -- {desc}" if desc else ""))
        until = semantic.data_until.get(v.name)
        scope = {"self": " 【系统已自动限定为当前用户本人的记录：查「我的…」时直接查，不要再按姓名或工号过滤】",
                 "dept": " 【系统已自动限定为当前用户所在部门及下级部门】"}.get(access_level(v, role), "")
        blocks.append(f"{v.name}（{v.title}）：{v.desc}" + (f" 数据截至 {until}。" if until else "") + scope + "\n" + "\n".join(cols))
    return "\n\n".join(blocks)


def pick_views(question: str, ctx: UserContext, baseline: bool = False) -> list[ViewDef]:
    """Schema 裁剪：只挑和问题最相关、且这个角色能访问的几个视图。baseline 模式不裁剪，全部给模型"""
    allowed = [v for v in semantic.views.values() if access_level(v, ctx.role)]
    if baseline:
        return allowed
    names = retriever.search(question, "view", top_k=VIEW_TOPK)
    for m in semantic.find_metrics(question)[:4]:                 # 问题里提到的指标所在的视图一定带上
        if m.view and m.view not in names:
            names.append(m.view)
    picked = [v for v in allowed if v.name in names]
    return picked or allowed[:VIEW_TOPK]


def pick_metrics(question: str, baseline: bool = False) -> str:
    if baseline:
        return "\n".join("- " + m.text() for m in semantic.metrics)
    names = list(dict.fromkeys([m.name for m in semantic.find_metrics(question)] + retriever.search(question, "metric", top_k=METRIC_TOPK)))
    ms = [semantic.metric(n) for n in names[:METRIC_TOPK + 2]]
    deps = [semantic.metric(d) for m in ms if m for d in m.depends_on]
    seen, lines = set(), []
    for m in [*ms, *deps]:
        if m and m.name not in seen:
            seen.add(m.name)
            lines.append("- " + m.text())
    return "\n".join(lines) or "（没有匹配到口径库里的指标，按字段含义计算）"


def generate(question: str, ctx: UserContext, period: Period | None, usage: llm.Usage, *, error: str = "",
             prev_sql: str = "", baseline: bool = False) -> tuple[SqlDraft, list[str]]:
    views = pick_views(question, ctx, baseline)
    period_hint = (f"用户说的时间对应 {period.start.isoformat()} 至 {period.end.isoformat()}（{period.label}），请直接使用这两个日期。"
                   if period else "用户没有说明时间。")
    repair = f"\n你上一次写的 SQL：\n{prev_sql}\n执行前校验没通过：{error}\n请修正后重新输出。" if error else ""
    prompt = _PROMPT.format(today=today().isoformat(), period=period_hint,
                            dictionary=labels.dictionary_prompt() + "\n" + template_engine.dept_dictionary(),
                            metrics=pick_metrics(question, baseline), schema=schema_text(views, ctx.role),
                            question=question, repair=repair)
    msg = llm.invoke(prompt, usage, json_mode=True)
    try:
        draft = SqlDraft(**json.loads(llm.strip_fence(msg.content)))
    except (ValidationError, json.JSONDecodeError) as e:
        raise ValueError(f"模型没有按要求输出 SQL：{str(e)[:150]}")
    return draft, [v.name for v in views]
