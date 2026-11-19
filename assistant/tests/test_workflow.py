"""Workflow 规则层和邮件解析的单元测试：纯函数，不依赖数据库和大模型"""
from datetime import date

import pytest

from app.workflow import mailbox, rules, understand
from app.workflow.store import DEFAULT_RULES as R

COST = {"model": "X", "revision": "A", "conversion_cost": 7.0, "price_month": "2026-09-01", "missing": [],
        "lines": [{"material": "M1", "name": "铜线", "qty": 0.2, "price": 60.0}, {"material": "M2", "name": "硅钢", "qty": 1.0, "price": 6.0}]}      # 材料 18 + 加工 7 = 25
PRODUCTS = [{"code": "KL-SV-60-200W", "name": "伺服 60 200W", "series": "SERVO", "voltage": "220VAC", "power_w": 200, "rated_speed_rpm": 3000, "status": "ACTIVE"},
            {"code": "KL-SV-80-400W", "name": "伺服 80 400W", "series": "SERVO", "voltage": "220VAC", "power_w": 400, "rated_speed_rpm": 3000, "status": "ACTIVE"},
            {"code": "KL-SV-80-750W", "name": "伺服 80 750W", "series": "SERVO", "voltage": "220VAC", "power_w": 750, "rated_speed_rpm": 3000, "status": "ACTIVE"},
            {"code": "KL-ST-42x48", "name": "步进 42", "series": "STEPPER", "voltage": "24VDC", "power_w": None, "rated_speed_rpm": 600, "status": "ACTIVE"}]
PARAMS = {"KL-SV-60-200W": {"rated_torque": 0.637}, "KL-SV-80-400W": {"rated_torque": 1.273}, "KL-SV-80-750W": {"rated_torque": 2.39}}
TOL = R["selection"]


# ---------- 定价 ----------

def test_unit_cost_adds_material_and_conversion():
    assert rules.unit_cost(COST) == {"material": 18.0, "conversion": 7.0, "total": 25.0}


def test_price_from_target_margin_and_tiers():
    q = rules.price_quote(COST, "A", R["margin"], R["tiers"])           # A 级：目标 20%，最低 10%
    assert q["tiers"][0] == {"qty": 1000, "unit_price": 31.25, "margin": 0.2}        # 25 ÷ 0.8
    assert q["tiers"][1]["unit_price"] == 30.31 and q["tiers"][2]["unit_price"] == 29.38
    assert q["floor_price"] == 27.78                                    # 25 ÷ 0.9，向上取到分
    assert all(t["unit_price"] >= q["floor_price"] for t in q["tiers"])


def test_tier_never_goes_below_floor():
    steep = {"tiers": [{"qty": 1000, "factor": 1.0}, {"qty": 10000, "factor": 0.5}]}
    q = rules.price_quote(COST, "A", R["margin"], steep)
    assert q["tiers"][1]["unit_price"] == 27.78


def test_missing_material_price_stops_quote():
    with pytest.raises(ValueError, match="没有采购价"):
        rules.price_quote({**COST, "missing": ["M9"]}, "A", R["margin"], R["tiers"])


@pytest.mark.parametrize("margin, role", [(0.20, "SALES_MANAGER"), (0.25, "SALES_MANAGER"), (0.15, "SALES_DIRECTOR"), (0.10, "SALES_DIRECTOR"), (0.08, "GM")])
def test_approval_route(margin, role):
    assert rules.approval_role(margin, 0.20, 0.10, R["approval"]) == role


def test_approval_compares_each_tier_with_rule_price():
    system = rules.price_quote(COST, "A", R["margin"], R["tiers"])["tiers"]
    same = rules.reprice(system, 25.0)
    assert rules.approval_for(same, system, 0.10, R["approval"]) == "SALES_MANAGER"          # 阶梯折扣本身不算让价
    cut = rules.reprice([{**t, "unit_price": t["unit_price"] - 1} for t in system], 25.0)
    assert rules.approval_for(cut, system, 0.10, R["approval"]) == "SALES_DIRECTOR"
    deep = rules.reprice([{**t, "unit_price": 27.0} for t in system], 25.0)
    assert rules.approval_for(deep, system, 0.10, R["approval"]) == "GM"


def test_reprice_recomputes_margin():
    assert rules.reprice([{"qty": 1000, "unit_price": 27.0}], 25.0)[0]["margin"] == 0.0741


@pytest.mark.parametrize("qty, price", [(500, 31.25), (1000, 31.25), (5000, 30.31), (8000, 30.31), (20000, 29.38)])
def test_tier_price_picks_highest_reached_tier(qty, price):
    tiers = rules.price_quote(COST, "A", R["margin"], R["tiers"])["tiers"]
    assert rules.tier_price(tiers, qty) == price


