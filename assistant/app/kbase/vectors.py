"""
知识库的向量索引（Milvus 集合 kb_chunks_v3）。它是 SQL 的派生索引：
  - 主键 chunk_id 由 SQL 那边算好传进来，写入用 upsert，重复导入不会产生重复数据
  - 三路检索都在这一个集合里：dense_vector（BGE-M3 稠密）、sparse_vector（BGE-M3 稀疏，消融对比用）、
    bm25_vector（Milvus 内置 BM25，content 字段用中文分词器，入库时自动生成，不用另起 Elasticsearch）
  - 元数据：doc_key、version、page、acl、level（数字密级）、series / item_name（主题）、state（PENDING / VALID / INVALID）
  - 检索前置过滤：权限（acl + level）和 state == "VALID" 都在表达式里，先过滤再取 TopK
  - 状态切换只改 state 等标量字段（partial upsert），不重写向量
"""
import threading

from pymilvus import DataType, Function, FunctionType

from app.infra.config.settings import settings
from app.infra.vector_store.milvus_gateway import DENSE_DIM, escape, milvus_gateway
from app.shared.runtime.logger import logger

FIELDS = ["chunk_id", "doc_key", "version", "doc_type", "title", "section", "page", "item_name", "series", "parent_id",
          "security_level", "level", "state", "content"]
ANALYZER = {"type": "chinese"}
VALID = 'state == "VALID"'
_lock = threading.Lock()
_ready = False


def name() -> str:
    return settings.kbase_collection


def schema():
    c = milvus_gateway.client
    s = c.create_schema(auto_id=False, enable_dynamic_field=False)
    s.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=32)
    s.add_field("doc_key", DataType.VARCHAR, max_length=80)
    s.add_field("version", DataType.VARCHAR, max_length=16)
    s.add_field("doc_type", DataType.VARCHAR, max_length=20)
    s.add_field("title", DataType.VARCHAR, max_length=400)
    s.add_field("section", DataType.VARCHAR, max_length=400)
    s.add_field("page", DataType.INT32)
    s.add_field("item_name", DataType.VARCHAR, max_length=200)
    s.add_field("series", DataType.VARCHAR, max_length=30)
    s.add_field("parent_id", DataType.VARCHAR, max_length=32)
    s.add_field("security_level", DataType.VARCHAR, max_length=12)
    s.add_field("level", DataType.INT8)
    s.add_field("acl", DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=32, max_length=64)
    s.add_field("state", DataType.VARCHAR, max_length=10)
    s.add_field("content", DataType.VARCHAR, max_length=8000, enable_analyzer=True, analyzer_params=ANALYZER)
    s.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
    s.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    s.add_field("bm25_vector", DataType.SPARSE_FLOAT_VECTOR)
    s.add_function(Function(name="content_bm25", function_type=FunctionType.BM25, input_field_names=["content"], output_field_names=["bm25_vector"]))
    return s


def ensure() -> None:
    global _ready
    with _lock:
        if _ready:
            return
        c = milvus_gateway.client
        if not c.has_collection(name()):
            idx = c.prepare_index_params()
            milvus_gateway._vector_indexes(idx)
            idx.add_index(field_name="bm25_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
                          params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75})
            for f in ("doc_key", "item_name", "series", "state", "acl", "level"):
                idx.add_index(field_name=f, index_type="INVERTED")
            c.create_collection(name(), schema=schema(), index_params=idx)
            logger.info(f"已创建集合 {name()}")
        c.load_collection(name())
        _ready = True


def upsert(rows: list[dict]) -> int:
    ensure()
    for i in range(0, len(rows), 200):
        milvus_gateway.client.upsert(collection_name=name(), data=rows[i:i + 200])
    return len(rows)


def update_fields(rows: list[dict]) -> int:
    """只改标量字段（状态、页码、章节等），向量和 BM25 不动。rows 里每条必须带 chunk_id。
    Milvus 要求同一批部分更新的字段集合完全一样，所以按字段集合分组发"""
    ensure()
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r)), []).append(r)
    for batch in groups.values():
        for i in range(0, len(batch), 500):
            milvus_gateway.client.upsert(collection_name=name(), data=batch[i:i + 500], partial_update=True)
    return len(rows)


def _ids_expr(chunk_ids: list[str]) -> str:
    return "chunk_id in [" + ", ".join(f'"{escape(i)}"' for i in chunk_ids) + "]"


def ids_of(doc_key: str, states: tuple[str, ...] | None = None) -> dict[str, str]:
    """这份文档在 Milvus 里实际有哪些切片 → {chunk_id: state}（强一致读，刚写完也能读到）"""
    ensure()
    expr = f'doc_key == "{escape(doc_key)}"'
    if states:
        expr += " and state in [" + ", ".join(f'"{s}"' for s in states) + "]"
    rows = milvus_gateway.client.query(name(), filter=expr, output_fields=["chunk_id", "state"], limit=16384, consistency_level="Strong")
    return {r["chunk_id"]: r["state"] for r in rows}


def fetch(chunk_ids: list[str], fields: list[str]) -> dict[str, dict]:
    ensure()
    out = {}
    for i in range(0, len(chunk_ids), 500):
        part = chunk_ids[i:i + 500]
        rows = milvus_gateway.client.query(name(), filter=_ids_expr(part), output_fields=["chunk_id", *fields], consistency_level="Strong")
        out.update({r["chunk_id"]: r for r in rows})
    return out


def contents_of(chunk_ids: list[str]) -> dict[str, str]:
    return {k: v["content"] for k, v in fetch(chunk_ids, ["content"]).items()} if chunk_ids else {}


def delete_ids(chunk_ids: list[str]) -> None:
    ensure()
    for i in range(0, len(chunk_ids), 500):
        milvus_gateway.client.delete(collection_name=name(), ids=chunk_ids[i:i + 500])


def delete_doc(doc_key: str) -> None:
    ensure()
    milvus_gateway.client.delete(collection_name=name(), filter=f'doc_key == "{escape(doc_key)}"')


def _and(*parts: str | None) -> str:
    return " and ".join(f"({p})" for p in parts if p)


def search(vector, field: str, expr: str | None, limit: int, valid_only: bool = True) -> list[dict]:
    """单路检索。field：dense_vector（传向量）/ sparse_vector（传稀疏字典）/ bm25_vector（直接传问题文本，Milvus 自己分词）"""
    ensure()
    param = {"dense_vector": {"metric_type": "COSINE", "params": {"ef": 128}}, "sparse_vector": {"metric_type": "IP"},
             "bm25_vector": {"metric_type": "BM25"}}[field]
    res = milvus_gateway.client.search(collection_name=name(), data=[vector], anns_field=field, search_params=param, limit=limit,
                                       filter=_and(VALID if valid_only else None, expr), output_fields=FIELDS)
    return [{**h["entity"], "score": float(h["distance"])} for h in (res[0] if res else [])]


def single_search(vector, field: str, expr: str | None, limit: int) -> list[dict]:
    """兼容旧调用（故障注入自测替换的是这个函数）"""
    return search(vector, field, expr, limit)
