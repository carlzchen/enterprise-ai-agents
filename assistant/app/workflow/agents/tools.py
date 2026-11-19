"""
Agent 的工具箱。三条规矩：
  1. 全部只读。没有任何一个工具能改数据库、发邮件、建单。
  2. 按角色发工具。技术 Agent 拿不到成本工具，商务 Agent 拿不到成本明细（只拿到单位成本这一个数）。
  3. 每次调用都记进「证据账本」（Ledger），返回结果里带一个证据编号 E1、E2……
     Agent 交卷时只能引用证据编号，报告里的每个数字都能追到是哪一次工具调用算出来的。
"""
import json
import threading
import time
from datetime import date

from pydantic import BaseModel, Field

from app.workflow import erp, rules, store
from app.workflow.agents.schemas import CHANGE_CN, Change

# 改型的一次性费用和周期（示例值：真实数值要由技术和财务给出，存在规则表里可以随时改）
DEFAULT_NRE = {"说明": "按改动类型的模具费、开发费、打样天数；新开物料的价格按被替换物料乘系数估算，采购周期按默认天数",
               "types": {"SHAFT": {"tooling": 3000, "dev": 5000, "sample_days": 15}, "WINDING": {"tooling": 0, "dev": 8000, "sample_days": 20},
                         "CONNECTOR": {"tooling": 2000, "dev": 3000, "sample_days": 10}, "HOUSING": {"tooling": 80000, "dev": 20000, "sample_days": 45},
                         "MAGNET": {"tooling": 0, "dev": 8000, "sample_days": 20}, "BEARING": {"tooling": 0, "dev": 3000, "sample_days": 10},
                         "ENCODER": {"tooling": 0, "dev": 10000, "sample_days": 20}, "PACKAGING": {"tooling": 1500, "dev": 1000, "sample_days": 7},
                         "OTHER": {"tooling": 5000, "dev": 10000, "sample_days": 25}},
               "new_part_price_factor": 1.2, "new_part_lead_days": 25,
               "add_part_price": {"SHAFT": 6, "WINDING": 3, "CONNECTOR": 15, "HOUSING": 8, "MAGNET": 10, "BEARING": 4, "ENCODER": 60, "PACKAGING": 1.5, "OTHER": 5}, "customer_test_days": 20, "busy_line_extra_days": 10, "busy_utilization": 0.9,
               "new_development_days": 180}


def nre_rule() -> dict:
    store.ensure()
    if not store.one("SELECT 1 FROM wf_rule WHERE name = 'nre' LIMIT 1"):
        store.execute("INSERT INTO wf_rule (name, version, content, updated_by) VALUES ('nre', 1, %s, 'system')", (store.dumps(DEFAULT_NRE),))
    return {**DEFAULT_NRE, **store.rule("nre")}                  # 规则表里缺的项用默认值补上


class Ledger:
    """证据账本：一次评估里所有工具调用的记录"""

    def __init__(self):
        self.items: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, agent: str, tool: str, args: dict, result: dict, ms: int | None = None) -> dict:
        with self._lock:
            eid = f"E{len(self.items) + 1}"
            self.items[eid] = {"id": eid, "agent": agent, "tool": tool, "args": args, "result": result, "ms": ms}
        return {"evidence_id": eid, **result}

    def get(self, eid: str | None, tool: str | None = None) -> dict | None:
        item = self.items.get(eid or "")
        return item if item and (tool is None or item["tool"] == tool) else None

    def numbers_text(self) -> str:
        return json.dumps([i["result"] for i in self.items.values()], ensure_ascii=False, default=str)


# ---------------------------------------------------------------- 工具实现（纯查询 + 纯计算）

def _material_prices() -> dict[str, float]:
    month = erp._q("SELECT MAX(price_month) m FROM md_material_price")[0]["m"]
    return {r["code"]: float(r["price"]) for r in erp._q("SELECT m.code, p.price FROM md_material_price p JOIN md_material m ON m.id = p.material_id WHERE p.price_month = %s", (month,))}


def find_similar(series: str | None = None, voltage_v: float | None = None, power_w: float | None = None, speed_rpm: float | None = None,
                 torque_nm: float | None = None, frame_size: str | None = None) -> dict:
    item = {"series": series, "voltage_v": voltage_v, "power_w": power_w, "speed_rpm": speed_rpm, "torque_nm": torque_nm, "frame_size": frame_size}
    r = rules.select({k: v for k, v in item.items() if v is not None}, erp.products(), erp.product_params, store.rule("selection"))
    return {"grade": r["grade"], "candidates": [{"model": c["model"], "name": c["name"], "satisfied": c["satisfied"], "total": c["total"], "rows": c["rows"]} for c in r["candidates"]]}


