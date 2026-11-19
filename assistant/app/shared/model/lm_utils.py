"""大语言模型 / 视觉模型客户端（OpenAI 兼容协议：DeepSeek、DashScope）"""
from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.infra.config.settings import settings


@lru_cache(maxsize=8)
def get_llm(json_mode: bool = False, temperature: float | None = None) -> ChatOpenAI:
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    return ChatOpenAI(model=settings.llm_model, base_url=settings.llm_base_url, api_key=settings.llm_api_key,
                      temperature=settings.llm_temperature if temperature is None else temperature,
                      timeout=60, max_retries=2, model_kwargs=kwargs)


@lru_cache(maxsize=2)
def get_vision_llm() -> ChatOpenAI:
    return ChatOpenAI(model=settings.vl_model, base_url=settings.vl_base_url, api_key=settings.vl_api_key,
                      temperature=0.1, timeout=90, max_retries=2)