def test_customer_level_from_credit_limit():
    assert [rules.customer_level(x, R["customer_level"]) for x in (36e6, 14e6, 5e6)] == ["A", "B", "C"]


# ---------- 选型 ----------

def test_select_exact_match_by_params():
    r = rules.select({"series": "SERVO", "voltage_v": 220, "power_w": 400, "speed_rpm": 3000}, PRODUCTS, lambda m: PARAMS.get(m, {}), TOL)
    assert r["grade"] == "EXACT" and r["candidates"][0]["model"] == "KL-SV-80-400W"


def test_select_compares_numbers_in_code_not_by_model():
    """1.26 ≥ 1.2 这种判断由代码做：要求转矩 1.2，1.273 的满足，0.637 的不满足"""
    r = rules.select({"series": "SERVO", "torque_nm": 1.2, "power_w": 400}, PRODUCTS, lambda m: PARAMS.get(m, {}), TOL)
    top = r["candidates"][0]
    assert top["model"] == "KL-SV-80-400W" and all(x["ok"] for x in top["rows"])
    small = rules.compare({"torque_nm": 1.2}, PRODUCTS[0], PARAMS["KL-SV-60-200W"], TOL)
    assert small["rows"][0]["ok"] is False


def test_select_near_and_none():
    near = rules.select({"series": "SERVO", "voltage_v": 220, "power_w": 400, "speed_rpm": 1500}, PRODUCTS, lambda m: PARAMS.get(m, {}), TOL)
    assert near["grade"] == "NEAR" and any(not x["ok"] for x in near["candidates"][0]["rows"])
    none = rules.select({"series": "SERVO", "voltage_v": 48, "power_w": 5000}, PRODUCTS, lambda m: {}, TOL)
    assert none["grade"] == "NONE"


def test_named_model_is_used_directly():
    r = rules.select({"model": "kl-sv-60-200w", "annual_volume": 5000}, PRODUCTS, lambda m: {}, TOL)
    assert r["candidates"][0]["model"] == "KL-SV-60-200W" and r["by"] == "客户指定了型号"


def test_missing_params():
    req = R["required_params"]
    assert rules.missing_params({"series": "SERVO", "power_w": 400}, req) == ["voltage_v", "annual_volume"]
    assert rules.missing_params({"model": "KL-SV-60-200W", "annual_volume": 3000}, req) == []
    assert "series" in rules.missing_params({"power_w": 100}, req)


def test_series_from_text():
    assert rules.series_of("需要一款伺服电机") == "SERVO" and rules.series_of("stepper motor NEMA17") == "STEPPER" and rules.series_of("你好") is None


# ---------- 议价 ----------

def test_negotiation_above_floor_offers_accept():
    n = rules.negotiation(31.25, 29.0, 25.0, 27.78, 0.20, 0.10, {"unit_price": 30.0})
    assert n["gap"] == 2.25 and [o["strategy"] for o in n["options"]] == ["ACCEPT", "PARTIAL", "TRADE_TERMS"]
    assert n["options"][0]["need_approval"] is True and n["options"][0]["margin"] == 0.1379
    assert rules.negotiation(31.25, 31.25, 25.0, 27.78, 0.20, 0.10, None)["options"][0]["need_approval"] is False


def test_negotiation_below_floor_never_offers_plain_accept():
    n = rules.negotiation(31.25, 26.0, 25.0, 27.78, 0.20, 0.10, None)
    kinds = [o["strategy"] for o in n["options"]]
    assert "ACCEPT" not in kinds and "DECLINE" in kinds and "ACCEPT_STRATEGIC" in kinds
    assert next(o for o in n["options"] if o["strategy"] == "PARTIAL")["price"] == 27.78


def test_negotiation_without_target_price():
    assert rules.negotiation(31.25, None, 25.0, 27.78, 0.2, 0.1, None)["options"][0]["strategy"] == "ASK_TARGET"


# ---------- PO 核对 ----------

T = date(2026, 10, 12)
CTX = {"quotes": {"KL-SV-80-400W": {"quote_no": "Q1", "valid_until": date(2026, 11, 1), "tiers": [{"qty": 1000, "unit_price": 300.0}, {"qty": 5000, "unit_price": 291.0}]}},
       "products": {"KL-SV-80-400W": {"revision": "B", "status": "ACTIVE"}}, "stock": {"KL-SV-80-400W": 100}, "last_deals": {},
       "credit": {"credit_limit": 5_000_000, "outstanding": 1_000_000, "overdue": 0, "max_overdue_days": 0, "open_orders": 500_000, "payment_terms_days": 60}}
