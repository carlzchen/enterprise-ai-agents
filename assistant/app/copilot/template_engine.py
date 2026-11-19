"""
模板引擎：模型只给出"用哪个模板、按什么分组、过滤什么、什么时间"，这里把它变成一条完整的 SQL。
分组列只能从模板声明的 dims 里选；过滤值先归一成数据库里的编码，再用 sqlglot 转成字面量，不做字符串拼接。
"""
import re
from dataclasses import dataclass, field
from datetime import date

from sqlglot import exp

from app.copilot import db, labels
from app.copilot.permission import ROLE_CN, UserContext
from app.copilot.semantic.loader import Template, semantic
from app.copilot.timeparse import Period, _month, _shift_month, today

TIME_DIMS = ("月份", "季度")
_ENUM_DIMS = {"产线", "产品系列", "用工类型", "计薪方式", "性别", "请假类型", "成本类型", "收款方式", "物料类别", "内外销", "事业部", "班组"}


class TemplateMiss(Exception):
    """模板接不住这个问题（不支持的过滤条件等），调用方应改走 SQL 生成"""


class TemplateDenied(Exception):
    """角色无权使用这个模板"""


@dataclass
class Rendered:
    sql: str
    template: Template
    title: str
    period: Period | None
    group_by: list[str]
    filters: dict
    notes: list[str] = field(default_factory=list)


