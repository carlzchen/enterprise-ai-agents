"""
制度文档库：制度原文按章节切片，BGE-M3 稠密 + 稀疏向量写入 Milvus（集合 copilot_policy）。
  ingest_file  导入或更新一份制度（按 doc_key 先删后写，新版本生效后旧版本不再被检索到）
  search       混合检索，返回带出处的切片
支持 Markdown / 纯文本 / Word（.docx）。
"""
import hashlib
import re
import threading
from datetime import datetime
from pathlib import Path

from pymilvus import DataType

from app.infra.llm.providers import llm_providers
from app.infra.persistence.history_repository import history_repository
from app.infra.vector_store.milvus_gateway import DENSE_DIM, escape, milvus_gateway
from app.shared.runtime.logger import PROJECT_ROOT, logger

COLLECTION = "copilot_policy"
POLICY_DIR = PROJECT_ROOT / "data" / "policies"
MAX_CHUNK = 700
_lock = threading.Lock()


def _registry():
    col = history_repository.col.database["copilot_policy_doc"]
    col.create_index("doc_key", unique=True)
    return col


def ensure_collection():
    c = milvus_gateway.client
    if c.has_collection(COLLECTION):
        return
    schema = c.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("pk", DataType.INT64, is_primary=True, auto_id=True)
    schema.add_field("doc_key", DataType.VARCHAR, max_length=64)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=256)
    schema.add_field("section", DataType.VARCHAR, max_length=256)
    schema.add_field("version", DataType.VARCHAR, max_length=32)
    schema.add_field("content", DataType.VARCHAR, max_length=8000)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=DENSE_DIM)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    idx = c.prepare_index_params()
    milvus_gateway._vector_indexes(idx)
    idx.add_index(field_name="doc_key", index_type="INVERTED")
    c.create_collection(COLLECTION, schema=schema, index_params=idx)
    logger.info(f"已创建集合 {COLLECTION}")


def doc_key(filename: str) -> str:
    return "POL-" + hashlib.sha1(Path(filename).stem.encode("utf-8")).hexdigest()[:10]


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        import docx
        lines = []
        for p in docx.Document(str(path)).paragraphs:
            style = (p.style.name or "").lower()
            level = re.search(r"heading (\d)", style)
            lines.append(("#" * int(level.group(1)) + " " if level else "") + p.text)
        return "\n".join(lines)
    return path.read_text(encoding="utf-8")


def split_sections(markdown: str, title: str) -> list[dict]:
    """按二级、三级标题切成章节；过长的章节再按段落切。每个切片带上"【制度名】章节"前缀，避免不同制度的相似条款串在一起"""
    meta_version = re.search(r"版本[:：]\s*([\w.\-]+)", markdown[:400])
    version = meta_version.group(1) if meta_version else "1.0"
    sections, current, buf = [], "文档信息", []      # 第一个标题之前是版本、生效日期、归口部门

    def flush():
        text = "\n".join(buf).strip()
        if not text:
            return
        parts, piece = [], ""
        for para in re.split(r"\n\s*\n", text):
            if piece and len(piece) + len(para) > MAX_CHUNK:
                parts.append(piece)
                piece = ""
            piece = (piece + "\n\n" + para).strip()
        if piece:
            parts.append(piece)
        for p in parts:
            sections.append({"section": current, "version": version, "content": f"【{title}】{current}\n{p}"})

    for line in markdown.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            if len(m.group(1)) == 1:
                continue                                    # 一级标题就是制度名
            flush()
            current, buf = m.group(2).strip(), []
        else:
            buf.append(line)
    flush()
    return sections


def ingest_file(path: str | Path, uploaded_by: str = "system") -> dict:
    path = Path(path)
    text = read_text(path)
    first = re.search(r"^#\s+(.*)", text, flags=re.M)
    title = (first.group(1).strip() if first else path.stem)
    key = doc_key(path.name)
    chunks = split_sections(text, title)
    with _lock:
        ensure_collection()
        c = milvus_gateway.client
        c.delete(collection_name=COLLECTION, filter=f'doc_key == "{escape(key)}"')
        if chunks:
            emb = llm_providers.generate_embeddings([ch["content"] for ch in chunks])
            c.insert(COLLECTION, [{"doc_key": key, "doc_title": title, "section": ch["section"][:250], "version": ch["version"],
                                   "content": ch["content"][:7900], "dense_vector": emb["dense"][i], "sparse_vector": emb["sparse"][i]}
                                  for i, ch in enumerate(chunks)])
    record = {"doc_key": key, "title": title, "file_name": path.name, "version": chunks[0]["version"] if chunks else "",
              "chunk_count": len(chunks), "status": "READY", "uploaded_by": uploaded_by, "updated_at": datetime.now()}
    _registry().update_one({"doc_key": key}, {"$set": record}, upsert=True)
    logger.info(f"制度已入库：{title}（{len(chunks)} 个切片）")
    from app.copilot import memory
    memory.answer_clear("policy")                 # 制度变了，之前缓存的回答作废
    return record


def ingest_all(force: bool = False) -> list[dict]:
    """启动时把 data/policies 下的制度导入一遍；已导入且文件没变化的跳过"""
    out = []
    reg = _registry()
    for f in sorted(POLICY_DIR.glob("*")):
        if f.suffix.lower() not in (".md", ".txt", ".docx"):
            continue
        digest = hashlib.sha1(f.read_bytes()).hexdigest()
        old = reg.find_one({"doc_key": doc_key(f.name)})
        if not force and old and old.get("digest") == digest and old.get("status") == "READY":
            continue
        rec = ingest_file(f)
        reg.update_one({"doc_key": rec["doc_key"]}, {"$set": {"digest": digest}})
        out.append(rec)
    return out


def list_documents() -> list[dict]:
    return [{k: v for k, v in d.items() if k not in ("_id", "digest")} for d in _registry().find().sort("title", 1)]


def delete_document(key: str) -> bool:
    reg = _registry()
    doc = reg.find_one({"doc_key": key})
    if not doc:
        return False
    milvus_gateway.client.delete(collection_name=COLLECTION, filter=f'doc_key == "{escape(key)}"')
    reg.delete_one({"doc_key": key})
    from app.copilot import memory
    memory.answer_clear("policy")
    f = POLICY_DIR / doc.get("file_name", "")
    if doc.get("file_name") and f.exists():
        f.unlink()
    return True


def search(query: str, top_k: int = 5) -> list[dict]:
    ensure_collection()
    emb = llm_providers.generate_embeddings([query])
    reqs = milvus_gateway.build_requests(emb["dense"][0], emb["sparse"][0], limit=top_k * 3)
    hits = milvus_gateway.hybrid_search(COLLECTION, reqs, weights=(0.6, 0.4), limit=top_k,
                                        output_fields=["doc_key", "doc_title", "section", "version", "content"])
    return [{**h["entity"], "score": round(float(h["distance"]), 4)} for h in hits]