def product_detail(model: str) -> dict:
    p = erp.product(model)
    if not p:
        return {"error": f"型号 {model} 不存在"}
    cost = erp.cost_inputs(model)
    return {"model": model, "name": p["name"], "series": p["series"], "voltage": p["voltage"], "power_w": float(p["power_w"]) if p["power_w"] else None,
            "rated_speed_rpm": p["rated_speed_rpm"], "revision": p["revision"], "params": erp.product_params(model),
            "bom": [{"material": l["material"], "name": l["name"], "qty": l["qty"]} for l in cost["lines"]]}


def engineering_history(keyword: str = "", series: str | None = None) -> dict:
    """历史工程变更：同类改动以前怎么做的、单位成本变了多少"""
    rows = erp._q("SELECT e.ecn_no, e.title, e.reason, e.unit_cost_delta, e.status, GROUP_CONCAT(DISTINCT p.code) products FROM plm_ecn e "
                  "LEFT JOIN plm_ecn_line l ON l.ecn_id = e.id LEFT JOIN md_product p ON p.id = l.product_id GROUP BY e.id ORDER BY e.request_date DESC")
    out = [{"ecn_no": r["ecn_no"], "title": r["title"], "reason": r["reason"], "unit_cost_delta": float(r["unit_cost_delta"]) if r["unit_cost_delta"] is not None else None,
            "products": r["products"]} for r in rows]
    if keyword:
        out = [r for r in out if any(k in r["title"] for k in keyword.split())] or out
    if series:
        prefix = {"SERVO": "KL-SV", "STEPPER": "KL-ST", "BLDC": "KL-BL", "SHADED_POLE": "KL-SP", "CROSSFLOW": "KL-CF", "GEAR": "KL-GR"}.get(series, "")
        out = [r for r in out if prefix and prefix in (r["products"] or "")] or out
    return {"changes": out[:6]}


KB_ROLE = "ENGINEER"          # Agent 按工程师的可见范围查知识库（看不到机密级文档）


# 【讲解】复用知识库 v3 的检索：稠密 + BM25 两路召回 → RRF 融合 → 重排；向量库不可用退到 MySQL 全文检索，重排排不上队就不重排。
# 权限按工程师角色前置过滤，只返回生效版本的切片。返回 doc_key 和版本号，报告里能追到是哪份文件的哪一版。
def search_docs(query: str, model: str | None = None, series: str | None = None) -> dict:
    """在知识库里检索规格书、工艺规程、图纸的相关段落。知识库服务没起来时返回空和原因"""
    try:
        from app.kbase.query import retrieve
    except Exception as e:
        return {"docs": [], "note": f"知识库暂时不可用：{str(e)[:80]}"}
    models, note = [model] if model else None, None
    try:
        hits = retrieve.recall({"rewritten": query}, KB_ROLE, models, series, mode="no_hyde", limit=30)
    except Exception as e:
        try:
            hits, note = retrieve.fulltext(query, KB_ROLE, models, limit=30), f"向量库不可用，改用全文检索（{type(e).__name__}）"
        except Exception as e2:
            return {"docs": [], "note": f"知识库暂时不可用：{str(e2)[:80]}"}
    try:
        top = retrieve.rerank(query, hits, top=4)
    except Exception:
        top = retrieve.rerank(query, hits, top=4, use=False)
    out = {"docs": [{"title": h.get("title"), "doc_key": h.get("doc_key"), "version": h.get("version"), "section": h.get("section"), "page": h.get("page"),
                     "text": (h.get("content") or "")[:500]} for h in top]}
    return {**out, "note": note} if note else out


