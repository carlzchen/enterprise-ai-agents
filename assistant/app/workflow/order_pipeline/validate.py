"""
规则校验（纯代码，不调大模型，不读数据库）：输入抽取结果 + 事先查好的数据（CheckContext），输出问题清单。

  extraction_incomplete  必填字段没抽到（PO 号、日期、明细的数量 / 单价 / 交期）
  unknown_part           客户料号在映射表里找不到 → 按描述里的产品线、功率、电压、规格代号给候选，等人确认
  price_deviation        单价和价目表（没有就用最近成交价）偏差超过阈值（默认 3%）
  no_price_reference     这个客户这个型号既没有价目表也没有成交记录
  below_moq / pack_multiple  数量低于最小起订量 / 不是包装倍数
  early_delivery         要求交期早于「下单日 + 标准交期」
  amount_mismatch        行金额 ≠ 数量 × 单价（多半是抽取错了）
  total_mismatch         PO 上印的总额 ≠ 各行之和（抽取错了，或者客户自己算错了）
  duplicate_po           同一个客户、同一个 PO 号、内容完全相同的 PO 已经收过
"""
import re
from dataclasses import dataclass, field
from datetime import timedelta

from app.workflow.order_pipeline.schemas import Candidate, Issue, LineMapping, POExtraction, ValidationReport

SERIES_WORDS = {"SERVO": ("伺服", "servo"), "STEPPER": ("步进", "stepper"), "BLDC": ("无刷", "bldc", "brushless"), "SHADED_POLE": ("罩极", "shaded pole"),
                "CROSSFLOW": ("贯流", "cross-flow", "crossflow"), "GEAR": ("减速", "gear")}


@dataclass
class CheckContext:
    customer_id: int
    part_map: dict[str, tuple[str, str]]               # 客户料号 → (内部物料号, 来源 mock / learned)
    catalog: list[dict]                                # 产品：code, name, series, voltage, power_w
    prices: dict[str, float]                           # 内部物料号 → 价目表单价（下单日有效的）
    last_deals: dict[str, float] = field(default_factory=dict)   # 内部物料号 → 最近成交价（没有价目表时用）
    ext: dict[str, tuple[int, int, int]] = field(default_factory=dict)   # 内部物料号 → (最小起订量, 包装倍数, 标准交期天数)
    price_tolerance: float = 0.03
    amount_tolerance: float = 0.01
    duplicate_of: str | None = None                    # 调用方查到「内容完全相同的已收 PO」时填它的来源


# ---------------------------------------------------------------- 料号候选

def _features(text: str) -> dict:
    t = (text or "").lower()
    power = re.search(r"(\d+(?:\.\d+)?)\s*w\b", t)
    volt = re.search(r"(\d+)\s*v(ac|dc)?", t)
    return {"series": {s for s, words in SERIES_WORDS.items() if any(w in t for w in words)},
            "power": float(power[1]) if power else None, "volt": int(volt[1]) if volt else None,
            "tokens": set(re.findall(r"[a-z]{1,3}\d{2,3}(?:[-x×]\d+)*|\d{2,3}[x×]\d{2,3}|\d{2,3}-\d{2,4}w?", t.replace(" ", "")))}


def suggest(desc: str | None, catalog: list[dict], top: int = 3) -> list[Candidate]:
    """按描述猜内部物料：产品线、功率、电压、规格代号（YJ61、42x40、60-200W …）逐项比对打分"""
    f = _features(desc or "")
    out = []
    for p in catalog:
        why, score = [], 0.0
        if p["series"] in f["series"]:
            score += 0.3
            why.append("产品线")
        if f["power"] and p.get("power_w") and abs(float(p["power_w"]) - f["power"]) < 0.01:
            score += 0.25
            why.append("功率")
        pv = re.search(r"(\d+)", p.get("voltage") or "")
        if f["volt"] and pv and int(pv[1]) == f["volt"]:
            score += 0.15
            why.append("电压")
        code_tail = p["code"].lower().split("-", 2)[-1]
        name_tokens = _features(f"{p['name']} {code_tail}")["tokens"] | {code_tail}
        if f["tokens"] & name_tokens:
            score += 0.3
            why.append("规格代号")
        if score >= 0.3:
            out.append(Candidate(material=p["code"], name=p["name"], score=round(min(score, 1.0), 2), why="、".join(why)))
    return sorted(out, key=lambda c: (-c.score, c.material))[:top]


# ---------------------------------------------------------------- 校验

