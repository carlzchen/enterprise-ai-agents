"""
语义检索：三类知识库
  制度文档库  copilot_policy    制度原文切片（见 policy.py）
  数据语义库  copilot_semantic  kind=view   视图和字段说明
  业务口径库  copilot_semantic  kind=metric 指标定义；kind=template 分析模板
检索 = 字面匹配（指标名、别名、示例问法）+ 向量检索（BGE-M3 稠密 + 稀疏，Milvus），两路用 RRF 融合。
向量库不可用时自动退化为只用字面匹配，问数主流程不受影响。
"""
import threading

from pymilvus import DataType

from app.copilot.semantic.loader import semantic
from app.infra.llm.providers import llm_providers
from app.infra.vector_store.milvus_gateway import DENSE_DIM, milvus_gateway
from app.shared.runtime.logger import logger

COLLECTION = "copilot_semantic"
_lock = threading.Lock()
_ready = False
_vector_ok = False


def _docs() -> list[dict]:
    docs = []
    for t in semantic.templates.values():
        docs.append({"kind": "template", "key": t.id,
                     "text": f"{t.name}。{t.desc}。指标：{'、'.join(t.metrics)}。常见问法：{'；'.join(t.examples)}"})
    for m in semantic.metrics:
        docs.append({"kind": "metric", "key": m.name, "text": f"{m.name}（{'、'.join(m.aliases)}）：{m.formula}"})
    for v in semantic.views.values():
        cols = "、".join(d.split("，")[0] for d in v.columns.values() if d)
        docs.append({"kind": "view", "key": v.name, "text": f"{v.title}：{v.desc} 字段：{cols}。常见问法：{'；'.join(v.questions)}"})
    return docs


def ensure_index(force: bool = False) -> bool:
    """建立（或重建）语义向量索引。内容很少（一百条以内），每次启动重建，保证和 YAML 一致"""
    global _ready, _vector_ok
    with _lock:
        if _ready and not force:
            return _vector_ok
        try:
            c = milvus_gateway.client
            if c.has_collection(COLLECTION):
                c.drop_collection(COLLECTION)
            schema = c.create_schema(auto_id=True, enable_dynamic_field=False)
            schema.add_field("pk", DataType.INT64, is_primary=True, auto_id=True)
            schema.add_field("kind", DataType.VARCHAR, max_length=16)
            schema.add_field("key", DataType.VARCHAR, max_length=64)
            schema.add_field("text", DataType.VARCHAR, max_length=4000)
            schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
            idx = c.prepare_index_params()
            milvus_gateway._vector_indexes(idx)
            idx.add_index(field_name="kind", index_type="INVERTED")
            c.create_collection(COLLECTION, schema=schema, index_params=idx)
            docs = _docs()
            emb = llm_providers.generate_embeddings([d["text"] for d in docs])
            c.insert(COLLECTION, [{**d, "text": d["text"][:3900], "dense_vector": emb["dense"][i], "sparse_vector": emb["sparse"][i]}
                                  for i, d in enumerate(docs)])
            c.load_collection(COLLECTION)
            _vector_ok = True
            logger.info(f"语义向量索引已建立：{len(docs)} 条")
        except Exception as e:
            _vector_ok = False
            logger.warning(f"语义向量索引不可用，退化为字面匹配：{e}")
        _ready = True
        return _vector_ok


def _bigrams(s: str) -> set[str]:
    s = "".join(ch for ch in s if not ch.isspace())
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def _lexical(query: str, kind: str) -> list[str]:
    q = _bigrams(query)
    scored = []
    for d in _docs():
        if d["kind"] != kind:
            continue
        score = len(q & _bigrams(d["text"])) / (len(q) or 1)
        if kind == "template":                                   # 指标名或别名直接出现在问题里的模板加分
            t = semantic.templates[d["key"]]
            for name in t.metrics:
                m = semantic.metric(name)
                if m and any(k in query for k in [m.name, *m.aliases]):
                    score += 0.5
        elif kind == "metric":
            m = semantic.metric(d["key"])
            if m and any(k in query for k in [m.name, *m.aliases]):
                score += 1.0
        scored.append((score, d["key"]))
    return [k for s, k in sorted(scored, reverse=True) if s > 0]


def _vector(query: str, kind: str, limit: int) -> list[str]:
    if not ensure_index():
        return []
    try:
        emb = llm_providers.generate_embeddings([query])
        reqs = milvus_gateway.build_requests(emb["dense"][0], emb["sparse"][0], expr=f'kind == "{kind}"', limit=limit)
        hits = milvus_gateway.hybrid_search(COLLECTION, reqs, weights=(0.6, 0.4), limit=limit, output_fields=["key"])
        return [h["entity"]["key"] for h in hits]
    except Exception as e:
        logger.warning(f"语义向量检索失败，本次只用字面匹配：{e}")
        return []


def search(query: str, kind: str, top_k: int = 6, use_vector: bool = True) -> list[str]:
    """返回最相关的 key 列表（模板编号 / 指标名 / 视图名）"""
    lists = [_lexical(query, kind)]
    if use_vector:
        lists.append(_vector(query, kind, top_k * 2))
    score: dict[str, float] = {}
    for keys in lists:                                            # RRF 融合
        for rank, key in enumerate(keys):
            score[key] = score.get(key, 0) + 1 / (60 + rank)
    return [k for k, _ in sorted(score.items(), key=lambda x: -x[1])][:top_k]
