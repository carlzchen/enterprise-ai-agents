"""
Milvus 访问层：两个集合
  chunks      —— 知识切片（子块）：稠密 + 稀疏向量、主体 item_name、父文档 parent_id 等元数据
  item_names  —— 主体名称索引：查询时把用户口语化的型号/主体映射到库中标准主体
索引：稠密 HNSW(COSINE)，稀疏 SPARSE_INVERTED_INDEX(IP)，常用过滤字段 VARCHAR 建 INVERTED 标量索引。
"""
import threading

from pymilvus import AnnSearchRequest, DataType, MilvusClient, WeightedRanker

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

DENSE_DIM = 1024
CHUNK_OUTPUT_FIELDS = ["chunk_id", "doc_key", "file_title", "item_name", "title", "parent_title", "part",
                       "parent_id", "doc_type", "revision", "source", "content"]


def escape(value: str) -> str:
    """转义过滤表达式中的字符串字面量，防止表达式注入"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def in_expr(field: str, values: list[str]) -> str:
    return f'{field} in [{", ".join(chr(34) + escape(v) + chr(34) for v in values)}]'


class MilvusGateway:
    def __init__(self):
        self._client: MilvusClient | None = None
        self._lock = threading.Lock()
        self.chunk_collection = settings.chunks_collection
        self.item_name_collection = settings.item_name_collection

    @property
    def client(self) -> MilvusClient:
        with self._lock:
            if self._client is None:
                self._client = MilvusClient(uri=settings.milvus_url)
                logger.info(f"Milvus 已连接：{settings.milvus_url}")
        return self._client

    def reset(self) -> None:
        """丢掉当前连接，下次使用时重新连。Milvus 重启或网络断过之后，旧连接是「已关闭」状态，不会自己恢复"""
        with self._lock:
            old, self._client = self._client, None
        try:
            if old is not None:
                old.close()
        except Exception:
            pass

    # ---------- 集合 ----------

    def _vector_indexes(self, index_params):
        index_params.add_index(field_name="dense_vector", index_type="HNSW", metric_type="COSINE",
                               params={"M": 32, "efConstruction": 200})
        index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP",
                               params={"inverted_index_algo": "DAAT_MAXSCORE"})

    def ensure_chunk_collection(self):
        c = self.client
        if c.has_collection(self.chunk_collection):
            return
        schema = c.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("chunk_id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("doc_key", DataType.VARCHAR, max_length=128)       # 文档唯一键：PLM 文档编号或上传文件键
        schema.add_field("file_title", DataType.VARCHAR, max_length=512)
        schema.add_field("item_name", DataType.VARCHAR, max_length=256)     # 主体：产品型号 / 产品系列 / 物料类别 / 手册主体
        schema.add_field("title", DataType.VARCHAR, max_length=512)
        schema.add_field("parent_title", DataType.VARCHAR, max_length=512)
        schema.add_field("part", DataType.INT16)
        schema.add_field("parent_id", DataType.INT64)                        # MySQL parent_document.id
        schema.add_field("doc_type", DataType.VARCHAR, max_length=32)
        schema.add_field("revision", DataType.VARCHAR, max_length=16)
        schema.add_field("source", DataType.VARCHAR, max_length=32)         # PLM / UPLOAD
        schema.add_field("content", DataType.VARCHAR, max_length=65535)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        idx = c.prepare_index_params()
        self._vector_indexes(idx)
        idx.add_index(field_name="item_name", index_type="INVERTED")
        idx.add_index(field_name="doc_key", index_type="INVERTED")
        c.create_collection(self.chunk_collection, schema=schema, index_params=idx)
        logger.info(f"已创建集合 {self.chunk_collection}")

    def ensure_item_name_collection(self):
        c = self.client
        if c.has_collection(self.item_name_collection):
            return
        schema = c.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("pk", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("doc_key", DataType.VARCHAR, max_length=128)
        schema.add_field("file_title", DataType.VARCHAR, max_length=512)
        schema.add_field("item_name", DataType.VARCHAR, max_length=256)
        schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
        idx = c.prepare_index_params()
        self._vector_indexes(idx)
        idx.add_index(field_name="doc_key", index_type="INVERTED")
        c.create_collection(self.item_name_collection, schema=schema, index_params=idx)
        logger.info(f"已创建集合 {self.item_name_collection}")

    # ---------- 写入 ----------

    def replace_document(self, collection: str, doc_key: str, rows: list[dict]) -> int:
        """幂等写入：先删除该文档旧数据再插入（文档升版时自动替换旧切片）"""
        self.client.delete(collection_name=collection, filter=f'doc_key == "{escape(doc_key)}"')
        if not rows:
            return 0
        res = self.client.insert(collection_name=collection, data=rows)
        return res.get("insert_count", len(rows))

    # ---------- 检索 ----------

    @staticmethod
    def build_requests(dense, sparse, expr: str | None = None, limit: int = 10) -> list[AnnSearchRequest]:
        return [
            AnnSearchRequest(data=[dense], anns_field="dense_vector", param={"metric_type": "COSINE", "params": {"ef": 64}},
                             limit=limit, expr=expr),
            AnnSearchRequest(data=[sparse], anns_field="sparse_vector", param={"metric_type": "IP"},
                             limit=limit, expr=expr),
        ]

    def hybrid_search(self, collection: str, reqs, weights=(0.5, 0.5), limit: int = 5,
                      output_fields: list[str] | None = None) -> list[dict]:
        """稠密 + 稀疏双路召回，WeightedRanker(norm_score=True) 先归一化再加权融合"""
        res = self.client.hybrid_search(collection_name=collection, reqs=reqs,
                                        ranker=WeightedRanker(*weights, norm_score=True), limit=limit,
                                        output_fields=output_fields or ["item_name"])
        return res[0] if res else []

    def distinct_item_names(self) -> list[str]:
        rows = self.client.query(self.item_name_collection, filter="pk >= 0", output_fields=["item_name"], limit=16384)
        return sorted({r["item_name"] for r in rows})


milvus_gateway = MilvusGateway()
