"""
主题确认：检索之前先把「问的是哪个型号 / 哪个系列」锁定，后面的 SQL 查询和向量检索都按它过滤。

  1. 问题里的型号和型号库（参数表、金蝶物料、文档登记的型号）比对：
       完全对上          锁定这些型号
       只写了前半截      （KL-SV-80）候选只有一个就自动补全；有多个时——参数类问题请用户选（数值不能猜），说明类问题把候选都当过滤条件
  2. 问题里没有型号：追问（有上文）沿用会话里锁定的主题；否则看有没有说系列（「伺服」）
  3. 锁定结果存进会话（MongoDB 的会话摘要文档），下一轮追问直接用

为什么要单独一步：同一系列的规格书结构几乎一样，不锁型号时向量检索很容易把 400W 的段落拿来回答 750W 的问题。
"""
import time

from app.infra.persistence.history_repository import history_repository
from app.kbase import store
from app.kbase.ingest.subject import SERIES_CN, series_of

_cache = {"at": 0.0, "models": set()}
TTL = 300


def known_models() -> set[str]:
    if time.time() - _cache["at"] > TTL:
        rows = store.query("SELECT DISTINCT model AS m FROM kb_param UNION SELECT f_number FROM kd_material WHERE f_number LIKE 'KL-%' "
                           "UNION SELECT product_code FROM kb_document WHERE product_code IS NOT NULL")
        _cache.update(at=time.time(), models={r["m"] for r in rows if r["m"]})
    return _cache["models"]


def _summaries():
    return history_repository.col.database["kb_session_summary"]


def locked(session_id: str) -> dict | None:
    doc = _summaries().find_one({"session_id": session_id}, {"subject": 1})
    return (doc or {}).get("subject")


def remember(session_id: str, lock: dict) -> None:
    _summaries().update_one({"session_id": session_id}, {"$set": {"subject": lock}}, upsert=True)


def resolve(question: str, plan: dict, session_lock: dict | None, has_history: bool) -> dict:
    """返回 {models, series, source, candidates, clarify}。clarify 为 True 时应该请用户先确认型号"""
    if plan.get("external_models"):
        return {"models": [], "external_models": plan["external_models"], "series": None,
                "source": "问题", "candidates": [], "clarify": False}
    known = known_models()
    exact = [m for m in plan["models"] if m in known]
    partial = [m for m in plan["models"] if m not in known]
    cands = sorted({k for m in partial for k in known if k.startswith(m + "-") or (len(m) >= 8 and k.startswith(m))})
    series = plan.get("series")
    if exact and not partial:
        return {"models": exact, "series": series_of(exact[0]) or series, "source": "问题", "candidates": [], "clarify": False}
    if partial and len(cands) == 1:
        return {"models": [*exact, cands[0]], "series": series_of(cands[0]), "source": "补全", "candidates": cands, "clarify": False}
    if partial and cands:
        need_exact = plan["route"] in ("PARAM", "MIXED")
        return {"models": [*exact, *cands] if not need_exact else exact, "series": series_of(cands[0]), "source": "候选",
                "candidates": cands[:10], "clarify": need_exact and not exact}
    if not plan["models"] and has_history and session_lock and not series:
        return {**session_lock, "source": "上文", "candidates": [], "clarify": False}
    if series:
        return {"models": [], "series": series, "source": "问题", "candidates": [], "clarify": False}
    return {"models": exact, "series": series_of(exact[0]) if exact else None, "source": "无" if not exact else "问题", "candidates": [], "clarify": False}


def clarify_text(lock: dict, question: str) -> str:
    opts = "、".join(lock["candidates"])
    return f"你问的型号不完整，知识库里符合的有：{opts}。请告诉我具体是哪一个（参数值必须对应到具体型号，不能猜）。"


def describe(lock: dict) -> str:
    """给生成提示词用的一句话：本轮锁定的主题"""
    if lock.get("external_models"):
        return (f"本轮问题涉及 {'、'.join(lock['external_models'])} 等标识。"
                "标识可能是产品型号、系列前缀、文档编号或端子名称，请按用户问题区分含义。"
                "系列问题按原文适用范围回答，并说明不同型号后缀的适用条件。\n")
    if lock.get("models"):
        return f"本轮问题锁定的型号：{'、'.join(lock['models'])}（来源：{lock['source']}）。回答只针对这些型号，资料里其他型号的内容不要混进来。\n"
    if lock.get("series"):
        return f"本轮问题锁定的系列：{SERIES_CN.get(lock['series'], lock['series'])}。\n"
    return ""


def apply(plan: dict, lock: dict) -> dict:
    """把锁定结果写回检索计划：SQL 和向量检索都按它过滤"""
    if lock.get("external_models"):
        return {**plan, "route": "DESC", "models": [], "series": None,
                "external_models": lock["external_models"]}
    out = {**plan, "models": lock["models"] or plan["models"], "series": lock.get("series") or plan.get("series")}
    if lock["source"] in ("补全", "上文") and lock["models"] and plan["route"] == "DESC" and plan.get("param_keys"):
        out["route"] = "MIXED"                                 # 补全出了具体型号、又问到了参数：数值走 SQL
    return out