PO = {"po_no": "PO1", "required_date": date(2026, 12, 20), "payment_days": 60, "lines": [{"model": "KL-SV-80-400W", "qty": 5000, "unit_price": 291.0}]}


def issues(po=None, **ctx):
    return [(i["type"], i["role"]) for i in rules.review_po({**PO, **(po or {})}, {**CTX, **ctx}, R["po_review"], T)]


def test_clean_po_has_no_issue():
    assert issues() == []


def test_po_price_uses_the_tier_for_its_quantity():
    assert issues({"lines": [{"model": "KL-SV-80-400W", "qty": 5000, "unit_price": 300.0}]}) == [("PRICE", "SALES")]      # 5000 台应该是 291
    assert issues({"lines": [{"model": "KL-SV-80-400W", "qty": 2000, "unit_price": 300.0}]}) == []


def test_po_expired_quote_and_missing_quote():
    old = {"KL-SV-80-400W": {**CTX["quotes"]["KL-SV-80-400W"], "valid_until": date(2026, 10, 1)}}
    assert issues(quotes=old) == [("PRICE", "SALES")] and issues(quotes={}) == [("PRICE", "SALES")]


def test_po_unknown_model_and_wrong_revision():
    assert issues({"lines": [{"model": "KL-XX-1", "qty": 10, "unit_price": 1}]}) == [("MODEL", "ENGINEER")]
    assert issues({"lines": [{"model": "KL-SV-80-400W", "qty": 5000, "unit_price": 291.0, "revision": "A"}]}) == [("VERSION", "ENGINEER")]


def test_po_delivery_payment_credit():
    assert issues({"required_date": date(2026, 10, 20)}) == [("DELIVERY", "PRODUCTION")]
    assert issues({"payment_days": 90}) == [("PAYMENT", "FINANCE")]
    assert issues({"lines": [{"model": "KL-SV-80-400W", "qty": 20000, "unit_price": 291.0}]}) == [("CREDIT", "FINANCE")]      # 582 万 > 可用 350 万
    assert issues(credit={**CTX["credit"], "overdue": 200_000, "max_overdue_days": 12}) == [("CREDIT", "FINANCE")]


def test_credit_available():
    assert rules.credit_available(CTX["credit"]) == 3_500_000


# ---------- 邮件解析 ----------

def test_strip_quoted_history():
    body = "价格偏高，目标价 28 元，能做吗？\n\n在 2026年10月8日，sales@kelang.example 写道：\n> 您好，报价如下\n> 单价 31.25"
    assert mailbox.strip_quotes(body) == "价格偏高，目标价 28 元，能做吗？"
    en = "Please confirm delivery.\n\nOn Mon, Oct 5, 2026 at 9:00 AM Sales <a@b.com> wrote:\n> quote attached"
    assert mailbox.strip_quotes(en) == "Please confirm delivery."
    assert mailbox.strip_quotes("见附件 PO。\n\n-----Original Message-----\nFrom: x") == "见附件 PO。"


def test_forwarded_mail_real_sender():
    body = "请跟进。\n\n---------- Forwarded message ---------\n发件人: 张工 <zhang@customer.example.com>\n主题: 询价\n\n需要 400W 伺服电机"
    assert mailbox.forwarded_sender(body) == "zhang@customer.example.com"
    assert mailbox.forwarded_sender("普通邮件") is None


def test_thread_key_ignores_reply_prefixes():
    assert mailbox.subject_key("Re: RE: 回复: 关于 KL-SV-80-400W 的询价") == mailbox.subject_key("Fwd: 关于 KL-SV-80-400W 的询价")


def test_language_detect():
    assert mailbox.language("请报价") == "zh" and mailbox.language("Please quote 5000 pcs") == "en"


# ---------- 起草内容的数字校验 ----------

def test_draft_numbers_must_come_from_facts():
    facts = {"model": "KL-SV-80-400W", "price": 300.25, "qty": 5000, "valid_until": "2026-11-11", "quote_no": "Q-2610-0007"}
    ok = "KL-SV-80-400W 5000 台的单价是 300.25 元，有效期到 2026-11-11，报价单号 Q-2610-0007。"
    assert understand.unknown_numbers(ok, facts) == []
    assert understand.unknown_numbers("单价 298.00 元，另可再降 3%。", facts) == ["298.00", "3"]


def test_special_requirements_field_exists():
    """标准参数之外的定制要求要单独抽出来，选型时据此判断是不是非标"""
    item = understand.InquiryItem(power_w=400, special_requirements=["轴伸加长到 40mm"])
    assert item.special_requirements == ["轴伸加长到 40mm"]