# 【讲解】按改动清单重算成本（成本 Agent 的核心工具）：Agent 只决定「改哪些」，「改了以后多少钱」由这里算。
# 换成已有物料用它的最新采购价；需要新开的物料按被替换物料价格 × 系数估算（报告里标「含估算」）；用量倍数直接乘。
def recost(base_model: str, changes: list[dict]) -> dict:
    """按改动清单重算单位成本。规则：换成已有物料按它的最新价；新开物料按被替换物料的价格 × 系数估算；用量倍数直接乘"""
    base = erp.cost_inputs(base_model)
    if not base:
        return {"error": f"型号 {base_model} 不存在"}
    rule, prices = nre_rule(), _material_prices()
    lines: dict[str, dict] = {}
    for l in base["lines"]:                                     # 同一物料可能出现在多个半成品下面（例如硅钢片同时用在转子和定子）：用量相加，不能按编码去重
        if l["material"] in lines:
            lines[l["material"]]["qty"] = round(lines[l["material"]]["qty"] + l["qty"], 6)
        else:
            lines[l["material"]] = dict(l)
    applied, estimated = [], []
    for ch in changes:
        c = Change.model_validate(ch)
        note = {"type": c.type, "description": c.description}
        if c.material_from and c.material_from not in lines:
            note["warning"] = f"基础型号的 BOM 里没有 {c.material_from}，这一项没有计入"
        elif c.material_from:
            line = lines[c.material_from]
            if c.material_to and c.material_to in prices:
                lines[c.material_to] = {**line, "material": c.material_to, "price": prices[c.material_to]}
                del lines[c.material_from]
                note["effect"] = f"{c.material_from} → {c.material_to}"
                line = lines[c.material_to]
            elif c.new_part or c.material_to:
                price = round(line["price"] * rule["new_part_price_factor"], 4)
                key = f"NEW:{c.material_from}"
                lines[key] = {**line, "material": key, "name": f"新开物料（替代 {line['name']}）", "price": price}
                del lines[c.material_from]
                estimated.append(key)
                note["effect"] = f"{c.material_from} 换成新开物料，单价按 {rule['new_part_price_factor']} 倍估算为 {price}"
                line = lines[key]
            if c.qty_factor:
                line["qty"] = round(line["qty"] * c.qty_factor, 6)
                note["effect"] = (note.get("effect", "") + f"；用量 × {c.qty_factor}").lstrip("；")
        elif c.new_part:                                      # 新增一个零件（不替换原有物料）：单价按改动类型的默认值估算
            price = float(rule["add_part_price"].get(c.type, rule["add_part_price"]["OTHER"]))
            key = f"ADD:{c.type}:{len(estimated) + 1}"
            lines[key] = {"material": key, "name": f"新增零件（{CHANGE_CN.get(c.type, c.type)}）", "qty": c.qty_factor or 1.0, "price": price}
            estimated.append(key)
            note["effect"] = f"新增零件，单价按规则默认值 {price} 估算"
        applied.append(note)
    material = sum(l["qty"] * l["price"] for l in lines.values())
    base_cost = rules.unit_cost(base)["total"]
    unit = rules.r4(material + base["conversion_cost"])
    return {"base_model": base_model, "base_unit_cost": base_cost, "unit_cost": unit, "delta": rules.r4(unit - base_cost), "applied": applied,
            "estimated_parts": estimated, "is_estimate": bool(estimated), "price_month": base["price_month"]}


def nre_fees(change_types: list[str]) -> dict:
    """按改动类型查一次性费用（模具费、开发费）和打样天数"""
    t = nre_rule()["types"]
    rows = [{"type": x, "name": CHANGE_CN.get(x, x), **t.get(x, t["OTHER"])} for x in dict.fromkeys(change_types)]
    return {"items": rows, "tooling_total": sum(r["tooling"] for r in rows), "dev_total": sum(r["dev"] for r in rows),
            "nre_total": sum(r["tooling"] + r["dev"] for r in rows), "sample_days": max([r["sample_days"] for r in rows], default=0)}


def line_load(series: str) -> dict:
    """该产品线所在产线近 30 天的负荷"""
    rows = erp._q("SELECT l.line_code, l.name, l.daily_capacity, l.shifts, AVG(d.total_qty) q, AVG(d.capacity) cap FROM mes_line l "
                  "LEFT JOIN mes_line_day d ON d.line_code = l.line_code AND d.work_date >= DATE_SUB((SELECT MAX(work_date) FROM mes_line_day), INTERVAL 30 DAY) "
                  "WHERE l.series = %s GROUP BY l.line_code", (series,))
    out = [{"line": r["line_code"], "name": r["name"], "daily_capacity": float(r["daily_capacity"] or 0), "avg_output": round(float(r["q"] or 0)),
            "utilization": round(float(r["q"] or 0) / float(r["cap"]), 3) if r["cap"] else None} for r in rows]
    return {"series": series, "lines": out, "busy": any((x["utilization"] or 0) >= nre_rule()["busy_utilization"] for x in out)}


