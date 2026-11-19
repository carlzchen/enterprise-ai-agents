"""
确定性的分析工具：归因和测算都是数学计算，由代码完成，模型只负责把结果讲清楚。
  pvm         毛利变动拆成 价格、成本、销量、结构 四个因素，四项相加严格等于总变动
  attribution 从销售事实表取两期数据做 pvm，并按系列 / 客户等维度汇总
  what_if     按给定假设重新测算，不改任何数据
"""
from collections import defaultdict

from app.copilot import db, sqlguard
from app.copilot.permission import UserContext
from app.copilot.timeparse import Period

BY_COL = {"产品系列": "series", "事业部": "business_unit", "客户": "customer_name", "行业": "industry", "区域": "region", "产品": "product_code"}


def pvm(base: dict, current: dict) -> dict:
    """
    base / current：{key: (销量, 收入, 成本)}，key 是最细的分析粒度（产品）。
    返回 {total: {price, cost, volume, mix, delta, gp0, gp1}, items: {key: {price, cost, volume, mix, delta}}}
    """
    q0 = sum(v[0] for v in base.values())
    q1 = sum(v[0] for v in current.values())
    gp0 = sum(v[1] - v[2] for v in base.values())
    gp1 = sum(v[1] - v[2] for v in current.values())
    items = {}
    for k in set(base) | set(current):
        a_q, a_r, a_c = base.get(k, (0, 0, 0))
        b_q, b_r, b_c = current.get(k, (0, 0, 0))
        p0, c0 = (a_r / a_q, a_c / a_q) if a_q else (None, None)
        p1, c1 = (b_r / b_q, b_c / b_q) if b_q else (None, None)
        if p0 is None and p1 is None:
            continue
        if p0 is None:                 # 新产品：没有上期单价可比，全部算作结构变化
            p0, c0 = p1, c1
        if p1 is None:                 # 停售产品：本期没有销量，价格和成本影响为 0
            p1, c1 = p0, c0
        m0 = p0 - c0
        s0 = a_q / q0 if q0 else 0
        s1 = b_q / q1 if q1 else 0
        price = (p1 - p0) * b_q
        cost = -(c1 - c0) * b_q
        volume = (q1 - q0) * s0 * m0
        mix = (s1 - s0) * q1 * m0
        items[k] = {"price": price, "cost": cost, "volume": volume, "mix": mix, "delta": price + cost + volume + mix}
    total = {f: sum(i[f] for i in items.values()) for f in ("price", "cost", "volume", "mix")}
    total.update(delta=gp1 - gp0, gp0=gp0, gp1=gp1)
    return {"total": total, "items": items}


def _facts(ctx: UserContext, p: Period, by_col: str, where: str) -> tuple[list[dict], sqlguard.Guarded]:
    sql = (f"SELECT f.product_code, f.{by_col} AS grp, SUM(f.qty) AS qty, SUM(f.revenue_cny) AS revenue, SUM(f.cost_cny) AS cost "
           f"FROM v_sales_fact f WHERE f.delivery_date BETWEEN '{p.start.isoformat()}' AND '{p.end.isoformat()}'{where} "
           f"GROUP BY f.product_code, f.{by_col} LIMIT 1000")
    g = sqlguard.guard(sql, ctx)
    return db.ro_query(g.sql)[1], g


def attribution(ctx: UserContext, current: Period, base: Period, by: str = "产品系列", where: str = "") -> dict:
    """毛利归因。where 是模板引擎生成的过滤片段（已转义）。成本列受权限控制，无权限会在 guard 里被拒绝"""
    by_col = BY_COL.get(by, "series")
    rows1, g1 = _facts(ctx, current, by_col, where)
    rows0, _ = _facts(ctx, base, by_col, where)
    cur = {(r["product_code"], r["grp"]): (r["qty"] or 0, r["revenue"] or 0, r["cost"] or 0) for r in rows1}
    old = {(r["product_code"], r["grp"]): (r["qty"] or 0, r["revenue"] or 0, r["cost"] or 0) for r in rows0}
    res = pvm(old, cur)
    groups: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for (code, grp), eff in res["items"].items():
        for f, v in eff.items():
            groups[grp][f] += v
    rev0, rev1 = sum(v[1] for v in old.values()), sum(v[1] for v in cur.values())
    t = res["total"]
    summary = {
        "当前期": current.label, "对比期": base.label,
        "当前期收入": rev1, "对比期收入": rev0, "当前期毛利": t["gp1"], "对比期毛利": t["gp0"],
        "当前期毛利率": t["gp1"] / rev1 if rev1 else None, "对比期毛利率": t["gp0"] / rev0 if rev0 else None,
        "毛利变动": t["delta"], "价格影响": t["price"], "成本影响": t["cost"], "销量影响": t["volume"], "结构影响": t["mix"],
    }
    factor_rows = [{"因素": n, "影响金额": t[f], "占变动比例": (t[f] / t["delta"] if t["delta"] else None)}
                   for n, f in (("价格", "price"), ("成本", "cost"), ("销量", "volume"), ("结构", "mix"))]
    factor_rows.append({"因素": "合计（毛利变动）", "影响金额": t["delta"], "占变动比例": 1.0 if t["delta"] else None})
    group_rows = sorted(({by: g, "价格影响": e["price"], "成本影响": e["cost"], "销量影响": e["volume"], "结构影响": e["mix"],
                          "毛利变动影响": e["delta"]} for g, e in groups.items()), key=lambda r: r["毛利变动影响"])
    return {"summary": summary, "factors": factor_rows, "groups": group_rows, "by": by, "sql": g1.sql, "scope_notes": g1.scope_notes}


