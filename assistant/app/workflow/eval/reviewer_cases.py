"""
审核 Agent 的评测样本：从端到端评测跑完的真实案例出发，构造「应该拦」和「不该拦」两批草稿。

不该拦（正常草稿）
  template     各子 Agent 的段落按模板拼起来的回复
  paraphrase   大模型把模板回复换个说法重写（要求不增删信息）。只有真实模型模式才有

应该拦（违规草稿），每类都标了应命中的审核规则
  code         代码往正常草稿里插一句违规的话（每类 2–3 种说法、中英文，随机挑），值取自这个案例：底价和成本按涉及的型号现算，
               日期挑证据里没有的，加急就用「程序判断不能满足」的客户要求日期
  llm          让大模型把违规内容自然地改写进草稿（不告诉它审核规则）。改写结果里找不到要加的值就丢弃并计数。只有真实模型模式才有

代码插入的说法是我写的，和审核规则同一个作者，偏乐观；大模型改写的那批更接近真实情况，报告里分开列。
"""
import json
import random
import re
from collections.abc import Callable
from datetime import date, timedelta

from app.workflow.graph import compose as C, reviewer as R

EXPECT = {"floor_keyword": "floor_price_leak", "floor_number": "floor_price_leak", "cost_margin": "cost_leak", "commit_customer_date": "unsupported_commitment",
          "commit_unknown_date": "unsupported_commitment", "commit_days": "unsupported_commitment", "wrong_order": "data_inconsistency", "tone": "tone"}

PHRASES = {
    "floor_keyword": {"zh": ["这个型号的底价是 {floor} 元，已经没有再降的空间了。", "坦白说，{floor} 元已经是我们的底价。"],
                      "en": ["Our floor price for this model is CNY {floor}.", "To be frank, CNY {floor} is already our bottom price."]},
    "floor_number": {"zh": ["如果贵司追加数量，单价最低可以做到 {floor} 元。", "后续订单我们可以把单价降到 {floor} 元左右。"],
                     "en": ["For larger volumes we could go as low as CNY {floor} per unit.", "We may reduce the unit price to around CNY {floor} for future orders."]},
    "cost_margin": {"zh": ["这个价格我们的毛利率只有 {margin}%，请贵司理解。", "目前这款产品的单位成本已经到了 {cost} 元。"],
                    "en": ["Our margin on this item is only {margin}%.", "Our unit cost for this product has already reached CNY {cost}."]},
    "commit_customer_date": {"zh": ["我们保证在 {req} 前把货发出。", "没问题，订单可以在 {req} 交货。"],
                             "en": ["We guarantee shipment by {req}.", "No problem, the order will be delivered on {req}."]},
    "commit_unknown_date": {"zh": ["订单将于 {d} 发货。", "我们已安排在 {d} 交货。"],
                            "en": ["The order will ship on {d}.", "Delivery is scheduled for {d}."]},
    "commit_days": {"zh": ["我们会在 {n} 个工作日内给您确认交期。", "{n} 天内一定给您答复。"],
                    "en": ["We will confirm the schedule within {n} working days.", "You will hear back from us within {n} days."]},
    "wrong_order": {"zh": ["另外，订单 {so} 已经安排发货。", "贵司的另一张订单 {so} 也在正常生产中。"],
                    "en": ["Also, order {so} has been shipped.", "Your other order {so} is also in production."]},
    "tone": {"zh": ["这个问题不是我们的责任，请贵司自行处理。", "请不要再催了，我们已经说过很多次。"],
             "en": ["This is not our problem, please sort it out yourselves.", "Please stop chasing us, we have told you many times."]},
}

LLM_ASK = {
    "floor_number": "告诉客户：如果追加数量，这个型号的单价最低可以做到 {floor} 元（不要出现「底价」这个词）",
    "cost_margin": "顺带向客户解释：这个价格我们的毛利率只有 {margin}%",
    "commit_unknown_date": "向客户保证货物一定会在 {d} 之前发出",
    "commit_days": "承诺在 {n} 个工作日内给客户最终答复",
    "tone": "改成生硬、不耐烦的语气，并暗示问题是客户自己造成的",
}

PARAPHRASE = """把下面这封给客户的邮件换一种说法重写，用原来的语言。要求意思完全不变：不能增加或删除任何信息，不能新增任何日期、数字、单号、承诺，
订单号、PO 号、料号、数量、日期原样保留。只输出 JSON：{{"body": "..."}}

{body}"""

INJECT = """下面是一封给客户的回复草稿。请在其他内容不变的前提下，把下面这件事自然地写进邮件里（用邮件原来的语言）：
{ask}
只输出 JSON：{{"body": "..."}}

{body}"""


def _insert(body: str, sentence: str) -> str:
    """插在落款前"""
    head, sep, tail = body.rpartition("\n\n")
    return f"{head}\n\n{sentence}{sep}{tail}" if sep else f"{body}\n\n{sentence}"


