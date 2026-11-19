from functools import lru_cache

import httpx

from app.infra.config.settings import settings
from app.shared.model.embedding_utils import generate_embeddings, get_bge_m3
from app.shared.model.lm_utils import get_llm, get_vision_llm
from app.shared.model.reranker_utils import get_reranker


@lru_cache(maxsize=1)
def _http() -> httpx.Client:
    return httpx.Client(base_url=settings.model_server_url.rstrip("/"), timeout=60, trust_env=False)


class LLMProvider:
    """模型统一入口：业务代码只依赖这里，便于替换模型或在测试中打桩"""

    def chat(self, json_mode: bool = False, temperature: float | None = None):
        return get_llm(json_mode, temperature)

    def vision_chat(self):
        return get_vision_llm()

    def generate_embeddings(self, texts: list[str]) -> dict[str, list]:
        """配置了独立的模型服务（MODEL_SERVER_URL，一般在 GPU 机器上）就调它，否则在本进程里算"""
        if settings.model_server_url:
            out = {"dense": [], "sparse": []}
            for i in range(0, len(texts), 32):
                r = _http().post("/embed", json={"texts": texts[i:i + 32]})
                r.raise_for_status()
                out["dense"] += r.json()["dense"]
                out["sparse"] += [{int(k): v for k, v in x.items()} for x in r.json()["sparse"]]
            return out
        return generate_embeddings(texts)

    def rerank_scores(self, query: str, texts: list[str]) -> list[float]:
        """交叉编码器给（问题, 文本）打分，0 到 1。文本超长时按模型的输入上限截断"""
        if not texts:
            return []
        if settings.model_server_url:
            r = _http().post("/rerank", json={"query": query, "texts": texts})
            r.raise_for_status()
            return r.json()["scores"]
        model = get_reranker()
        tok = model.tokenizer
        budget = max(64, 512 - 8 - len(tok.encode(query, add_special_tokens=False)))

        def fit(t: str) -> str:
            ids = tok.encode(t, add_special_tokens=False)
            return t if len(ids) <= budget else tok.decode(ids[:budget])
        scores = model.compute_score([[query, fit(t)] for t in texts], normalize=True)
        return [float(x) for x in (scores if isinstance(scores, list) else [scores])]

    def bge_m3(self):
        return get_bge_m3()

    def reranker(self):
        return get_reranker()


llm_providers = LLMProvider()