def material_lead_time(materials: list[str]) -> dict:
    """物料的历史采购周期（下单到预计到货的天数）；新开物料没有历史，用默认值"""
    rule, out = nre_rule(), []
    for code in materials:
        if code.startswith("NEW:") or not code:
            out.append({"material": code, "days": rule["new_part_lead_days"], "source": "新开物料，按默认天数"})
            continue
        r = erp._q("SELECT AVG(DATEDIFF(o.expected_date, o.order_date)) d, COUNT(*) n FROM pur_order o JOIN pur_order_line l ON l.po_id = o.id "
                   "JOIN md_material m ON m.id = l.material_id WHERE m.code = %s", (code,))[0]
        out.append({"material": code, "days": round(float(r["d"]), 1) if r["n"] else rule["new_part_lead_days"],
                    "source": f"近 {r['n']} 张采购单的平均" if r["n"] else "没有采购记录，按默认天数"})
    return {"items": out, "max_days": max([x["days"] for x in out], default=0)}


def estimate_schedule(sample_days: int, material_days: float, busy: bool, new_development: bool = False) -> dict:
    """交期估算：打样 = 改动所需天数和物料到货取较长者；首批量产 = 打样 + 客户测试 + （产线忙时）排产等待"""
    rule = nre_rule()
    if new_development:
        return {"sample_days": None, "first_batch_days": rule["new_development_days"], "basis": "需要新开发，按新品项目的经验周期估算"}
    sample = int(max(sample_days, material_days))
    extra = rule["busy_line_extra_days"] if busy else 0
    first = sample + rule["customer_test_days"] + extra
    return {"sample_days": sample, "customer_test_days": rule["customer_test_days"], "busy_extra_days": extra, "first_batch_days": first,
            "first_batch_date": date.fromordinal(date.today().toordinal() + first).isoformat()}


def price_for(model: str, unit_cost: float, customer_id: int, annual_volume: int | None = None) -> dict:
    """用估算出的单位成本走正式的定价规则：阶梯价、底价、需要谁审批（和标准品报价同一套规则）"""
    ext = store.one("SELECT level FROM wf_customer_ext WHERE customer_id = %s", (customer_id,))
    level = ext["level"] if ext else "NEW"
    q = rules.price_quote({"lines": [], "conversion_cost": unit_cost, "missing": []}, level, store.rule("margin"), store.rule("tiers"))
    return {"model": model, "unit_cost": unit_cost, "level": level, "tiers": q["tiers"], "floor_price": q["floor_price"], "target_margin": q["target_margin"],
            "min_margin": q["min_margin"], "annual_volume": annual_volume}


def deal_history(model: str, customer_id: int) -> dict:
    return {"last_deal": erp.last_deal(customer_id, model), "range_12m": erp.deal_range(model)}


def amortize(nre_total: float, volume: int) -> dict:
    """一次性费用分摊进单价：每台加多少"""
    if volume <= 0:
        return {"error": "数量必须大于 0"}
    return {"nre_total": nre_total, "volume": volume, "per_unit": round(nre_total / volume, 4)}


# ---------------------------------------------------------------- 工具说明（给模型看的）和按角色分配

class _FindSimilar(BaseModel):
    series: str | None = Field(None, description="产品线代码：SERVO / STEPPER / BLDC / SHADED_POLE / CROSSFLOW / GEAR")
    voltage_v: float | None = None
    power_w: float | None = None
    speed_rpm: float | None = None
    torque_nm: float | None = None
    frame_size: str | None = None


class _Model(BaseModel):
    model: str = Field(description="我方型号，如 KL-SV-80-400W")


class _History(BaseModel):
    keyword: str = Field("", description="改动关键词，如「转轴」「磁钢」「出线」")
    series: str | None = None


class _Docs(BaseModel):
    query: str
    model: str | None = Field(None, description="锁定某个型号的资料，如 KL-SV-130-2KW；不确定就留空")
    series: str | None = Field(None, description="锁定产品线：SERVO / STEPPER / BLDC / SHADED_POLE / CROSSFLOW / GEAR")


class _Recost(BaseModel):
    base_model: str
    changes: list[Change]


class _Nre(BaseModel):
    change_types: list[str] = Field(description="改动类型列表，如 [\"SHAFT\", \"CONNECTOR\"]")