def _lit(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return exp.Literal.string(str(v)).sql(dialect="mysql")


def _dept_names(value: str) -> list[str]:
    """部门过滤：'生产部' 要把下级车间一起算上；'研发' 这种简称做模糊匹配"""
    depts = db.meta_query("SELECT id, name, parent_id FROM org_dept")
    roots = [d for d in depts if d["name"] == value] or [d for d in depts if value.rstrip("部门") and value.rstrip("部门") in d["name"]]
    if not roots:
        return []
    ids = {d["id"] for d in roots}
    changed = True
    while changed:
        changed = False
        for d in depts:
            if d["parent_id"] in ids and d["id"] not in ids:
                ids.add(d["id"])
                changed = True
    return [d["name"] for d in depts if d["id"] in ids]


_dept_cache: dict = {}


def dept_dictionary() -> str:
    """给模型看的部门清单（含上下级），让它知道"智能驱控车间"是部门而不是产线或事业部。缓存 10 分钟"""
    import time
    if _dept_cache.get("t", 0) > time.time() - 600:
        return _dept_cache["text"]
    depts = db.meta_query("SELECT id, name, parent_id FROM org_dept ORDER BY sort_no, id")
    kids: dict = {}
    for d in depts:
        kids.setdefault(d["parent_id"], []).append(d)
    root = next((d for d in depts if d["parent_id"] is None), None)
    parts = []
    for d in kids.get(root["id"] if root else None, []):
        sub = "、".join(k["name"] for k in kids.get(d["id"], []))
        parts.append(d["name"] + (f"（下辖 {sub}）" if sub else ""))
    text = "部门：" + (root["name"] + "、" if root else "") + "、".join(parts) + "。按上级部门过滤时会自动包含下级部门"
    _dept_cache.update(t=time.time(), text=text)
    return text


def _fuzzy(table: str, col: str, value: str) -> list[str]:
    rows = db.meta_query(f"SELECT DISTINCT `{col}` AS v FROM `{table}` WHERE `{col}` LIKE %s LIMIT 20", (f"%{value}%",))
    return [r["v"] for r in rows]


def _filter_values(dim: str, value) -> list:
    """把一个过滤值归一成数据库里的取值列表；认不出来抛 TemplateMiss"""
    out = []
    for v in (value if isinstance(value, (list, tuple)) else [value]):
        if v is None or v == "":
            continue
        if dim == "部门":
            names = _dept_names(str(v))
            if not names:
                raise TemplateMiss(f"没有找到部门「{v}」")
            out.extend(names)
        elif dim == "客户":
            names = _fuzzy("md_customer", "name", str(v))
            if not names:
                raise TemplateMiss(f"没有找到客户「{v}」")
            out.extend(names)
        elif dim == "产品":
            codes = _fuzzy("md_product", "code", str(v)) or _fuzzy("md_product", "name", str(v))
            if not codes:
                raise TemplateMiss(f"没有找到产品「{v}」")
            out.extend(codes)
        elif dim in _ENUM_DIMS:
            n = labels.normalize(dim, v)
            if n is None:
                raise TemplateMiss(f"不认识的{dim}「{v}」")
            out.append(n)
        else:
            out.append(v)
    return list(dict.fromkeys(out))


def _clamp(tpl: Template, period: Period) -> tuple[Period, str | None]:
    """所选时间超出了数据范围（月份还没过完、工资还没核算）：截到数据截止日，并告诉用户"""
    until = None
    for name in re.findall(r"\b(v_\w+)\b", tpl.sql):
        u = semantic.data_until.get(name, "")[:10]
        if not u:
            continue
        d = _month(int(u[:4]), int(u[5:7])).end if len(u) == 7 else date.fromisoformat(u)
        until = d if until is None or d < until else until
    if until is None or period.end <= until:
        return period, None
    title = "、".join(dict.fromkeys(semantic.views[n].title for n in re.findall(r"\b(v_\w+)\b", tpl.sql) if n in semantic.views))
    if period.start > until:
        return period, f"{title}的数据只到 {until.isoformat()}，所选时间还没有数据，可以改问 {until.strftime('%Y-%m')}"
    gap = (period.end - until).days
    clamped = Period(period.start, until, period.label, period.grain)
    return clamped, (f"{title}的数据只到 {until.isoformat()}，之后的日期没有统计" if gap > 5 else None)


def _default_period(tpl: Template, now: date) -> Period:
    if tpl.time == "asof":
        return Period(now, now, now.isoformat(), "day")
    return _month(*_shift_month(now.year, now.month, -1))


def render(template_id: str, ctx: UserContext, *, group_by: list[str] | None = None, filters: dict | None = None,
           period: Period | None = None, top_n: int | None = None, ascending: bool | None = None,
           order_by: str | None = None) -> Rendered:
    tpl = semantic.templates.get(template_id)
    if tpl is None:
        raise TemplateMiss(f"没有模板 {template_id}")
    if tpl.roles and ctx.role not in tpl.roles:
        raise TemplateDenied(f"「{tpl.name}」只对{'、'.join(ROLE_CN.get(r, r) for r in tpl.roles)}开放，你的角色是{ROLE_CN.get(ctx.role, ctx.role)}")
    notes: list[str] = []
    now = today()

    # 时间
    if tpl.time == "none":
        period = None
    elif period is None:
        period = _default_period(tpl, now)
        notes.append(f"没有说明时间，按{'今天' if tpl.time == 'asof' else '上个月'}（{period.label}）统计")
    if period and tpl.time == "asof" and period.end > now:
        period = Period(period.start, now, period.label, period.grain)
    if period and tpl.time == "range":
        period, note = _clamp(tpl, period)
        if note:
            notes.append(note)

    # 分组
    wanted = list(dict.fromkeys([*tpl.fixed, *(group_by or [])]))
    unknown = [d for d in wanted if d not in tpl.dims]
    if unknown and tpl.kind != "list":
        raise TemplateMiss(f"模板「{tpl.name}」不能按 {'、'.join(unknown)} 分组")
    dims = ([] if tpl.kind == "list" else [d for d in wanted if d in tpl.dims])[:3]      # 没要求分组就给合计，不替用户做主

    # 过滤
    clauses, used_filters = [], {}
    for name, value in (filters or {}).items():
        if value in (None, "", []):
            continue
        if name not in tpl.filters:
            raise TemplateMiss(f"模板「{tpl.name}」不支持按{name}过滤")
        values = _filter_values(name, value)
        if not values:
            continue
        col = tpl.filters[name]
        clauses.append(f" AND {col} = {_lit(values[0])}" if len(values) == 1 else f" AND {col} IN ({', '.join(_lit(v) for v in values)})")
        used_filters[name] = values if len(values) > 1 else values[0]

    # 排序与条数
    time_dims = [d for d in dims if d in TIME_DIMS]
    if tpl.kind == "list":
        order = ""
    elif not dims:
        order = "1"
    else:
        measure = tpl.order or "1"
        outputs = re.findall(r"AS `([^`]+)`", tpl.sql)                 # 模板输出的结果列，排序只能从这里选
        if order_by and order_by in outputs:
            measure = f"`{order_by}` {'ASC' if ascending else 'DESC'}"
        elif ascending is not None and tpl.order:
            measure = re.sub(r"\b(ASC|DESC)\b", "ASC" if ascending else "DESC", tpl.order, count=1)
        if time_dims and top_n is None and not order_by:
            order = ", ".join(str(dims.index(d) + 1) for d in time_dims) + (f", {measure}" if len(dims) > len(time_dims) else "")
        else:
            order = measure
    n = top_n or (tpl.default_top if tpl.kind == "list" else None)
    limit = f"LIMIT {max(1, min(int(n), 1000))}" if n else ""

    sql = tpl.sql
    sql = sql.replace("{dims}", "".join(f"{tpl.dims[d]} AS `{d}`, " for d in dims))
    sql = sql.replace("{group_by}", "GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims))) if dims else "")
    sql = sql.replace("{filters}", "".join(clauses)).replace("{order}", order).replace("{limit}", limit)
    if period:
        params = {"start": period.start.isoformat(), "end": period.end.isoformat(), "start_month": period.start_month,
                  "end_month": period.end_month, "m_start": period.start.replace(day=1).isoformat(),
                  "m_end": period.end.replace(day=1).isoformat()}
        sql = re.sub(r"(?<![:\w]):([a-z_]+)\b", lambda m: _lit(params[m.group(1)]) if m.group(1) in params else m.group(0), sql)
    sql = re.sub(r"\n\s*\n", "\n", sql).strip()

    title = tpl.name + (f"（{period.label}）" if period else "")
    return Rendered(sql=sql, template=tpl, title=title, period=period, group_by=dims, filters=used_filters,
                    notes=list(dict.fromkeys(notes)))
