"""
大模型调用的统一入口（问数和知识库共用）：
  - 记录每次调用的 Token 用量，方便做成本统计和优化前后对比
  - 并发闸门：同时打到模型服务的请求数有上限，超了排队，排不上就告诉调用方「不可用」
  - 熔断：主模型连续失败就断开一段时间；配置了备用模型时自动切过去，没有配置则由业务流程自己降级
  - 模型服务换成自建 vLLM（Qwen3）只改配置：地址、模型名、是否关闭思考模式
"""
import json
import re
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.infra.config.settings import settings
from app.platform import metrics
from app.platform import resilience as R

BREAKER = R.Breaker("llm", "大模型服务", fail_max=3, reset_seconds=30)
FALLBACK = R.Breaker("llm_fallback", "备用大模型", fail_max=3, reset_seconds=30)
POOL = R.Bulkhead("llm", "大模型服务", limit=settings.llm_concurrency, queue=settings.llm_concurrency * 2, wait_seconds=20)


def _extra() -> dict:
    try:
        return json.loads(settings.llm_extra_body) if settings.llm_extra_body else {}
    except ValueError:
        return {}


@lru_cache(maxsize=8)
def chat(json_mode: bool = False, fallback: bool = False) -> ChatOpenAI:
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    base, key, model = ((settings.llm_fallback_base_url, settings.llm_fallback_api_key, settings.llm_fallback_model) if fallback
                        else (settings.llm_base_url, settings.llm_api_key, settings.llm_model))
    extra = _extra()
    return ChatOpenAI(model=model, base_url=base, api_key=key, temperature=0, timeout=settings.llm_timeout, max_retries=1, stream_usage=True,
                      model_kwargs=kwargs, **({"extra_body": extra} if extra else {}))


def _guarded(app: str, primary, backup):
    """先走主模型；主模型不可用且配置了备用模型时走备用。都不行就抛 R.Unavailable，由业务流程降级"""
    with POOL.enter():
        try:
            out = BREAKER.call(primary)
            metrics.LLM_CALLS.labels(app, "ok").inc()
            return out, False
        except R.Unavailable as e:
            metrics.LLM_CALLS.labels(app, "fail").inc()
            if not settings.llm_fallback_model:
                raise
            out = FALLBACK.call(backup)
            metrics.LLM_CALLS.labels(app, "fallback").inc()
            metrics.DEGRADED.labels(app, "fallback_model").inc()
            return out, True


class Usage:
    """一次请求内累计的 Token 和模型调用次数"""

    def __init__(self):
        self.input = self.output = self.calls = 0
        self.first_token_ms: int | None = None      # 第一段回答文字推给用户的时间
        self.fallback = False                       # 这次请求里有没有用到备用模型

    def add(self, message) -> None:
        meta = getattr(message, "usage_metadata", None) or {}
        self.input += int(meta.get("input_tokens", 0))
        self.output += int(meta.get("output_tokens", 0))
        self.calls += 1

    @property
    def total(self) -> int:
        return self.input + self.output

    def as_dict(self) -> dict:
        return {"input": self.input, "output": self.output, "total": self.total, "calls": self.calls}


def invoke(prompt, usage: Usage | None = None, json_mode: bool = False, tools: list | None = None, tool_choice=None, app: str = "copilot"):
    def run(fallback: bool):
        model = chat(json_mode, fallback)
        if tools:
            model = model.bind_tools(tools, tool_choice=tool_choice) if tool_choice else model.bind_tools(tools)
        return model.invoke(prompt)
    msg, used_backup = _guarded(app, lambda: run(False), lambda: run(True))
    if usage is not None:
        usage.add(msg)
        usage.fallback = usage.fallback or used_backup
    return msg


def stream(prompt, on_delta, usage: Usage | None = None, app: str = "copilot") -> str:
    """流式生成：每拿到一段就回调 on_delta，返回完整文本"""
    def run(fallback: bool):
        parts, last = [], None
        for chunk in chat(False, fallback).stream(prompt):
            last = chunk if last is None else last + chunk
            if chunk.content:
                parts.append(chunk.content)
                on_delta(chunk.content)
        return "".join(parts), last
    (text, last), used_backup = _guarded(app, lambda: run(False), lambda: run(True))
    if usage is not None and last is not None:
        usage.add(last)
        usage.fallback = usage.fallback or used_backup
    return text


def strip_fence(text: str) -> str:
    """去掉模型偶尔加上的 ```json 围栏"""
    return re.sub(r"^```(?:json|sql)?\s*|\s*```$", "", text.strip())