def material_clue(ctx: UserContext, current: Period, base: Period) -> list[dict]:
    """归因的佐证：两期的原材料采购价差率。没有权限或没有数据时返回空，不影响主结果"""
    try:
        out = []
        for label, p in (("对比期", base), ("当前期", current)):
            sql = ("SELECT v.category AS `物料类别`, ROUND(SUM(v.variance) / NULLIF(SUM(v.qty * v.po_price), 0), 4) AS `价差率`, "
                   f"ROUND(SUM(v.variance), 2) AS `价差金额` FROM v_price_variance v WHERE v.invoice_date BETWEEN "
                   f"'{p.start.isoformat()}' AND '{p.end.isoformat()}' GROUP BY v.category")
            for r in db.ro_query(sqlguard.guard(sql, ctx, dry_run=False).sql)[1]:
                out.append({"期间": label, **r})
        return out
    except Exception:
        return []


SCENARIOS = {"计件单价": "piece_rate", "基本工资": "base_salary", "销售价格": "sales_price", "单位成本": "unit_cost"}


def what_if(ctx: UserContext, scenario: str, pct: float, period: Period, where: str = "") -> dict:
    """
    按假设重新测算（pct 为百分比，5 表示上调 5%）。只读数据做计算，不改任何记录。
    返回 {rows, assumptions, sql}
    """
    x = pct / 100.0
    kind = SCENARIOS.get(scenario, scenario)
    months = f"s.period BETWEEN '{period.start_month}' AND '{period.end_month}'"
    if kind == "piece_rate":
        sql = (f"SELECT s.line_code AS `产线`, COUNT(DISTINCT s.employee_id) AS `计件人数`, "
               f"ROUND(SUM(s.piece_pay + s.guarantee_topup), 2) AS `现计件与保底合计`, "
               f"ROUND(SUM(GREATEST(s.piece_pay * {1 + x}, IF(s.guarantee_topup > 0, s.piece_pay + s.guarantee_topup, 0))), 2) AS `调整后合计`, "
               f"ROUND(SUM(GREATEST(s.piece_pay * {1 + x}, IF(s.guarantee_topup > 0, s.piece_pay + s.guarantee_topup, 0)) "
               f"- (s.piece_pay + s.guarantee_topup)), 2) AS `人工成本变动` "
               f"FROM v_payslip s WHERE s.wage_type = 'PIECE' AND {months}{where} GROUP BY s.line_code ORDER BY s.line_code")
        assumptions = [f"计件单价统一调整 {pct:+g}%，产量和出勤不变",
                       "原来拿保底的员工，计件工资上涨后先冲抵保底补差，超过保底的部分才增加成本",
                       "社保公积金缴费基数不变，所以单位社保不变", "加班溢价按保底工资计算，不受计件单价影响"]
    elif kind == "base_salary":
        sql = (f"SELECT s.dept_name AS `部门`, COUNT(DISTINCT s.employee_id) AS `月薪人数`, ROUND(SUM(s.gross_pay), 2) AS `现应发工资`, "
               f"ROUND(SUM((s.base_pay - s.deduction + s.overtime_pay) * {x}), 2) AS `人工成本变动` "
               f"FROM v_payslip s WHERE s.wage_type = 'MONTHLY' AND {months}{where} GROUP BY s.dept_name ORDER BY `人工成本变动` DESC")
        assumptions = [f"月薪员工基本工资统一调整 {pct:+g}%，人数和出勤不变", "加班工资和缺勤扣款按基本工资同比例变化",
                       "社保公积金缴费基数不变，所以单位社保不变", "没有考虑个税变化（个税由员工承担，不影响企业成本）"]
    elif kind in ("sales_price", "unit_cost"):
        dates = f"f.delivery_date BETWEEN '{period.start.isoformat()}' AND '{period.end.isoformat()}'"
        delta = f"SUM(f.revenue_cny) * {x}" if kind == "sales_price" else f"-SUM(f.cost_cny) * {x}"
        sql = (f"SELECT f.series AS `产品系列`, ROUND(SUM(f.revenue_cny), 2) AS `销售收入`, ROUND(SUM(f.gross_profit_cny), 2) AS `现毛利`, "
               f"ROUND({delta}, 2) AS `毛利变动`, "
               f"ROUND((SUM(f.gross_profit_cny) + {delta}) / NULLIF(SUM(f.revenue_cny){' * ' + str(1 + x) if kind == 'sales_price' else ''}, 0), 4) AS `调整后毛利率` "
               f"FROM v_sales_fact f WHERE {dates}{where} GROUP BY f.series ORDER BY `毛利变动`")
        what = "销售价格" if kind == "sales_price" else "单位成本"
        assumptions = [f"{what}统一调整 {pct:+g}%，销量和产品结构不变", "没有考虑价格变化对销量的影响" if kind == "sales_price" else "没有考虑成本变化向售价的传导"]
    else:
        raise ValueError(f"不支持的测算场景：{scenario}")
    g = sqlguard.guard(sql, ctx)
    cols, rows = db.ro_query(g.sql)
    return {"columns": cols, "rows": rows, "assumptions": assumptions, "sql": g.sql, "scope_notes": g.scope_notes}
