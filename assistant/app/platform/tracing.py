"""
链路追踪（Langfuse）：把一次问答的每一步记下来——改写成了什么、走了哪条路线、检索到哪些资料、重排分数、
发给大模型的提示词和它的回答、token 和耗时、有没有降级、用户有没有点踩。出了问题时，开发人员可以按请求回放。

它只负责「记录」，不参与业务逻辑：没有配置或 Langfuse 连不上时，这里的所有方法都什么也不做，问答照常。
降级本身是 app/platform/resilience.py 里的熔断器做的；这里只是把「在哪一步降级了、为什么」记进链路。

@observe：给函数加上它，调用时自动在当前链路下开一个 span（嵌套调用就是嵌套的 span），记录输入、输出、耗时和异常。
  一次问答的链路长这样：改写+HyDE → 召回（三路各一个子 span）→ 融合 → 重排 → 生成（generation，带 token 和提示词版本）
  当前链路放在上下文变量里（bind / unbind），跨线程时由 resilience.run_with_timeout 带过去
"""
import contextvars
import functools
import time

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

_client = None
_down_until = 0.0


def client():
    global _client, _down_until
    if not settings.langfuse_host or not settings.langfuse_public_key or time.time() < _down_until:
        return None
    if _client is None:
        try:
            from langfuse import Langfuse
            _client = Langfuse(public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key, host=settings.langfuse_host,
                               timeout=5, flush_at=20, flush_interval=3)
        except Exception as e:
            _down_until = time.time() + 300
            logger.warning(f"[trace] Langfuse 初始化失败，5 分钟内不再尝试：{str(e)[:120]}")
            return None
    return _client


def url(trace_id: str) -> str | None:
    if not settings.langfuse_host or not trace_id:
        return None
    return f"{settings.langfuse_public_url or settings.langfuse_host}/project/{settings.langfuse_project_id}/traces/{trace_id}"


def _short(v, limit: int = 4000):
    if isinstance(v, str):
        return v[:limit]
    if isinstance(v, (list, tuple)):
        return [_short(x, 600) for x in v[:20]]
    if isinstance(v, dict):
        return {k: _short(x, 1200) for k, x in list(v.items())[:30]}
    return v


class Trace:
    """一次请求的链路。所有方法都吞掉异常：追踪出问题不能影响问答"""

    def __init__(self, app: str, name: str, trace_id: str, user: str, session: str, question: str, metadata: dict | None = None):
        self.id, self._t, self._spans = trace_id, None, {}
        c = client()
        if c is None:
            return
        try:
            self._t = c.trace(id=trace_id, name=name, user_id=user, session_id=session, input=question, tags=[app], metadata=metadata or {})
        except Exception:
            self._t = None

    @property
    def on(self) -> bool:
        return self._t is not None

    def start(self, name: str, input=None) -> None:
        if self._t is not None:
            try:
                self._spans[name] = self._t.span(name=name, input=_short(input))
            except Exception:
                pass

    def end(self, name: str, output=None, warning: str | None = None) -> None:
        span = self._spans.pop(name, None)
        if span is not None:
            try:
                span.end(output=_short(output), level="WARNING" if warning else "DEFAULT", status_message=warning)
            except Exception:
                pass

    def generation(self, name: str, prompt, answer: str, usage: dict | None, seconds: float, model: str | None = None) -> None:
        if self._t is not None:
            try:
                self._t.generation(name=name, model=model or settings.llm_model, input=_short(prompt, 12000), output=_short(answer, 8000),
                                   usage={"input": (usage or {}).get("input", 0), "output": (usage or {}).get("output", 0)}, metadata={"seconds": round(seconds, 2)})
            except Exception:
                pass

    # ---------- 嵌套 span（@observe 用） ----------

    def child(self, parent, name: str, input=None):
        if self._t is None:
            return None
        try:
            return (parent or self._t).span(name=name, input=_short(input))
        except Exception:
            return None

    def close(self, span, output=None, seconds: float = 0.0, error: str | None = None) -> None:
        if span is not None:
            try:
                span.end(output=_short(output), metadata={"seconds": round(seconds, 3)}, level="ERROR" if error else "DEFAULT", status_message=error)
            except Exception:
                pass

    def child_generation(self, parent, name: str, prompt, answer: str, usage: dict | None, seconds: float, model: str | None = None,
                         metadata: dict | None = None) -> None:
        if self._t is not None:
            try:
                (parent or self._t).generation(name=name, model=model or settings.llm_model, input=_short(prompt, 12000), output=_short(answer, 8000),
                                               usage={"input": (usage or {}).get("input", 0), "output": (usage or {}).get("output", 0)},
                                               metadata={"seconds": round(seconds, 2), **(metadata or {})})
            except Exception:
                pass

    def event(self, name: str, detail=None, warning: bool = False) -> None:
        if self._t is not None:
            try:
                self._t.event(name=name, metadata=_short(detail), level="WARNING" if warning else "DEFAULT")
            except Exception:
                pass

    def finish(self, output: str, tags: list[str], metadata: dict) -> None:
        if self._t is not None:
            try:
                self._t.update(output=_short(output, 8000), tags=[t for t in tags if t], metadata=_short(metadata))
            except Exception:
                pass


def score(trace_id: str, name: str, value: float, comment: str | None = None) -> None:
    """把用户反馈或评测分数挂到对应的链路上"""
    c = client()
    if c is not None and trace_id:
        try:
            c.score(trace_id=trace_id, name=name, value=value, comment=comment)
        except Exception:
            pass


# ---------------------------------------------------------------- @observe

_current: contextvars.ContextVar = contextvars.ContextVar("kb_trace", default=None)      # (Trace, 当前 span)


def bind(trace: "Trace"):
    return _current.set((trace, None))


def unbind(token) -> None:
    _current.reset(token)


def current() -> tuple:
    return _current.get() or (None, None)


def _arg_view(args, kwargs) -> dict:
    out = {}
    for i, a in enumerate(args[:3]):
        if isinstance(a, (str, int, float, bool)) or a is None:
            out[f"arg{i}"] = a
        elif isinstance(a, (list, tuple)):
            out[f"arg{i}"] = f"<{len(a)} 项>"
    out.update({k: v for k, v in kwargs.items() if isinstance(v, (str, int, float, bool)) or v is None})
    return out


def observe(name: str | None = None, output=None, input=None):
    """output / input：把返回值、参数转成适合记录的样子（默认参数只记简单类型，返回值原样截断）"""
    def deco(fn):
        span_name = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            trace, parent = current()
            if trace is None or not trace.on:
                return fn(*args, **kwargs)
            span = trace.child(parent, span_name, input(*args, **kwargs) if input else _arg_view(args, kwargs))
            token = _current.set((trace, span))
            t = time.time()
            try:
                out = fn(*args, **kwargs)
            except Exception as e:
                trace.close(span, None, time.time() - t, f"{type(e).__name__}: {str(e)[:200]}")
                raise
            finally:
                _current.reset(token)
            try:
                view = output(out) if output else out
            except Exception:
                view = None
            trace.close(span, view, time.time() - t)
            return out
        return wrapper
    return deco


def generation(name: str, prompt, answer: str, usage: dict | None, seconds: float, metadata: dict | None = None) -> None:
    """在当前 span 下记一次大模型调用"""
    trace, parent = current()
    if trace is not None:
        trace.child_generation(parent, name, prompt, answer, usage, seconds, metadata=metadata)