class _Series(BaseModel):
    series: str


class _Materials(BaseModel):
    materials: list[str] = Field(description="物料编码；新开物料写成 NEW:被替换的物料编码")


class _Schedule(BaseModel):
    sample_days: int
    material_days: float
    busy: bool
    new_development: bool = False


class _Price(BaseModel):
    model: str
    unit_cost: float
    customer_id: int
    annual_volume: int | None = None


class _Deal(BaseModel):
    model: str
    customer_id: int


class _Amortize(BaseModel):
    nre_total: float
    volume: int


TOOLS = {   # 名字: (函数, 参数结构, 给模型看的说明)
    "find_similar": (find_similar, _FindSimilar, "按参数在标准品里找最接近的型号，返回逐项比对"),
    "product_detail": (product_detail, _Model, "查一个型号的参数和 BOM（不含价格）"),
    "engineering_history": (engineering_history, _History, "查历史工程变更：同类改动以前怎么做、成本变了多少"),
    "search_docs": (search_docs, _Docs, "在知识库里检索规格书、工艺规程、图纸的相关段落"),
    "recost": (recost, _Recost, "按改动清单重算单位成本（程序计算）"),
    "nre_fees": (nre_fees, _Nre, "按改动类型查模具费、开发费和打样天数（规则表）"),
    "line_load": (line_load, _Series, "查产品线所在产线近 30 天的负荷"),
    "material_lead_time": (material_lead_time, _Materials, "查物料的历史采购周期"),
    "estimate_schedule": (estimate_schedule, _Schedule, "按打样天数、物料周期、产线忙闲估算打样和首批交期（程序计算）"),
    "price_for": (price_for, _Price, "用单位成本走定价规则，得到阶梯价、底价和审批层级（程序计算）"),
    "deal_history": (deal_history, _Deal, "查这个型号的历史成交价"),
    "amortize": (amortize, _Amortize, "把一次性费用分摊到单价上（程序计算）"),
}

# 每个 Agent 能用哪些工具：最小权限
SCOPES = {
    "tech": ["find_similar", "product_detail", "engineering_history", "search_docs"],
    "cost": ["product_detail", "recost", "nre_fees", "engineering_history"],
    "lead": ["line_load", "material_lead_time", "estimate_schedule", "nre_fees"],
    "commercial": ["price_for", "deal_history", "amortize"],
}
# 新开发预研（DeepAgents）：主 Agent 只拿找型号和看型号两个工具，查资料、查历史变更交给两个子 Agent
RESEARCH_SCOPES = {
    "research": ["find_similar", "product_detail"],
    "research/docs": ["search_docs", "product_detail"],
    "research/cases": ["engineering_history", "find_similar", "product_detail"],
}
SCOPES["single"] = sorted({t for v in SCOPES.values() for t in v})          # 对照方案：一个 Agent 拿全部工具


def specs(names: list[str], registry: dict | None = None) -> list[dict]:
    """转成模型的函数调用格式"""
    reg = registry or TOOLS
    return [{"type": "function", "function": {"name": n, "description": reg[n][2], "parameters": reg[n][1].model_json_schema()}} for n in names]


# 【讲解】所有工具调用都经过这里：① 检查这个 Agent 有没有权限用这个工具（最小权限）；② 用 Pydantic 校验参数；
# ③ 执行后把结果记进证据账本，返回给 Agent 的结果带上证据编号。Agent 交卷时只能引用证据编号。
def call(name: str, args: dict, agent: str, ledger: Ledger, allowed: list[str], registry: dict | None = None) -> dict:
    """执行一次工具调用。不在这个 Agent 权限里的工具直接拒绝"""
    if name not in allowed:
        return {"error": f"你没有权限使用工具 {name}"}
    fn, schema, _ = (registry or TOOLS)[name]
    try:
        parsed = schema.model_validate(args or {})
    except Exception as e:
        return {"error": f"参数不对：{str(e)[:200]}"}
    kwargs = parsed.model_dump()
    if name == "recost":
        kwargs["changes"] = [c.model_dump() if hasattr(c, "model_dump") else c for c in parsed.changes]
    t0 = time.time()
    try:
        result = fn(**kwargs)
    except Exception as e:
        return {"error": f"工具执行失败：{type(e).__name__}: {str(e)[:150]}"}
    return ledger.add(agent, name, kwargs, json.loads(json.dumps(result, default=str)), int((time.time() - t0) * 1000))
