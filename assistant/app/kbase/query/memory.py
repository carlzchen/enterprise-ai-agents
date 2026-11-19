"""
多轮对话的上下文管理：最近几轮原样保留，更早的轮次压缩成一段摘要。
  - 改写追问、生成回答时：带上「摘要 + 最近 KEEP_TURNS 轮」，既不丢早先确定的型号和条件，也不让上下文越聊越长
  - 压缩放在回答之后的后台线程里做，不占用户等待的时间；下一轮提问时直接读现成的摘要
"""
import threading

from app.copilot import llm
from app.kbase.query import prompts
from app.infra.persistence.history_repository import history_repository
from app.shared.runtime.logger import logger

KEEP_TURNS = 3            # 最近几轮原样保留
COMPRESS_EVERY = 2        # 每多出几轮没进摘要的旧对话，就压缩一次

def _messages():
    return history_repository.col.database["kb_message"]


def _summaries():
    return history_repository.col.database["kb_session_summary"]


def load(session_id: str) -> tuple[list[dict], str]:
    """返回（最近几轮原文, 更早内容的摘要）"""
    rows = list(_messages().find({"session_id": session_id}).sort("ts", -1).limit(KEEP_TURNS * 2))
    history = [{"role": r["role"], "text": r["text"]} for r in reversed(rows)]
    doc = _summaries().find_one({"session_id": session_id}) if history else None
    return history, (doc or {}).get("summary", "")


def compress(session_id: str) -> str | None:
    """把最近 KEEP_TURNS 轮之前、还没进摘要的对话并入摘要。没有需要压缩的内容时返回 None"""
    doc = _summaries().find_one({"session_id": session_id}) or {}
    rows = list(_messages().find({"session_id": session_id}).sort("ts", 1))
    old = [r for r in rows[:-KEEP_TURNS * 2] if r["ts"] > doc.get("upto_ts", 0)] if len(rows) > KEEP_TURNS * 2 else []
    if len(old) < COMPRESS_EVERY * 2:
        return None
    dialogue = "\n".join(f"{'用户' if r['role'] == 'user' else '助手'}：{r['text'][:400]}" for r in old)
    summary = llm.invoke(prompts.text(prompts.SUMMARY).format(summary=doc.get("summary") or "（无）", dialogue=dialogue)).content.strip()[:400]
    _summaries().update_one({"session_id": session_id}, {"$set": {"summary": summary, "upto_ts": old[-1]["ts"], "turns": len(rows) // 2 - KEEP_TURNS}}, upsert=True)
    logger.info(f"[kbase] 会话 {session_id[:8]} 压缩了 {len(old) // 2} 轮历史：{summary[:60]}")
    return summary


def compress_later(session_id: str) -> None:
    def work():
        try:
            compress(session_id)
        except Exception:
            logger.exception("[kbase] 历史摘要压缩失败")
    threading.Thread(target=work, daemon=True, name="kb-summary").start()


def forget(session_id: str) -> None:
    _summaries().delete_many({"session_id": session_id})
