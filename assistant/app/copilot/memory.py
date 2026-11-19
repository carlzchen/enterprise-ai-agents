"""
对话记忆（Redis）。每个会话存四样东西，2 小时不活动自动过期：
  turns          最近几轮对话原文
  summary        更早对话的摘要（上下文压缩：超过 4 轮的部分压成一小段，避免提示词越来越长）
  last_call      上一轮的工具调用和参数（多轮追问靠它继承条件）
  last_result_id 上一轮结果的编号（换图表、导出时用）
查询结果也缓存在 Redis：键里带角色和行级范围，不同权限的人不会命中同一份缓存。
"""
import hashlib
import json
import threading

import redis

from app.copilot import llm
from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

TTL = 2 * 3600
KEEP_TURNS = 8            # 4 轮问答
CACHE_TTL = 600
RESULT_TTL = 3600
_client = None
_lock = threading.Lock()


def r() -> redis.Redis:
    global _client
    with _lock:
        if _client is None:
            _client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=3)
    return _client


def _key(session_id: str) -> str:
    return f"copilot:mem:{session_id}"


def load(session_id: str) -> dict:
    try:
        raw = r().get(_key(session_id))
        return json.loads(raw) if raw else {}
    except Exception as e:                       # 记忆读不到时当作新会话，不影响回答
        logger.warning(f"读取对话记忆失败：{e}")
        return {}


def save(session_id: str, mem: dict) -> None:
    try:
        r().setex(_key(session_id), TTL, json.dumps(mem, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning(f"写入对话记忆失败：{e}")


def clear(session_id: str) -> None:
    try:
        r().delete(_key(session_id))
    except Exception:
        pass


def append_turn(session_id: str, mem: dict, question: str, answer: str, call: dict | None, result_id: str | None) -> dict:
    turns = mem.get("turns", []) + [{"role": "user", "text": question}, {"role": "assistant", "text": answer[:400]}]
    mem = {**mem, "turns": turns}
    if call is not None:
        mem["last_call"] = call
    if result_id:
        mem["last_result_id"] = result_id
    save(session_id, mem)
    if len(turns) > KEEP_TURNS:
        threading.Thread(target=_compress, args=(session_id,), daemon=True).start()   # 压缩放后台，不拖慢本次回答
    return mem


def _compress(session_id: str) -> None:
    mem = load(session_id)
    turns = mem.get("turns", [])
    if len(turns) <= KEEP_TURNS:
        return
    old, keep = turns[:-KEEP_TURNS], turns[-KEEP_TURNS:]
    text = "\n".join(f"{'用户' if t['role'] == 'user' else '助手'}：{t['text']}" for t in old)
    try:
        msg = llm.invoke(f"把下面的对话压缩成不超过 80 字的摘要，保留查过的指标、对象、时间范围和关键数字：\n"
                         f"已有摘要：{mem.get('summary', '无')}\n{text}")
        mem["summary"], mem["turns"] = (msg.content or "").strip()[:200], keep
        save(session_id, mem)
    except Exception as e:
        logger.warning(f"对话压缩失败：{e}")


# ---------- 查询结果缓存 ----------

def cache_key(scope_key: str, sql: str) -> str:
    return f"copilot:sql:{scope_key}:{hashlib.md5(sql.encode('utf-8')).hexdigest()}"


def cache_get(scope_key: str, sql: str):
    try:
        raw = r().get(cache_key(scope_key, sql))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def cache_set(scope_key: str, sql: str, columns: list, rows: list) -> None:
    try:
        r().setex(cache_key(scope_key, sql), CACHE_TTL, json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, default=str))
    except Exception:
        pass


# ---------- 结果暂存（换图表、导出用） ----------

def result_put(result_id: str, payload: dict) -> None:
    try:
        r().setex(f"copilot:result:{result_id}", RESULT_TTL, json.dumps(payload, ensure_ascii=False, default=str))
    except Exception as e:
        logger.warning(f"暂存查询结果失败：{e}")


def result_get(result_id: str) -> dict | None:
    try:
        raw = r().get(f"copilot:result:{result_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


# ---------- 意图识别结果缓存 ----------

def route_get(key: str) -> dict | None:
    try:
        raw = r().get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def route_set(key: str, call: dict) -> None:
    try:
        r().setex(key, CACHE_TTL, json.dumps(call, ensure_ascii=False, default=str))
    except Exception:
        pass


# ---------- 回答缓存：同一个人群范围、同一份数据、同一个问题，10 分钟内回答是一样的 ----------

def answer_key(kind: str, scope_key: str, *parts: str) -> str:
    return f"copilot:ans:{kind}:" + hashlib.md5("|".join([scope_key, *parts]).encode("utf-8")).hexdigest()


def answer_get(key: str) -> str | None:
    try:
        return r().get(key)
    except Exception:
        return None


def answer_set(key: str, text: str) -> None:
    try:
        r().setex(key, CACHE_TTL, text)
    except Exception:
        pass


def answer_clear(kind: str) -> None:
    """制度文档更新后，清掉制度问答的回答缓存"""
    try:
        for k in r().scan_iter(f"copilot:ans:{kind}:*", count=500):
            r().delete(k)
    except Exception:
        pass
