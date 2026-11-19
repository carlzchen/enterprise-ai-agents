"""
审核 Agent：每一封要发给客户的草稿都过这里，不通过就驳回重写（最多 WORKFLOW_REVIEW_MAX_ROUNDS 次），之后转人工。

主要检查都是代码，不靠模型「觉得」：
  floor_price_leak / cost_leak   底价、成本、毛利：关键词，加上这次涉及的型号算出来的单位成本和底价，草稿里出现这些数字就拦
  unsupported_commitment         承诺句（保证、将于、安排在、will ship……）里的日期和数字，必须在工具结果里出现过；答复时限（3 个工作日内）一律拦；
                                 客户自己要求的日期不算依据（只是复述「您要求的 X 日正在评估」不算承诺），PO 里「早于标准交期」那一行的日期也不算
  unsupported_number             其他句子里的数字，必须在工具结果、子任务结果或客户来信里出现过
  data_inconsistency             提到了这次没查过的订单号；某张订单的句子里出现了别的订单的日期；漏了某个子任务
  tone                           生硬、推卸的说法：规则只认几个明显的词，主要靠一次大模型语气检查（图上默认开启，测试和 --fake 评测里关掉）
"""
import json
import re
from collections.abc import Callable
from datetime import date

from app.workflow import erp, rules, store
from app.workflow.graph.state import ReviewIssue, ReviewResult
from app.workflow.understand import _numbers, unknown_numbers

FLOOR_WORDS = r"底价|最低价|价格底线|floor price|bottom price|lowest (?:possible )?price"
COST_WORDS = r"成本|毛利|利润率|利润空间|进价|cost price|our cost|unit cost|production cost|gross margin|profit margin|\bmargins?\b"
COMMIT_WORDS = (r"保证|确保|承诺|一定|可以在|能在|将在|将于|会在|安排在|预计|可于|提前到|按时|如期|准时|发货|交货|到货|送达|交付|"
                r"\bguarantee|\bensure|\bwill\b|\bcan (?:deliver|ship)|\bcommit|\bpromise|\bconfirm|\bexpected\b|\bscheduled\b|\bship|\bdeliver|\barriv")
# 承诺句里出现客户自己提的日期：如果是在说「正在评估、尽快回复」而没有肯定答应，就是复述客户的要求，不算承诺
DEFER = (r"评估|核实|确认后|尽快回复|回复您|答复您|跟进|无法|暂时不能|暂不能|不能确认|待确认|您希望|您要求|您提出|贵司希望|贵司要求|"
         r"evaluat|assess|review|check|get back|revert|follow up|unable|cannot confirm|can't confirm|not able|your request|you requested|you asked")
AFFIRM = (r"可以满足|能满足|可满足|确认按|确认在|确认于|保证|确保|承诺|一定|将于|将在|可于|可以在|能在|按时|如期|准时|没有问题|"
          r"guarantee|ensure|will (?:ship|deliver|arrive)|can (?:meet|ship|deliver)|confirm(?:ed)? (?:delivery|shipment|the date)|commit|promise|no problem")
DURATION = r"\d+\s*(?:个)?\s*(?:工作日|自然日|天|日内|小时|周|working days?|business days?|days?|hours?|weeks?)"
RUDE = r"不可能|没办法|你们自己|贵司自己的问题|无法负责|概不负责|请自行|！！|impossible|your fault|not our problem|no way|!!"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NOT_COMMIT_KEYS = {"customer_requested_date", "requested_date", "order_date"}     # 这些字段里的日期不能当承诺的依据
SO_RE = re.compile(r"\bSO\d{8,12}\b")


# ---------------------------------------------------------------- 日期

