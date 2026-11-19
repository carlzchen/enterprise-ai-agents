"""
向量模型和重排模型的独立服务：python -m app.platform.model_server（默认端口 8097）

为什么要单独拆出来：这两个模型吃显存、吃算力，和业务服务放在一起时没法单独扩容。
拆出来放到 GPU 机器上之后，问数、知识库、入库任务都通过 HTTP 调它（配置 MODEL_SERVER_URL），
业务服务本身就不用加载模型了，可以起很多个实例。

  POST /embed    {"texts": [...]}                → {"dense": [[...]], "sparse": [{词 id: 权重}]}（BGE-M3 同时出稠密和稀疏）
  POST /rerank   {"query": "...", "texts": [...]} → {"scores": [...]}（bge-reranker-v2-m3，0 到 1）
设备和精度由环境变量控制：BGE_DEVICE / BGE_RERANKER_DEVICE 设为 cuda，BGE_FP16 / BGE_RERANKER_FP16 设为 true。
"""
import os
import threading
import time

from fastapi import FastAPI
from prometheus_client import Histogram
from pydantic import BaseModel, Field

os.environ.pop("MODEL_SERVER_URL", None)                    # 这个进程自己就是模型服务，不能再转发给别人

from app.infra.llm.providers import llm_providers  # noqa: E402
from app.platform import metrics  # noqa: E402

app = FastAPI(title="向量与重排模型服务")
_lock = threading.Lock()
SECONDS = Histogram("model_server_seconds", "模型推理耗时", ["op"], buckets=(0.01, 0.03, 0.1, 0.3, 1, 3, 10))
metrics.install(app)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)


class RerankRequest(BaseModel):
    query: str
    texts: list[str] = Field(max_length=200)


@app.post("/embed")
def embed(req: EmbedRequest):
    t = time.time()
    with _lock:
        e = llm_providers.generate_embeddings(req.texts)
    SECONDS.labels("embed").observe(time.time() - t)
    return {"dense": [list(map(float, v)) for v in e["dense"]], "sparse": [{str(k): float(v) for k, v in s.items()} for s in e["sparse"]]}


@app.post("/rerank")
def rerank(req: RerankRequest):
    t = time.time()
    with _lock:
        scores = llm_providers.rerank_scores(req.query, req.texts)
    SECONDS.labels("rerank").observe(time.time() - t)
    return {"scores": scores}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("MODEL_SERVER_PORT", "8097")))
