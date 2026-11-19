"""
规则层：价格、毛利、底价、审批层级、选型比对、PO 核对、信用，全部在这里用代码算。
这里的函数不调用大模型，也不读写数据库：输入什么数据、用哪版规则，就一定得到同样的结果，所以可以写单元测试、可以拿历史数据回放。
金额统一按人民币、四位小数计算，对外展示保留两位。
"""
import re
from datetime import date, timedelta

SERIES_CN = {"SERVO": "交流伺服电机", "STEPPER": "步进电机", "BLDC": "无刷直流电机", "SHADED_POLE": "罩极电机", "CROSSFLOW": "贯流风机", "GEAR": "减速电机"}
SERIES_WORDS = {"SERVO": ["伺服", "servo"], "STEPPER": ["步进", "stepper", "step motor"], "BLDC": ["无刷", "bldc", "brushless"],
                "SHADED_POLE": ["罩极", "shaded pole", "shaded-pole"], "CROSSFLOW": ["贯流", "cross flow", "crossflow", "cross-flow"], "GEAR": ["减速", "齿轮", "gear"]}
PARAM_CN = {"voltage_v": "额定电压", "power_w": "额定功率", "speed_rpm": "额定转速", "torque_nm": "额定转矩", "frame_size": "机座号或外形尺寸", "annual_volume": "年用量"}
ROLE_CN = {"SALES_MANAGER": "销售经理", "SALES_DIRECTOR": "销售总监", "GM": "总经理"}


def r4(x: float) -> float:
    return round(float(x) + 1e-9, 4)


# ---------------------------------------------------------------- 选型

def series_of(text: str | None) -> str | None:
    t = (text or "").lower()
    return next((s for s, words in SERIES_WORDS.items() if any(w in t for w in words)), None)


def _num(text) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", str(text or ""))
    return float(m.group(0)) if m else None


def missing_params(item: dict, required: dict) -> list[str]:
    """按产品线检查必填参数。客户直接点名了型号就不再要求技术参数"""
    need = list(required.get("ALL", []))
    if not item.get("model"):
        need = required.get(item.get("series") or "", ["power_w", "voltage_v"]) + need
        if not item.get("series"):
            need = ["series"] + need
    return [k for k in need if item.get(k) in (None, "", 0)]


# 【讲解】选型比对：「满不满足」由代码比数值，不让模型判断大小（模型可能说出「1.26 小于 1.2」）。
# 电压要相等；功率要不小于要求、且不能富余太多；转速在容差内；转矩不小于要求。容差在规则表里配置。
def compare(item: dict, product: dict, params: dict, tol: dict) -> dict:
    """一个候选型号对客户要求的逐项比对。每一项：要求、实际、是否满足"""
    rows = []

    def add(name, want, got, ok, note=""):
        rows.append({"param": name, "required": want, "actual": got, "ok": bool(ok), "note": note})
    if item.get("voltage_v") is not None:
        got = _num(product.get("voltage"))
        add("额定电压", f"{item['voltage_v']:g} V", product.get("voltage") or "未知", got is not None and abs(got - item["voltage_v"]) < 1e-6)
    if item.get("power_w") is not None:
        got = float(product["power_w"]) if product.get("power_w") is not None else params.get("rated_power")
        ok = got is not None and item["power_w"] <= got <= item["power_w"] * (1 + tol.get("power_over_ratio", 0.5))
        add("额定功率", f"≥ {item['power_w']:g} W", f"{got:g} W" if got is not None else "未知", ok, "" if ok or got is None or got < item["power_w"] else "功率富余过多")
    if item.get("speed_rpm") is not None:
        got = float(product["rated_speed_rpm"]) if product.get("rated_speed_rpm") is not None else params.get("rated_speed")
        t = tol.get("speed_tolerance", 0.1)
        add("额定转速", f"{item['speed_rpm']:g} rpm（±{t:.0%}）", f"{got:g} rpm" if got is not None else "未知", got is not None and abs(got - item["speed_rpm"]) <= item["speed_rpm"] * t)
    if item.get("torque_nm") is not None:
        got = params.get("rated_torque")
        add("额定转矩", f"≥ {item['torque_nm']:g} N·m", f"{got:g} N·m" if got is not None else "资料里没有", got is not None and got >= item["torque_nm"])
    if item.get("frame_size"):
        want = re.sub(r"\D", "", str(item["frame_size"]))
        code = product["code"]
        add("机座号或外形尺寸", str(item["frame_size"]), code, bool(want) and want in re.sub(r"[^0-9xX]", " ", code).replace("x", " ").replace("X", " ").split())
    return {"model": product["code"], "name": product["name"], "rows": rows, "satisfied": sum(r["ok"] for r in rows), "total": len(rows)}


