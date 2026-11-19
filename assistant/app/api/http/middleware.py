"""
请求追踪中间件：X-Trace-Id 优先取请求头（前端 / ERP 后端传入，实现跨系统串联），否则生成；
请求内所有 loguru 日志自动带 trace_id，响应头回传同一个追踪号，并记录访问日志（慢请求 WARNING）。
"""
import re
import time
import uuid

from fastapi import FastAPI, Request

from app.shared.runtime.logger import logger

HEADER = "X-Trace-Id"
_SAFE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_SLOW_MS = 3000


def install_trace_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def trace(request: Request, call_next):
        incoming = request.headers.get(HEADER, "")
        trace_id = incoming if _SAFE.match(incoming) else uuid.uuid4().hex[:16]
        start = time.time()
        with logger.contextualize(trace_id=trace_id):
            try:
                response = await call_next(request)
            except Exception:
                logger.exception(f"[access] {request.method} {request.url.path} 未处理异常")
                raise
            ms = int((time.time() - start) * 1000)
            line = f"[access] {request.method} {request.url.path} -> {response.status_code} {ms}ms"
            if response.status_code >= 500:
                logger.error(line)
            elif ms >= _SLOW_MS and "/stream/" not in request.url.path:
                logger.warning(line + " (slow)")
            else:
                logger.info(line)
        response.headers[HEADER] = trace_id
        return response
