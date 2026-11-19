"""
RAG 查询日志（MongoDB 集合 kb_query_log）：每次问答一条，记下排查问题需要的全部过程。

  问题      原问题、改写后的问题、HyDE 假设答案、走了哪条路线、锁定的主题
  召回      三路融合后的候选（每条带：哪一路排第几、RRF 分数）
  精排      精排后留下的切片和分数
  答案      最终回答、引用（文档号、版本、章节、页码、切片号）、状态、降级、引用校验说明
  开销      各阶段耗时、token、提示词版本、上下文预算
  反馈      用户点赞 / 点踩、原因、正确答案（点了之后回写到这一条上）

为什么放 MongoDB：每条日志的结构不固定（召回条数、引用条数、降级说明都不一样），嵌套着放最自然，也不和业务表抢 MySQL。
Bad Case 池直接从这里筛点踩的记录，点开就能看到当时召回了什么、精排留下了什么、引用了哪个切片，回溯到原始 chunk。
"""
from datetime import datetime

from app.infra.persistence.history_repository import history_repository
from app.shared.runtime.logger import logger

_ready = {"ok": False}


def col():
    c = history_repository.col.database["kb_query_log"]
    if not _ready["ok"]:
        c.create_index("answer_id", unique=True)
        c.create_index([("username", 1), ("ts", -1)])
        c.create_index([("feedback.rating", 1), ("feedback.at", -1)])
        _ready["ok"] = True
    return c


def _hit(h: dict) -> dict:
    return {"chunk_id": h.get("chunk_id"), "doc_key": h.get("doc_key"), "version": h.get("version"), "section": h.get("section"),
            "page": h.get("page"), "routes": h.get("routes"), "rrf": round(h.get("rrf", 0.0), 5)}


def write(state: dict, answer_id: str, text: str, status: str) -> None:
    """问答结束时写一条。写失败只记日志，不影响回答"""
    try:
        plan, lock = state.get("plan") or {}, state.get("lock") or {}
        col().insert_one({
            "answer_id": answer_id, "trace_id": state.get("trace_id"), "session_id": state.get("session_id"), "username": state.get("username"),
            "scope_key": state["scope"].key() if state.get("scope") else None, "ts": datetime.now(),
            "question": state.get("question"), "rewritten": state.get("rewritten"), "hyde": state.get("hyde") or None,
            "route": plan.get("route"), "subject": {k: lock.get(k) for k in ("models", "series", "source")} if lock else None,
            "recall": [_hit(h) for h in (state.get("hits") or [])[:30]],
            "reranked": [{"chunk_id": d.get("chunk_id"), "doc_key": d.get("doc_key"), "section": d.get("section"), "score": round(float(d.get("score") or 0), 4)}
                         for d in state.get("docs") or []],
            "facts": [f.get("title") for f in state.get("facts") or []],
            "answer": text, "status": status,
            "citations": [{k: s.get(k) for k in ("index", "type", "doc_key", "version", "title", "section", "page", "chunk_id", "score")} for s in state.get("sources") or []],
            "degraded": state.get("degraded") or [], "verify_note": state.get("verify_note"), "cache_hit": bool(state.get("cache_hit")),
            "latency_ms": state.get("latency"), "tokens": state["usage"].as_dict() if state.get("usage") else None, "budget": state.get("pack"),
            "prompt_version": state.get("prompt_version"), "feedback": None})
    except Exception as e:
        logger.warning(f"[kbase] 写查询日志失败：{e}")


def set_feedback(answer_id: str, rating: int, reason: str | None, correct_answer: str | None, comment: str | None) -> None:
    col().update_one({"answer_id": answer_id}, {"$set": {"feedback": {"rating": rating, "reason": reason, "correct_answer": correct_answer,
                                                                      "comment": comment, "at": datetime.now()}}})


def bad_cases(limit: int = 100) -> list[dict]:
    """Bad Case 池：点踩的记录，最新的在前"""
    rows = col().find({"feedback.rating": {"$lt": 0}}, {"_id": 0}).sort("feedback.at", -1).limit(limit)
    return list(rows)