def select(item: dict, products: list[dict], params_of, tol: dict) -> dict:
    """返回匹配分级（EXACT 完全匹配 / NEAR 近似匹配 / NONE 无匹配）和按匹配程度排好的候选"""
    if item.get("model"):
        hit = next((p for p in products if p["code"].upper() == item["model"].upper()), None)
        if hit:
            c = compare(item, hit, params_of(hit["code"]), tol)
            return {"grade": "EXACT" if c["satisfied"] == c["total"] else "NEAR", "candidates": [c], "by": "客户指定了型号"}
    pool = [p for p in products if p.get("status", "ACTIVE") == "ACTIVE" and (not item.get("series") or p["series"] == item["series"])]
    cands = [compare(item, p, params_of(p["code"]), tol) for p in pool]
    cands = [c for c in cands if c["total"]]
    cands.sort(key=lambda c: (-(c["satisfied"] / c["total"]), -c["satisfied"], c["model"]))
    if not cands or cands[0]["satisfied"] == 0:
        return {"grade": "NONE", "candidates": cands[:3], "by": "按参数筛选"}
    return {"grade": "EXACT" if cands[0]["satisfied"] == cands[0]["total"] else "NEAR", "candidates": cands[:3], "by": "按参数筛选"}


# ---------------------------------------------------------------- 定价

def customer_level(credit_limit: float, rule: dict) -> str:
    if credit_limit >= rule["A_credit_at_least"]:
        return "A"
    return "B" if credit_limit >= rule["B_credit_at_least"] else "C"


def unit_cost(cost_inputs: dict) -> dict:
    material = sum(l["qty"] * l["price"] for l in cost_inputs["lines"])
    return {"material": r4(material), "conversion": r4(cost_inputs["conversion_cost"]), "total": r4(material + cost_inputs["conversion_cost"])}


def margin_of(price: float, cost: float) -> float:
    return r4((price - cost) / price) if price else 0.0


# 【讲解】定价公式（常见问题）：
#   第一档单价 = 单位成本 ÷（1 − 目标毛利率）      目标毛利率按客户等级从规则表取
#   其他各档   = 第一档 × 阶梯系数                   例如 5000 台 ×0.97、10000 台 ×0.94
#   底价       = 单位成本 ÷（1 − 最低毛利率）       任何一档都不会低于底价
# 有物料没有采购价时直接报错，宁可停下来让采购补价，也不用不完整的成本报价。
def price_quote(cost_inputs: dict, level: str, margin_rule: dict, tier_rule: dict) -> dict:
    """
    报价 = 单位成本 ÷（1 − 目标毛利率），按数量阶梯乘系数，任何一档不低于底价。
    底价 = 单位成本 ÷（1 − 最低毛利率）。
    """
    if cost_inputs["missing"]:
        raise ValueError("这些物料没有采购价，算不出成本：" + "、".join(cost_inputs["missing"]))
    m = margin_rule["levels"].get(level) or margin_rule["levels"]["NEW"]
    cost = unit_cost(cost_inputs)
    base, floor = cost["total"] / (1 - m["target"]), cost["total"] / (1 - m["min"])
    tiers = []
    for t in sorted(tier_rule["tiers"], key=lambda x: x["qty"]):
        price = round(max(base * t["factor"], floor), 2)
        tiers.append({"qty": t["qty"], "unit_price": price, "margin": margin_of(price, cost["total"])})
    return {"cost": cost, "level": level, "target_margin": m["target"], "min_margin": m["min"], "floor_price": round(floor + 0.004999, 2), "tiers": tiers,
            "margin": tiers[0]["margin"]}


def reprice(tiers: list[dict], cost_total: float) -> list[dict]:
    """销售改了价格之后重算每一档的毛利率"""
    return [{"qty": int(t["qty"]), "unit_price": round(float(t["unit_price"]), 2), "margin": margin_of(float(t["unit_price"]), cost_total)} for t in tiers]


def approval_role(lowest_margin: float, target: float, minimum: float, rule: dict) -> str:
    """按所有阶梯里最低的那个毛利率决定审批层级"""
    if lowest_margin >= target - 1e-9:
        return rule["at_or_above_target"]
    return rule["between"] if lowest_margin >= minimum - 1e-9 else rule["below_min"]