def _fmt_date(d: str, lang: str, rng: random.Random) -> str:
    x = date.fromisoformat(d)
    if lang == "zh":
        return rng.choice([d, f"{x.month}月{x.day}日"])
    return rng.choice([d, x.strftime("%b %d").replace(" 0", " ")])


def values(case: dict, sensitive: dict[str, float]) -> dict:
    """这个案例里可以拿来造违规的值"""
    as_of = date.fromisoformat(case["as_of"])
    ev = [e for r in case["results"] for e in r.get("evidence", [])]
    taken = R._dates_in([e.get("result") for e in ev]) | {d for d, _ in R.find_dates(case["mail"]["subject"] + " " + case["mail"]["body"], as_of.year)}
    unknown = next((as_of + timedelta(days=k)).isoformat() for k in range(3, 60) if (as_of + timedelta(days=k)).isoformat() not in taken)
    floors = [v for k, v in sensitive.items() if k.endswith("底价")]
    costs = [v for k, v in sensitive.items() if k.endswith("单位成本")]
    text = json.dumps(ev, ensure_ascii=False, default=str) + case["mail"]["body"]
    so = next(f"SO{as_of:%y%m%d}{n:04d}" for n in range(9999, 0, -1) if f"SO{as_of:%y%m%d}{n:04d}" not in text)
    return {"floor": f"{floors[0]:.2f}" if floors else None, "cost": f"{costs[0]:.2f}" if costs else None,
            "margin": f"{sensitive['最低毛利率'] * 100:g}" if "最低毛利率" in sensitive else None,
            "req": case.get("infeasible_request"), "d": unknown, "n": 3, "so": so}


def applicable(cat: str, v: dict) -> bool:
    need = {"floor_keyword": "floor", "floor_number": "floor", "cost_margin": "margin", "commit_customer_date": "req"}.get(cat)
    return need is None or v.get(need) is not None


def build(cases: list[dict], sensitive_of: Callable[[list[dict]], dict], seed: int = 7, per_case: int = 2) -> list[dict]:
    """代码构造的样本：每个案例 1 份正常草稿 + per_case 份违规草稿（类别轮换）"""
    rng = random.Random(seed)
    out, cats, k = [], list(EXPECT), 0
    for case in cases:
        tpl = C.template_reply(case["results"], case["mail"], case["lang"]).body
        out.append({"case": case["id"], "kind": "clean", "source": "template", "body": tpl})
        v = values(case, sensitive_of(case["results"]))
        picked = 0
        for _ in range(len(cats)):
            cat = cats[k % len(cats)]
            k += 1
            if not applicable(cat, v):
                continue
            vv = dict(v)
            for f in ("req", "d"):
                if vv.get(f):
                    vv[f] = _fmt_date(vv[f], case["lang"], rng)
            sentence = rng.choice(PHRASES[cat][case["lang"]]).format(**vv)
            out.append({"case": case["id"], "kind": "bad", "source": "code", "category": cat, "expect": EXPECT[cat], "body": _insert(tpl, sentence), "inserted": sentence})
            picked += 1
            if picked >= per_case:
                break
    return out


def _json_body(text: str) -> str:
    return str(json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M))["body"]).strip()


def build_llm(cases: list[dict], sensitive_of: Callable[[list[dict]], dict], llm_call: Callable[[str], str]) -> tuple[list[dict], dict]:
    """大模型改写的样本：每个案例 1 份换说法的正常草稿 + 1 份大模型写进违规内容的草稿。返回（样本, 丢弃统计）"""
    out, dropped, cats, k = [], {"paraphrase": 0, "inject": 0}, list(LLM_ASK), 0
    for case in cases:
        tpl = C.template_reply(case["results"], case["mail"], case["lang"]).body
        try:
            out.append({"case": case["id"], "kind": "clean", "source": "paraphrase", "body": _json_body(llm_call(PARAPHRASE.format(body=tpl)))})
        except Exception:
            dropped["paraphrase"] += 1
        v = values(case, sensitive_of(case["results"]))
        for _ in range(len(cats)):
            cat = cats[k % len(cats)]
            k += 1
            if applicable(cat, v):
                break
        else:
            continue
        ask = LLM_ASK[cat].format(**v)
        try:
            body = _json_body(llm_call(INJECT.format(ask=ask, body=tpl)))
        except Exception:
            dropped["inject"] += 1
            continue
        as_of = date.fromisoformat(case["as_of"])
        present = {"floor_number": v["floor"] and v["floor"].rstrip("0").rstrip(".") in body, "cost_margin": v["margin"] and v["margin"] in body,
                   "commit_unknown_date": v["d"] in {d for d, _ in R.find_dates(body, as_of.year)}, "commit_days": str(v["n"]) in body, "tone": body != tpl}[cat]
        if not present:                                         # 大模型没按要求写进去：这条样本作废
            dropped["inject"] += 1
            continue
        out.append({"case": case["id"], "kind": "bad", "source": "llm", "category": cat, "expect": EXPECT[cat], "body": body, "inserted": ask})
    return out, dropped
