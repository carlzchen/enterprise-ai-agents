"""查询结果的整理：列类型推断、编码翻译成中文、转成给模型看的表格、单行结果直接生成一句话"""
import re

from app.copilot import labels

_PCT = re.compile(r"率|占比|OEE|稼动|效率|比例|同比|环比|增幅|降幅")
_MONEY = re.compile(r"金额|成本|工资|收入|毛利|产值|单价|售价|价差|余额|应发|实发|补差|公积金|社保|费用|影响|利润|pay|cost|amount|revenue|profit|price|salary", re.I)
_INT = re.compile(r"人数|人次|人天|人月|笔数|单数|天数|次数|发票数|数量|销量|良品数|报废数|qty|count", re.I)


def col_type(name: str, values: list) -> str:
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not nums:
        return "text"
    if _PCT.search(name) and all(abs(v) <= 10 for v in nums):
        return "pct"
    if _MONEY.search(name):
        return "money"
    if _INT.search(name) or all(float(v).is_integer() for v in nums):
        return "int"
    return "num"


def build(columns: list[str], rows: list[dict]) -> dict:
    """返回 {columns: [{name, type}], rows: [dict]}，编码已翻译，整数不带小数点"""
    meta = [{"name": c, "type": col_type(c, [r.get(c) for r in rows])} for c in columns]
    out = []
    for r in rows:
        row = {}
        for m in meta:
            v = r.get(m["name"])
            if m["type"] == "int" and isinstance(v, float) and v.is_integer():
                v = int(v)
            if v is None and m["type"] == "text":
                v = "未分配"                                   # 分组列为空（比如职能人员没有产线）
            row[m["name"]] = labels.display(m["name"], v)
        out.append(row)
    return {"columns": meta, "rows": out}


def fmt(value, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.2f}%"
    if kind == "money":
        return f"{value / 10000:,.2f} 万元" if abs(value) >= 100000 else f"{value:,.2f} 元"
    if kind == "int":
        return f"{value:,.0f}"
    if kind == "num":
        return f"{value:,.2f}"
    return str(value)


def to_markdown(result: dict, max_rows: int = 30, max_cols: int = 9) -> str:
    cols = result["columns"][:max_cols]
    lines = ["| " + " | ".join(c["name"] for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in result["rows"][:max_rows]:
        lines.append("| " + " | ".join(fmt(r.get(c["name"]), c["type"]) for c in cols) + " |")
    if len(result["rows"]) > max_rows:
        lines.append(f"（共 {len(result['rows'])} 行，只列出前 {max_rows} 行）")
    return "\n".join(lines)


def one_line(title: str, result: dict) -> str | None:
    """只有一行结果时，不调用模型，直接把数字拼成一句话（又快又不会说错）"""
    if len(result["rows"]) != 1:
        return None
    r = result["rows"][0]
    dims = [f"{r[c['name']]}" for c in result["columns"] if c["type"] == "text" and r.get(c["name"]) is not None]
    nums = [f"{c['name']} **{fmt(r[c['name']], c['type'])}**" for c in result["columns"] if c["type"] != "text"]
    if not nums:
        return None
    head = title + ("，" + "、".join(dims) if dims else "")
    return f"{head}：" + "，".join(nums) + "。"


def headline(result: dict, prefer: list[str] | None = None) -> str | None:
    """
    多行结果的第一句话由代码直接给出（最高、最低，或最新一期），查询一结束就能推给用户，不用等模型。
    模型随后只补充其他值得注意的点。
    """
    rows, cols = result["rows"], result["columns"]
    dims = [c["name"] for c in cols if c["type"] == "text"]
    measures = [c for c in cols if c["type"] != "text"]
    if len(rows) < 2 or not dims or not measures:
        return None
    prefer = prefer or []
    m = next((c for c in measures if any(p and (p in c["name"] or c["name"] in p) for p in prefer)), None) or \
        next((c for c in measures if c["type"] == "pct"), measures[-1] if len(measures) > 2 else measures[0])
    valid = [r for r in rows if isinstance(r.get(m["name"]), (int, float)) and not str(r.get(dims[0])).startswith("合计")]
    if len(valid) < 2:
        return None
    label = lambda r: "、".join(str(r[d]) for d in dims)                     # noqa: E731
    time_dim = next((d for d in dims if d in ("月份", "季度")), None)
    hi, lo = max(valid, key=lambda r: r[m["name"]]), min(valid, key=lambda r: r[m["name"]])
    if time_dim and len(dims) == 1:
        last = max(valid, key=lambda r: str(r[time_dim]))
        return (f"共 {len(valid)} 期，最新一期 {last[time_dim]} 的{m['name']}为 **{fmt(last[m['name']], m['type'])}**；"
                f"最高在 {hi[time_dim]}（{fmt(hi[m['name']], m['type'])}），最低在 {lo[time_dim]}（{fmt(lo[m['name']], m['type'])}）。")
    return (f"共 {len(valid)} 项，{m['name']}最高的是 **{label(hi)}**（{fmt(hi[m['name']], m['type'])}），"
            f"最低的是 **{label(lo)}**（{fmt(lo[m['name']], m['type'])}）。")
