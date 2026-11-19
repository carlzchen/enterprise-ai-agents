"""BGE-M3 向量化：同时产出稠密向量(1024 维)与稀疏向量(词权重)，模型单例懒加载。"""
import os
import threading

from pymilvus.model.hybrid import BGEM3EmbeddingFunction

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

_model: BGEM3EmbeddingFunction | None = None
_lock = threading.Lock()


def get_bge_m3() -> BGEM3EmbeddingFunction:
    global _model
    with _lock:
        if _model is None:
            name = settings.bge_m3_path if settings.bge_m3_path and os.path.exists(settings.bge_m3_path) else "BAAI/bge-m3"
            logger.info(f"加载 BGE-M3：{name}，device={settings.bge_device}")
            _model = BGEM3EmbeddingFunction(model_name=name, device=settings.bge_device,
                                            use_fp16=settings.bge_fp16, normalize_embeddings=True)
    return _model


def _sparse_rows(sparse, n: int) -> list[dict[int, float]]:
    """scipy CSR 稀疏矩阵 → 每行一个 {token_id: weight} 字典（Milvus SPARSE_FLOAT_VECTOR 格式）"""
    rows = []
    for i in range(n):
        start, end = sparse.indptr[i], sparse.indptr[i + 1]
        rows.append({int(k): float(v) for k, v in zip(sparse.indices[start:end], sparse.data[start:end])})
    return rows


def generate_embeddings(texts: list[str], batch_size: int = 8) -> dict[str, list]:
    if not texts or any(not isinstance(t, str) for t in texts):
        raise ValueError("texts 必须是非空字符串列表")
    model = get_bge_m3()
    dense, sparse = [], []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        out = model.encode_documents(batch)
        dense.extend(v.tolist() for v in out["dense"])
        sparse.extend(_sparse_rows(out["sparse"], len(batch)))
    return {"dense": dense, "sparse": sparse}