# 【讲解】审批路由：比的是「有没有低于规则算出的价格」，而不是每一档的毛利率。
# 原因：阶梯价本身带折扣，大数量那一档的毛利率天然低于目标，如果按毛利率比，所有报价都要销售总监审批。
# 这是端到端自测时发现并修正的问题。
def approval_for(tiers: list[dict], system_tiers: list[dict], min_margin: float, rule: dict) -> str:
    """
    报价要谁审批：每一档都不低于系统按规则算出的价格 → 销售经理；有哪一档低于规则价、但毛利率还在最低毛利率以上 → 销售总监；
    有哪一档的毛利率低于最低毛利率 → 总经理。（数量阶梯本身带折扣，所以不能简单拿每一档的毛利率去比目标毛利率）
    """
    system = {int(t["qty"]): float(t["unit_price"]) for t in system_tiers}
    if any(t["margin"] < min_margin - 1e-9 for t in tiers):
        return rule["below_min"]
    below = any(float(t["unit_price"]) < system.get(int(t["qty"]), float(t["unit_price"])) - 0.005 for t in tiers)
    return rule["between"] if below else rule["at_or_above_target"]


def tier_price(tiers: list[dict], qty: float) -> float | None:
    """某个数量应该适用哪一档的价格：达到的最高一档；不到第一档的按第一档"""
    ts = sorted(tiers, key=lambda t: t["qty"])
    hit = [t for t in ts if qty >= t["qty"]]
    return (hit[-1] if hit else ts[0])["unit_price"] if ts else None


# ---------------------------------------------------------------- 议价

# 【讲解】议价测算：系统只算账、列选项，不替销售做决定。
# 目标价高于底价 → 接受 / 让一部分 / 用条件换价格；低于底价 → 让到底价 / 条件换价格 / 方案换价格 / 战略性接受（必须总经理批）/ 放弃。
# 每个选项都带价格和毛利率，以及是否需要审批。
def negotiation(current_price: float, target_price: float | None, cost_total: float, floor_price: float, target_margin: float, min_margin: float, history: dict | None,
                rule_price: float | None = None) -> dict:
    """还价时把账算清楚，并按规则给出候选做法。选哪个由销售决定"""
    out = {"current_price": round(current_price, 2), "target_price": target_price, "floor_price": round(floor_price, 2), "current_margin": margin_of(current_price, cost_total),
           "last_deal_price": (history or {}).get("unit_price"), "options": []}
    if target_price is None:
        out["options"].append({"strategy": "ASK_TARGET", "title": "先问清客户的目标价", "price": None, "margin": None, "need_approval": False,
                               "why": "客户没有给出明确的目标价，没法判断差距"})
        return out
    out["gap"] = round(current_price - target_price, 2)
    out["gap_ratio"] = r4((current_price - target_price) / current_price)
    out["target_margin_at_customer_price"] = margin_of(target_price, cost_total)

    def opt(strategy, title, price, why):
        m = margin_of(price, cost_total) if price else None
        out["options"].append({"strategy": strategy, "title": title, "price": round(price, 2) if price else None, "margin": m,
                               "need_approval": bool(price) and price < (rule_price or current_price) - 0.005, "why": why})
    if target_price >= floor_price:
        opt("ACCEPT", "接受客户的目标价", target_price, f"目标价高于底价 {floor_price:.2f}，毛利率 {margin_of(target_price, cost_total):.1%}")
        mid = round((current_price + target_price) / 2, 2)
        if mid > target_price:
            opt("PARTIAL", "让一部分", mid, "取当前报价和目标价的中间值，留出再谈一轮的余地")
        opt("TRADE_TERMS", "用条件换价格", target_price, "同意目标价，但要求提高起订量、缩短账期或改用标准包装")
    else:
        opt("PARTIAL", "让到底价", floor_price, f"目标价低于底价 {floor_price:.2f}，最多只能让到底价（毛利率 {min_margin:.1%}）")
        opt("TRADE_TERMS", "用条件换价格", floor_price, "底价成交，同时要求提高起订量或缩短账期")
        opt("TRADE_SOLUTION", "用方案换价格", None, "推荐降本方案（换材料或改用标准品），需要技术确认后重新核价")
        opt("ACCEPT_STRATEGIC", "战略性接受目标价", target_price, f"低于底价，毛利率只有 {margin_of(target_price, cost_total):.1%}，必须总经理审批并写明理由")
        opt("DECLINE", "放弃", None, "目标价低于底价，且不具备战略价值")
    return out


# ---------------------------------------------------------------- PO 核对和信用

def credit_available(c: dict) -> float:
    """可用信用 = 信用额度 − 未回款（含逾期） − 已下单未发货"""
    return round(c["credit_limit"] - c["outstanding"] - c["open_orders"], 2)


