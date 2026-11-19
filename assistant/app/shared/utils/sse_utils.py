"""
SSE 推送：每个会话一个线程安全队列。图在后台线程执行，节点通过 push_to_session 推送事件，
/stream/{session_id} 的异步生成器从队列取出并按 SSE 协议输出。
"""
import asyncio
import json
import queue
from typing import Any

from fastapi import Request


class SSEEvent:
    READY = "ready"
    PROGRESS = "progress"   # 节点进度
    DELTA = "delta"         # LLM 流式增量
    FINAL = "final"         # 最终答案、引用来源、图片
    ERROR = "error"
    CLOSE = "__close__"


_queues: dict[str, queue.Queue] = {}


def create_sse_queue(session_id: str) -> queue.Queue:
    q = queue.Queue()
    _queues[session_id] = q
    return q


def push_to_session(session_id: str, event: str, data: dict[str, Any]):
    q = _queues.get(session_id)
    if q is not None:
        q.put({"event": event, "data": data})


def close_session(session_id: str):
    push_to_session(session_id, SSEEvent.CLOSE, {})


def _pack(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def sse_generator(session_id: str, request: Request):
    q = _queues.get(session_id)
    if q is None:
        yield _pack(SSEEvent.ERROR, {"error": "会话不存在或已结束"})
        return
    loop = asyncio.get_running_loop()
    try:
        yield _pack(SSEEvent.READY, {})
        while True:
            if await request.is_disconnected():
                break
            try:
                msg = await loop.run_in_executor(None, q.get, True, 1.0)
            except queue.Empty:
                continue
            if msg["event"] == SSEEvent.CLOSE:
                break
            yield _pack(msg["event"], msg["data"])
    finally:
        _queues.pop(session_id, None)
