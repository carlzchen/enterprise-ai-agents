"""
知识库升级到 v3（切片级增量 + 三路检索 + 统一授权 + 图片私有化）。可重复执行。

  1. SQL：补字段（state / valid_from / valid_to / norm_hash / series / subject / metas、sec_level、图纸卡片……）和新表
  2. 已有切片补 norm_hash、series、subject（规则识别，标明 by=rule-migrate；不调大模型）
  3. Milvus：从 kb_chunks_v2 把向量原样拷到 kb_chunks_v3（不重新向量化），state / level / series 按 SQL 填；BM25 由 Milvus 自动生成
  4. 图片：切片和父章节里的 MinIO 公开地址换成 kbimg:// 引用，登记 kb_image；去掉桶的匿名读策略
  5. 对账：每份文档的生效版本核对一遍

用法：PYTHONPATH=. .venv/bin/python scripts/kbase_migrate_v3.py [--old kb_chunks_v2]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infra.config.settings import settings  # noqa: E402
from app.infra.vector_store.milvus_gateway import milvus_gateway  # noqa: E402
from app.kbase import authz, store, vectors  # noqa: E402
from app.kbase.ingest import clean, subject, versioning  # noqa: E402

PUBLIC_IMG = re.compile(r"\]\((https?://[^)\s]+/images/([^/)\s]+)/([0-9a-f]{16})(\.[a-z]+))\)")


def step_sql() -> None:
    store.ensure_tables()
    rows = store.query("SELECT c.chunk_id, c.content, c.item_name, c.series, c.subject, c.norm_hash, d.product_code, d.series AS doc_series, d.title "
                       "FROM kb_chunk c JOIN kb_document d ON d.doc_key = c.doc_key")
    todo = [r for r in rows if not r["norm_hash"] or r["subject"] is None]
    for r in todo:
        sub = {**subject.rule_subject(r["content"], {"product_code": r["product_code"] or r["item_name"], "series": r["doc_series"]}), "by": "rule-migrate"}
        series = r["series"] or subject.series_of(r["item_name"]) or sub["series"] or r["doc_series"] or None
        store.execute("UPDATE kb_chunk SET norm_hash = %s, series = %s, subject = %s WHERE chunk_id = %s",
                      (clean.norm_hash(r["content"]), series, store.dumps(sub), r["chunk_id"]))
    print(f"SQL：{len(rows)} 个切片，补齐 {len(todo)} 个的哈希和主题")


def step_images() -> None:
    n_ref, n_img = 0, 0
    for table, key in (("kb_chunk", "chunk_id"), ("kb_parent", "parent_id")):
        for r in store.query(f"SELECT {key} AS id, doc_key, version, content FROM {table} WHERE content LIKE %s", ("%/images/%",)):
            found = PUBLIC_IMG.findall(r["content"])
            if not found:
                continue
            new = PUBLIC_IMG.sub(lambda m: f"](kbimg://{m.group(3)}{m.group(4)})", r["content"])
            store.execute(f"UPDATE {table} SET content = %s{', chunk_hash = %s' if table == 'kb_chunk' else ''} WHERE {key} = %s",
                          (new, versioning.content_hash(new), r["id"]) if table == "kb_chunk" else (new, r["id"]))
            n_ref += len(found)
            for _url, doc_dir, sha, ext in found:
                n_img += store.execute("INSERT IGNORE INTO kb_image (doc_key, version, sha, object_key, mime) VALUES (%s,%s,%s,%s,%s)",
                                       (r["doc_key"], r["version"], sha, f"images/{doc_dir}/{sha}{ext}", {".png": "image/png"}.get(ext, "image/jpeg"))) and 1
    # 图片挂到所在的章节上（回答引用这个章节时一起展示）
    for img in store.query("SELECT id, doc_key, version, sha FROM kb_image WHERE parent_seq IS NULL"):
        sec = store.one("SELECT seq FROM kb_parent WHERE doc_key = %s AND version = %s AND content LIKE %s ORDER BY seq LIMIT 1",
                        (img["doc_key"], img["version"], f"%kbimg://{img['sha']}%"))
        if sec:
            store.execute("UPDATE kb_image SET parent_seq = %s WHERE id = %s", (sec["seq"], img["id"]))
    from app.infra.object_storage.minio_gateway import minio_gateway
    private = minio_gateway.make_private()
    print(f"图片：替换 {n_ref} 处公开地址，登记 {n_img} 张；桶已改成私有：{private}")


def step_milvus(old: str) -> None:
    c = milvus_gateway.client
    vectors.ensure()
    new = vectors.name()
    if old == new or not c.has_collection(old):
        print(f"Milvus：没有旧集合 {old}，跳过拷贝（缺的向量由对账补）")
        return
    sql = {r["chunk_id"]: r for r in store.query(
        "SELECT c.chunk_id, c.doc_key, c.version, c.section, c.page, c.parent_id, c.item_name, c.series, c.state, c.content, "
        "d.doc_type, d.title, d.security_level, d.acl_groups FROM kb_chunk c JOIN kb_document d ON d.doc_key = c.doc_key")}
    have = set()
    it = c.query_iterator(old, filter="", output_fields=["chunk_id", "dense_vector", "sparse_vector"], batch_size=200)
    rows = []
    while True:
        batch = it.next()
        if not batch:
            break
        for b in batch:
            s = sql.get(b["chunk_id"])
            if not s:
                continue
            have.add(b["chunk_id"])
            lvl = authz.level_of(s["security_level"])
            rows.append({"chunk_id": s["chunk_id"], "doc_key": s["doc_key"], "version": s["version"], "doc_type": s["doc_type"], "title": s["title"][:390],
                         "section": (s["section"] or "")[:390], "page": int(s["page"] or 0), "item_name": (s["item_name"] or "")[:190],
                         "series": (s["series"] or "")[:30], "parent_id": s["parent_id"] or "", "security_level": s["security_level"],
                         "level": lvl if lvl is not None else -1, "acl": authz.parse_acl(s["acl_groups"])[:32], "state": s["state"],
                         "content": s["content"][:versioning.MAX_CONTENT], "dense_vector": b["dense_vector"],
                         "sparse_vector": {int(k): float(v) for k, v in b["sparse_vector"].items()}})
    it.close()
    vectors.upsert(rows)
    print(f"Milvus：从 {old} 拷贝 {len(rows)} 条向量到 {new}；SQL 里有、旧集合没有的 {len(set(sql) - have)} 条由对账补")


def step_reconcile() -> None:
    ok = bad = 0
    for d in store.query("SELECT doc_key, active_version FROM kb_document WHERE active_version IS NOT NULL"):
        v = versioning.verify(d["doc_key"], d["active_version"], repair=True)
        ok, bad = ok + v["ok"], bad + (not v["ok"])
        if not v["ok"]:
            print("  对账不一致：", d["doc_key"], json.dumps(v, ensure_ascii=False))
    print(f"对账：一致 {ok}，不一致 {bad}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="kb_chunks_v2")
    a = ap.parse_args()
    print(f"目标集合：{settings.kbase_collection}")
    step_sql()
    step_images()
    step_milvus(a.old)
    step_reconcile()