def validate(po: POExtraction, ctx: CheckContext, missing: list[str] | None = None) -> ValidationReport:
    issues: list[Issue] = []
    mappings: list[LineMapping] = []
    for m in missing or []:
        issues.append(Issue(rule="extraction_incomplete", field=m, message=f"没有抽到 {m}"))
    if ctx.duplicate_of:
        issues.append(Issue(rule="duplicate_po", field="header.po_no", message=f"PO {po.header.po_no} 已经收过（{ctx.duplicate_of}），内容完全相同", actual=po.header.po_no))
    order_date = po.header.order_date
    for i, ln in enumerate(po.lines):
        where = f"lines[{i}]"
        hit = ctx.part_map.get(ln.customer_part_no or "")
        if hit:
            material = hit[0]
            mappings.append(LineMapping(line_no=ln.line_no, customer_part_no=ln.customer_part_no, material=material, how="learned" if hit[1] == "learned" else "map"))
        else:
            material = None
            cands = suggest(ln.description, ctx.catalog)
            mappings.append(LineMapping(line_no=ln.line_no, customer_part_no=ln.customer_part_no, material=None, how="none", candidates=cands))
            issues.append(Issue(rule="unknown_part", line_no=ln.line_no, field=f"{where}.customer_part_no", actual=ln.customer_part_no,
                                expected="、".join(c.material for c in cands) or None,
                                message=f"客户料号 {ln.customer_part_no} 没有登记" + (f"，可能是 {cands[0].material}（{cands[0].why}相符）" if cands else "，按描述也没找到候选")))
        if ln.qty is not None and ln.unit_price is not None and ln.amount is not None and abs(ln.qty * ln.unit_price - ln.amount) > ctx.amount_tolerance:
            issues.append(Issue(rule="amount_mismatch", line_no=ln.line_no, field=f"{where}.amount", expected=f"{ln.qty * ln.unit_price:.2f}", actual=f"{ln.amount:.2f}",
                                message="行金额不等于数量 × 单价，请核对抽取结果"))
        if not material:
            continue
        ref = ctx.prices.get(material) or ctx.last_deals.get(material)
        if ln.unit_price is not None:
            if not ref:
                issues.append(Issue(rule="no_price_reference", line_no=ln.line_no, field=f"{where}.unit_price", actual=f"{ln.unit_price:.2f}",
                                    message=f"{material} 对这个客户既没有价目表也没有成交记录，请确认价格依据"))
            elif abs(ln.unit_price - ref) / ref > ctx.price_tolerance:
                issues.append(Issue(rule="price_deviation", line_no=ln.line_no, field=f"{where}.unit_price", expected=f"{ref:.2f}", actual=f"{ln.unit_price:.2f}",
                                    message=f"单价 {ln.unit_price:.2f} 偏离参考价 {ref:.2f} {(ln.unit_price - ref) / ref:+.1%}（阈值 {ctx.price_tolerance:.0%}）"))
        moq, pack, lead = ctx.ext.get(material, (0, 1, 0))
        if ln.qty is not None:
            if ln.qty < moq:
                issues.append(Issue(rule="below_moq", line_no=ln.line_no, field=f"{where}.qty", expected=f"≥ {moq}", actual=f"{ln.qty:g}",
                                    message=f"数量 {ln.qty:g} 低于最小起订量 {moq}"))
            if pack > 1 and ln.qty % pack:
                issues.append(Issue(rule="pack_multiple", line_no=ln.line_no, field=f"{where}.qty", expected=f"{pack} 的倍数", actual=f"{ln.qty:g}",
                                    message=f"数量 {ln.qty:g} 不是包装倍数 {pack} 的整数倍"))
        if ln.delivery_date and order_date and ln.delivery_date < order_date + timedelta(days=lead):
            issues.append(Issue(rule="early_delivery", line_no=ln.line_no, field=f"{where}.delivery_date", expected=f"≥ {order_date + timedelta(days=lead)}",
                                actual=str(ln.delivery_date), message=f"要求交期 {ln.delivery_date} 早于标准交期（下单后 {lead} 天，即 {order_date + timedelta(days=lead)}）"))
    total = po.header.total_amount
    if total is not None and po.lines and all(ln.qty is not None and ln.unit_price is not None for ln in po.lines):
        calc = round(sum(ln.qty * ln.unit_price for ln in po.lines), 2)
        if abs(calc - total) > max(ctx.amount_tolerance, 0.005 * len(po.lines)):
            issues.append(Issue(rule="total_mismatch", field="header.total_amount", expected=f"{calc:.2f}", actual=f"{total:.2f}",
                                message=f"PO 总额 {total:,.2f} 和各行之和 {calc:,.2f} 对不上"))
    return ValidationReport(issues=issues, mappings=mappings)