def find_dates(text: str, year: int) -> list[tuple[str, tuple[int, int]]]:
    """草稿里的日期 → [(YYYY-MM-DD, 在原文的位置)]。认 2026-10-18、2026年10月18日、10月18日、Oct 18、18 October 这几种写法"""
    out = []
    pats = [(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?", lambda m: (int(m[1]), int(m[2]), int(m[3]))),
            (r"(?<![\d年-])(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", lambda m: (year, int(m[1]), int(m[2]))),
            (r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", lambda m: (int(m[3] or year), MONTHS[m[1].lower()], int(m[2]))),
            (r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:,?\s*(\d{4}))?", lambda m: (int(m[3] or year), MONTHS[m[2].lower()], int(m[1])))]
    taken: list[tuple[int, int]] = []
    for pat, conv in pats:
        for m in re.finditer(pat, text, flags=re.I):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            try:
                d = date(*conv(m))
            except (ValueError, KeyError):
                continue
            taken.append(m.span())
            out.append((d.isoformat(), m.span()))
    return out


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + " " + text[b:]
    return text


def _dates_in(obj, skip_keys: set[str] = frozenset()) -> set[str]:
    """一个工具结果里出现的全部日期（跳过 skip_keys 这些字段）"""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in skip_keys:
                out |= _dates_in(v, skip_keys)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _dates_in(v, skip_keys)
    elif obj is not None:
        out |= set(re.findall(r"\d{4}-\d{2}-\d{2}", str(obj)))
    return out


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;\n])|(?<=\.)\s+", text)
    return [p.strip() for p in parts if p and p.strip()]


# ---------------------------------------------------------------- 敏感数字（底价、成本）

def sensitive_numbers(results: list[dict]) -> dict[str, float]:
    """这次涉及到的型号：单位成本、底价、最低 / 目标毛利率。算不出来的跳过（不影响关键词检查）"""
    models: set[str] = set()
    for r in results:
        for a in r.get("actions", []):
            if a["kind"] == "erp_save_sales_order":
                models |= {ln["material"] for ln in a["payload"].get("lines", [])}
        for ev in r.get("evidence", []):
            for ln in (ev.get("result") or {}).get("lines", []) or []:
                if isinstance(ln, dict) and ln.get("model"):
                    models.add(ln["model"])
    out: dict[str, float] = {}
    try:
        margin, tiers = store.rule("margin"), store.rule("tiers")
    except Exception:
        return out
    for m in sorted(models):
        try:
            ci = erp.cost_inputs(m)
            if not ci:
                continue
            q = rules.price_quote(ci, "NEW", margin, tiers)
            out[f"{m} 单位成本"] = round(q["cost"]["total"], 2)
            out[f"{m} 底价"] = q["floor_price"]
            out["最低毛利率"] = q["min_margin"]
            out["目标毛利率"] = q["target_margin"]
        except Exception:
            continue
    return out


def _near(x: float, y: float) -> bool:
    return y > 0 and abs(x - y) <= max(0.01, y * 0.005)


# ---------------------------------------------------------------- 审核

def review(body: str, results: list[dict], mail: dict, as_of: date, sensitive: dict[str, float] | None = None,
           tone_llm: Callable[[str], str] | None = None, round_no: int = 0) -> ReviewResult:
    issues: list[ReviewIssue] = []
    customer_text = f"{mail.get('subject', '')}\n{mail.get('body', '')}"
    evidence = [ev for r in results for ev in r.get("evidence", [])]
    evidence_text = json.dumps([ev.get("result") for ev in evidence] + [r.get("facts") for r in results], ensure_ascii=False, default=str)

    # 1. 底价 / 成本 / 毛利
    for m in re.finditer(FLOOR_WORDS, body, flags=re.I):
        issues.append(ReviewIssue(rule="floor_price_leak", detail=f"出现了「{m[0]}」，底价不能告诉客户", quote=_around(body, m.start())))
    for m in re.finditer(COST_WORDS, body, flags=re.I):
        issues.append(ReviewIssue(rule="cost_leak", detail=f"出现了「{m[0]}」，成本和毛利不能告诉客户", quote=_around(body, m.start())))
    dates_all = find_dates(body, as_of.year)
    plain = _strip_spans(body, [s for _, s in dates_all])
    public = {float(n) for n in _numbers(customer_text + " " + evidence_text)}     # 客户自己写的、PO 上的价格不算泄露
    percents = {float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", plain)}
    for n in {float(x) for x in _numbers(plain)} - public:
        for k, v in (sensitive or {}).items():
            hit = (n in percents and _near(n, round(v * 100, 4))) if v < 1 else _near(n, v)     # 毛利率只和百分数比
            if hit:
                rule = "floor_price_leak" if "底价" in k else "cost_leak"
                issues.append(ReviewIssue(rule=rule, detail=f"数字 {n:g} 和内部的「{k}」（{v}）一致", quote=_find_quote(body, n)))

    # 2. 承诺和数字要有依据
    commit_dates: set[str] = set()
    for ev in evidence:
        commit_dates |= _dates_in(ev.get("result"), NOT_COMMIT_KEYS | {"lines", "issues"})
    for r in results:                                         # 订单确认里的行交期：没有「早于标准交期」问题的行才算有依据
        if r.get("agent") == "order":
            bad = {i["line_no"] for i in r["facts"].get("issues", []) if i["rule"] == "early_delivery"}
            commit_dates |= {ln["delivery_date"] for i, ln in enumerate(r["facts"].get("lines", []), 1) if ln.get("delivery_date") and i not in bad}
        if r.get("facts", {}).get("committed_date"):
            commit_dates.add(r["facts"]["committed_date"])
    customer_dates = {d for d, _ in find_dates(customer_text, as_of.year)}
    any_dates = commit_dates | _dates_in([ev.get("result") for ev in evidence]) | customer_dates
    listless = re.sub(r"(?m)^\s*\d{1,2}[.、)）]\s+", "", body)          # 多件事分段时行首的「1. 2.」是序号，先去掉再分句
    for s in _sentences(listless):
        ds = find_dates(s, as_of.year)
        rest = _strip_spans(s, [sp for _, sp in ds])
        unknown = unknown_numbers(rest, {"e": evidence_text}, customer_text)
        if re.search(COMMIT_WORDS, s, flags=re.I):
            for m in re.finditer(DURATION, s, flags=re.I):          # 「3 个工作日内回复」：答复时限没有任何工具能给出依据
                issues.append(ReviewIssue(rule="unsupported_commitment", detail=f"承诺了时限「{m[0].strip()}」，没有依据", quote=s))
            restating = bool(re.search(DEFER, s, flags=re.I)) and not re.search(AFFIRM, s, flags=re.I)
            for d, _ in ds:
                if d not in commit_dates and not (restating and d in customer_dates):
                    issues.append(ReviewIssue(rule="unsupported_commitment", detail=f"承诺的日期 {d} 在工具查到的结果里没有依据", quote=s))
            if unknown:
                issues.append(ReviewIssue(rule="unsupported_commitment", detail=f"承诺里的数字 {'、'.join(unknown)} 没有依据", quote=s))
        else:
            for d, _ in ds:
                if d not in any_dates:
                    issues.append(ReviewIssue(rule="unsupported_number", detail=f"日期 {d} 在工具结果和来信里都找不到", quote=s))
            if unknown:
                issues.append(ReviewIssue(rule="unsupported_number", detail=f"数字 {'、'.join(unknown)} 在工具结果和来信里都找不到", quote=s))

    # 3. 数据一致
    known_orders = set(SO_RE.findall(evidence_text)) | set(SO_RE.findall(customer_text))
    for so in sorted(set(SO_RE.findall(body)) - known_orders):
        issues.append(ReviewIssue(rule="data_inconsistency", detail=f"订单号 {so} 这次没有查过，可能写错了", quote=_find_quote(body, so)))
    for r in results:
        oid = (r.get("facts") or {}).get("order_no")
        if r.get("agent") == "delivery" and oid:
            own = _dates_in([ev.get("result") for ev in r.get("evidence", [])]) | {d for d, _ in find_dates(customer_text, as_of.year)}
            for s in _sentences(body):
                if oid in s:
                    for d, _ in find_dates(s, as_of.year):
                        if d not in own:
                            issues.append(ReviewIssue(rule="data_inconsistency", detail=f"订单 {oid} 的句子里出现了不属于这张单的日期 {d}", quote=s))
        keys = {k for k in ((r.get("facts") or {}).get("po_no"), oid) if k}
        keys |= {str(ev["result"]["customer_po"]) for ev in r.get("evidence", []) if isinstance(ev.get("result"), dict) and ev["result"].get("customer_po")}
        named = {k for k in keys if k in (r.get("section") or "")}       # 这一段原本提到的单号（我方订单号或客户 PO 号）
        if named and not any(k in body for k in keys):
            issues.append(ReviewIssue(rule="data_inconsistency", detail=f"回复里漏掉了子任务 {r['subtask_id']}（{'/'.join(sorted(named))}）"))

    # 4. 语气
    for m in re.finditer(RUDE, body, flags=re.I):
        issues.append(ReviewIssue(rule="tone", detail=f"语气不合适：「{m[0]}」", quote=_around(body, m.start())))
    if tone_llm:
        try:
            v = json.loads(tone_llm("判断下面这封给客户的邮件语气是否专业、礼貌，不推卸责任。只输出 JSON：{\"ok\": true/false, \"reason\": \"...\"}\n\n" + body))
            if not v.get("ok", True):
                issues.append(ReviewIssue(rule="tone", detail=f"语气检查：{v.get('reason', '')}"[:200]))
        except Exception:
            pass                                              # 语气的模型检查是加分项，不可用时只用规则

    uniq, seen = [], set()
    for i in issues:
        k = (i.rule, i.detail)
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    return ReviewResult(passed=not uniq, issues=uniq, round=round_no)


def _around(text: str, pos: int, width: int = 30) -> str:
    return text[max(0, pos - width): pos + width].replace("\n", " ")


def _find_quote(text: str, needle) -> str:
    s = f"{needle:g}" if isinstance(needle, float) else str(needle)
    i = text.find(s)
    return _around(text, i) if i >= 0 else ""
