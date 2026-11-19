"""bge-reranker-v2-m3 交叉编码器重排序，单例懒加载。"""
import os
import threading

from FlagEmbedding import FlagReranker

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

_model: FlagReranker | None = None
_lock = threading.Lock()


def get_reranker() -> FlagReranker:
    global _model
    with _lock:
        if _model is None:
            name = settings.reranker_path if settings.reranker_path and os.path.exists(settings.reranker_path) else "BAAI/bge-reranker-v2-m3"
            logger.info(f"加载重排序模型：{name}，device={settings.reranker_device}")
            _model = FlagReranker(name, device=settings.reranker_device, use_fp16=settings.reranker_fp16)
    return _model
