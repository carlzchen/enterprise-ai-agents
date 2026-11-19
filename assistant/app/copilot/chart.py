"""根据结果的形状选图表：有时间维度画折线，类别对比画柱状，占比画饼图。输出一个精简的描述，前端转成 ECharts 配置"""
TIME_COLS = ("月份", "季度", "日期", "年份", "month", "quarter", "period")
CHART_CN = {"柱状图": "bar", "柱形图": "bar", "条形图": "bar", "折线图": "line", "趋势图": "line", "曲线": "line", "饼图": "pie",
            "饼状图": "pie", "表格": "table", "明细": "table"}


def build(result: dict, hint: str = "bar", prefer: list[str] | None = None, title: str = "") -> dict | None:
    cols, rows = result["columns"], result["rows"]
    dims = [c["name"] for c in cols if c["type"] == "text"]
    measures = [c for c in cols if c["type"] != "text"]
    if hint == "table" or not dims or not measures or len(rows) < 2:
        return None
    rows = rows[:40]
    prefer = prefer or []
    measures.sort(key=lambda c: 0 if any(p in c["name"] or c["name"] in p for p in prefer) else 1)
    time_dim = next((d for d in dims if d in TIME_COLS), None)
    kind = "line" if (time_dim or hint == "line") else hint

    # 时间 × 类别：每个类别一条线
    if time_dim and len(dims) >= 2:
        cat = next(d for d in dims if d != time_dim)
        m = measures[0]
        xs = sorted({str(r[time_dim]) for r in rows})
        series = {}
        for r in rows:
            series.setdefault(str(r[cat]), {})[str(r[time_dim])] = r[m["name"]]
        return {"type": "line", "title": f"{title} · {m['name']}", "x": xs,
                "series": [{"name": k, "type": "line", "pct": m["type"] == "pct", "data": [v.get(x) for x in xs]}
                           for k, v in list(series.items())[:12]]}

    x_dim = time_dim or dims[0]
    xs = [str(r[x_dim]) for r in rows]
    if kind == "pie":
        m = next((c for c in measures if c["type"] != "pct"), measures[0])
        if len(rows) <= 12:
            return {"type": "pie", "title": f"{title} · {m['name']}", "x": xs,
                    "series": [{"name": m["name"], "type": "pie", "pct": False, "data": [r[m["name"]] for r in rows]}]}
        kind = "bar"
    first = measures[0]
    series = [{"name": first["name"], "type": kind, "pct": first["type"] == "pct", "axis": 0, "data": [r[first["name"]] for r in rows]}]
    # 再配一个量纲不同的指标放在右轴（金额配比率，或比率配金额）
    other = next((c for c in measures[1:] if (c["type"] == "pct") != (first["type"] == "pct")), None)
    if other:
        series.append({"name": other["name"], "type": "line" if kind == "bar" else "bar", "pct": other["type"] == "pct", "axis": 1,
                       "data": [r[other["name"]] for r in rows]})
    return {"type": kind, "title": title, "x": xs, "series": series}


def switch(chart: dict | None, result: dict, to: str, title: str = "") -> dict | None:
    """用户说"换成饼图"：不重新查询，只用同一份结果换一种画法"""
    return build(result, hint=to, title=title)