# 【讲解】PO 六项核对，每一项差异都标上由谁处理：
#   价格（按数量对应的阶梯价、报价是否过期）→ 销售    型号和版本 → 技术    交期（库存 + 生产周期）→ 计划
#   付款条件 → 财务    信用额度（额度 − 未回款 − 已下单未发货）→ 财务    重复 PO 在入口就拦（flows.handle_po）
# ctx 里的数据由调用方事先查好传进来，这个函数本身不查数据库，所以可以直接写单元测试。
def review_po(po: dict, ctx: dict, rule: dict, today: date) -> list[dict]:
    """
    PO 的六项核对。ctx 里是事先查好的数据：
      quotes   {型号: {quote_no, tiers, valid_until}}   该客户该型号最新的已发出报价
      products {型号: {revision, status}}               产品主数据
      stock    {型号: 库存数量}
      credit   erp.credit() 的结果
      last_deals {型号: 上次成交价}
    返回差异项；空列表表示没有差异。每一项带负责处理的角色
    """
    issues, tol, amount = [], rule.get("price_tolerance", 0.005), 0.0

    def add(kind, role, title, detail):
        issues.append({"id": f"I{len(issues) + 1}", "type": kind, "role": role, "title": title, "detail": detail, "resolved": False})
    for line in po.get("lines", []):
        model, qty, price = line.get("model"), float(line.get("qty") or 0), line.get("unit_price")
        amount += qty * float(price or 0)
        product = ctx["products"].get(model)
        if not product:
            add("MODEL", "ENGINEER", f"型号 {model} 在产品主数据里找不到", "请确认客户写的型号是否有误，或是否为新型号")
            continue
        if product.get("status") not in (None, "ACTIVE"):
            add("MODEL", "ENGINEER", f"型号 {model} 已停用", f"当前状态 {product['status']}")
        if line.get("revision") and line["revision"] != product["revision"]:
            add("VERSION", "ENGINEER", f"{model} 的版本对不上", f"PO 写的是 {line['revision']} 版，当前生效的是 {product['revision']} 版")
        q = ctx["quotes"].get(model)
        if price is None:
            add("PRICE", "SALES", f"{model} 的 PO 上没有单价", "请和客户确认价格")
        elif not q:
            last = ctx["last_deals"].get(model)
            add("PRICE", "SALES", f"{model} 找不到有效报价", f"PO 单价 {float(price):.2f}" + (f"，上次成交价 {last:.2f}" if last else "，也没有历史成交记录") + "。请确认价格依据")
        else:
            want = tier_price(q["tiers"], qty)
            if q["valid_until"] < today:
                add("PRICE", "SALES", f"{model} 的报价 {q['quote_no']} 已过期", f"有效期到 {q['valid_until']}。PO 单价 {float(price):.2f}，报价 {want:.2f}")
            elif abs(float(price) - want) > want * tol:
                add("PRICE", "SALES", f"{model} 的单价和报价不一致", f"PO 单价 {float(price):.2f}，报价 {q['quote_no']} 在 {qty:g} 台这一档是 {want:.2f}，差 {float(price) - want:+.2f}")
        need = po.get("required_date")
        if need and qty > ctx["stock"].get(model, 0) and need < today + timedelta(days=rule.get("lead_days", 20)):
            add("DELIVERY", "PRODUCTION", f"{model} 的交期可能做不到", f"客户要求 {need} 交 {qty:g} 台，现有库存 {ctx['stock'].get(model, 0):g} 台，"
                f"标准生产周期 {rule.get('lead_days', 20)} 天，最早约 {today + timedelta(days=rule.get('lead_days', 20))}。请计划给出可行日期")
    c = ctx["credit"]
    if po.get("payment_days") is not None and c.get("payment_terms_days") is not None and int(po["payment_days"]) != int(c["payment_terms_days"]):
        add("PAYMENT", "FINANCE", "付款条件和约定的不一样", f"PO 写的是 {po['payment_days']} 天，客户档案里约定的是 {c['payment_terms_days']} 天")
    avail = credit_available(c)
    if amount > avail:
        add("CREDIT", "FINANCE", "超出可用信用额度", f"本单金额 {amount:,.2f}，可用额度 {avail:,.2f}（额度 {c['credit_limit']:,.2f} − 未回款 {c['outstanding']:,.2f} − 已下单未发货 {c['open_orders']:,.2f}）")
    elif c.get("overdue", 0) > 0:
        add("CREDIT", "FINANCE", "客户有逾期未回款", f"逾期金额 {c['overdue']:,.2f}，最长逾期 {c['max_overdue_days']} 天。额度够用，请确认是否照常接单")
    return issues
