"""
版本与一致性。写入协议：比对 → 暂存（PENDING）→ 对账 → 事务切换。

  diff      新版本的切片按归一化文本的 SHA-256 和当前生效的切片比对：
              没变   复用原来那一行（同一个 chunk_id），向量、主题结果都不重算，只登记这个版本下的元数据（序号、章节、页码）
              新增   新建一行，state = PENDING，检索时被过滤掉，用户看不到
              消失   切换时标成 INVALID，并记下 valid_to
  stage     写 SQL（真相源）和 Milvus（只写新增切片的向量）。kb_document.active_version 还指向旧版本
  verify    对账：这个版本应有的切片在 Milvus 里是否都在、有没有多余、抽样比对内容哈希、SQL 和 Milvus 的状态是否一致；能修的自动修
  switch    一个 SQL 事务里：PENDING → VALID、消失的 → INVALID、沿用的切片换上新版本的元数据、文档版本指针改到新版本、写通知事件。
            提交之后再把状态同步到 Milvus（只改标量字段）；同步失败登记补偿任务重试，期间检索按 SQL 的状态再过滤一遍，不会查到失效内容
  rollback  和 switch 是同一套动作，目标换成上一个版本（它的切片和每个版本的元数据都还在）
  失败      导入中断时登记补偿任务，清理这次导入留下的 PENDING 切片、Milvus 向量、MinIO 文件

doc_id + version 幂等：同一版本重复导入，文件哈希一样直接跳过；没导完的接着做，新增切片的主键是确定的，重写不会产生重复。
"""
import hashlib
import json
import random
from datetime import datetime, timedelta

from app.kbase import authz, store, vectors
from app.kbase.ingest import events
from app.shared.runtime.logger import logger

SAMPLE = 20
KEEP_DAYS = 7
MAX_CONTENT = 7900
COMP_BACKOFF = [30, 120, 600, 1800]


def chunk_id(doc_key: str, version: str, seq: int) -> str:
    """确定性主键（首次出现的版本 + 序号）：同一版本重复导入得到同样的主键，upsert 不会产生重复数据"""
    return hashlib.md5(f"{doc_key}|{version}|{seq}".encode("utf-8")).hexdigest()


def parent_id(doc_key: str, version: str, seq: int) -> str:
    return hashlib.md5(f"P|{doc_key}|{version}|{seq}".encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    """SQL 和 Milvus 对账用的内容哈希（存的是什么就比什么）"""
    return hashlib.md5(text[:MAX_CONTENT].encode("utf-8")).hexdigest()


def _loads(v):
    return json.loads(v) if isinstance(v, (str, bytes)) else (v or {})


def _jpath(version: str) -> str:
    return '$."' + version.replace('"', "") + '"'


def decide(doc_key: str, version: str, sha: str) -> tuple[str, str]:
    """
    文件级判重。返回（动作, 说明）：
      SKIP      内容和已入库的这个版本完全一样，跳过
      CONFLICT  版本号没变但内容变了，拒绝（版本号没升就改了内容，是流程问题）
      STALE     版本号比当前生效的还旧，拒绝
      NEW       新文档或新版本
      RESUME    同一版本上次没导完，接着做
    """
    doc = store.one("SELECT active_version FROM kb_document WHERE doc_key = %s", (doc_key,))
    ver = store.one("SELECT status, file_sha256 FROM kb_doc_version WHERE doc_key = %s AND version = %s", (doc_key, version))
    if ver:
        if ver["status"] in ("ACTIVE", "SUPERSEDED", "PURGED"):
            if ver["file_sha256"] == sha:
                return "SKIP", f"版本 {version} 已经入库，内容没有变化"
            return "CONFLICT", f"版本 {version} 已经入库，但这次的文件内容不同；内容有修改时必须升版本号"
        return "RESUME", f"版本 {version} 上次没有导完，继续"
    if doc and doc["active_version"] and _older(version, doc["active_version"]):
        return "STALE", f"版本 {version} 比当前生效的版本 {doc['active_version']} 还旧"
    return "NEW", "新版本" if doc else "新文档"


def _older(a: str, b: str) -> bool:
    def key(v: str):
        return (0, float(v)) if v.replace(".", "", 1).isdigit() else (1, v)
    return key(a) < key(b)


# ---------------------------------------------------------------- 比对

def diff(doc_key: str, version: str, chunks: list[dict]) -> dict:
    """chunks 已带 norm_hash。给每个 chunk 定 chunk_id 和 reused 标记；复用的带上原来的主题结果。返回统计"""
    current: dict[str, list[dict]] = {}
    for r in store.query("SELECT chunk_id, norm_hash, subject, item_name, series FROM kb_chunk WHERE doc_key = %s AND state = 'VALID' "
                         "ORDER BY chunk_seq", (doc_key,)):
        current.setdefault(r["norm_hash"], []).append(r)
    used = set()
    for c in chunks:
        same = current.get(c["norm_hash"]) or []
        if same:                                               # 同一份文档里有两块一模一样的内容时，按顺序一对一复用
            old = same.pop(0)
            used.add(old["chunk_id"])
            c.update(chunk_id=old["chunk_id"], reused=True, subject=_loads(old["subject"]) or None,
                     item_name=old["item_name"] or c.get("item_name", ""), series=old["series"] or "")
        else:
            c.update(chunk_id=chunk_id(doc_key, version, c["seq"]), reused=False)
    vanished = [r["chunk_id"] for rows in current.values() for r in rows if r["chunk_id"] not in used]
    return {"new": sum(1 for c in chunks if not c["reused"]), "reused": sum(1 for c in chunks if c["reused"]), "vanished": len(vanished)}


# ---------------------------------------------------------------- 暂存

def _meta(c: dict) -> dict:
    return {"seq": c["seq"], "section": c["section"][:300], "page": c.get("page"), "parent_id": c["parent_id"]}


def _doc_fields(meta: dict) -> dict:
    acl = authz.parse_acl(meta["acl_groups"])
    return {"doc_type": meta["doc_type"], "title": meta["title"][:390], "security_level": meta["security_level"],
            "level": authz.level_of(meta["security_level"]) if authz.level_of(meta["security_level"]) is not None else -1, "acl": acl[:32]}


def stage(s: dict, embeddings: dict) -> int:
    """暂存：SQL 是真相源，先写 SQL，再写 Milvus。embeddings 只对应新增的切片（顺序一致）"""
    dk, ver, meta = s["doc_key"], s["version"], s["meta"]
    acl = authz.parse_acl(meta["acl_groups"])
    new = [c for c in s["chunks"] if not c.get("reused")]
    reused = [c for c in s["chunks"] if c.get("reused")]
    store.execute(
        "INSERT INTO kb_document (doc_key, title, doc_type, source, security_level, sec_level, acl_groups, owner_email, product_code, series) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE title = VALUES(title), doc_type = VALUES(doc_type), "
        "security_level = VALUES(security_level), sec_level = VALUES(sec_level), acl_groups = VALUES(acl_groups), owner_email = VALUES(owner_email), "
        "product_code = VALUES(product_code), series = VALUES(series)",
        (dk, meta["title"][:300], meta["doc_type"], meta.get("source", "UPLOAD"), meta["security_level"], authz.level_of(meta["security_level"]),
         ",".join(acl), meta.get("owner_email"), meta.get("product_code"), meta.get("series")))
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO kb_doc_version (doc_key, version, file_name, file_sha256, file_type, route, status, chunk_count, parent_count, "
            "param_count, parsed_by, raw_url, diff) VALUES (%s,%s,%s,%s,%s,%s,'STAGED',%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "file_name = VALUES(file_name), file_sha256 = VALUES(file_sha256), file_type = VALUES(file_type), route = VALUES(route), "
            "status = 'STAGED', chunk_count = VALUES(chunk_count), parent_count = VALUES(parent_count), param_count = VALUES(param_count), "
            "parsed_by = VALUES(parsed_by), raw_url = VALUES(raw_url), diff = VALUES(diff), error = NULL",
            (dk, ver, s["file_name"], s["file_sha256"], s["file_type"], s["route"], len(s["chunks"]), len(s["parents"]),
             len(s.get("params", [])), s.get("parsed_by"), s.get("raw_url"), store.dumps(s.get("diff") or {})))
        # 上次没导完留下的同版本暂存数据先清掉（新增切片的主键是确定的，清掉再写和覆盖是一样的结果）
        cur.execute("DELETE FROM kb_chunk WHERE doc_key = %s AND valid_from = %s AND state = 'PENDING'", (dk, ver))
        cur.execute("UPDATE kb_chunk SET metas = JSON_REMOVE(metas, %s) WHERE doc_key = %s AND JSON_CONTAINS_PATH(metas, 'one', %s)",
                    (_jpath(ver), dk, _jpath(ver)))
        for table in ("kb_parent", "kb_param", "kb_drawing", "kb_drawing_dim", "kb_drawing_bom"):
            cur.execute(f"DELETE FROM {table} WHERE doc_key = %s AND version = %s", (dk, ver))
        cur.executemany("INSERT INTO kb_parent (parent_id, doc_key, version, seq, title, page, content) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        [(p["parent_id"], dk, ver, i, p["title"][:300], p.get("page"), p["content"]) for i, p in enumerate(s["parents"])])
        cur.executemany(
            "INSERT INTO kb_chunk (chunk_id, doc_key, version, chunk_seq, parent_id, section, page, chunk_hash, norm_hash, item_name, series, "
            "content, state, valid_from, subject, metas) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s,%s,%s)",
            [(c["chunk_id"], dk, ver, c["seq"], c["parent_id"], c["section"][:300], c.get("page"), content_hash(c["content"]), c["norm_hash"],
              c.get("item_name", "")[:200], c.get("series") or None, c["content"], ver, store.dumps(c.get("subject")),
              store.dumps({ver: _meta(c)})) for c in new])
        for c in reused:                                       # 沿用的切片：只登记这个版本下的元数据，切换时才生效
            cur.execute("UPDATE kb_chunk SET metas = JSON_SET(COALESCE(metas, JSON_OBJECT()), %s, CAST(%s AS JSON)) WHERE chunk_id = %s",
                        (_jpath(ver), store.dumps(_meta(c)), c["chunk_id"]))
        if s.get("params"):
            cur.executemany(
                "INSERT INTO kb_param (doc_key, version, model, param_key, param_name, value_num, value_text, unit, source_ref) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(dk, ver, p["model"], p["param_key"], p["param_name"], p["value_num"], p["value_text"], p["unit"], p["source_ref"])
                 for p in s["params"]])
        d = s.get("drawing")
        if d:
            _stage_drawing(cur, s, d)
        for img in s.get("images") or []:
            cur.execute("INSERT IGNORE INTO kb_image (doc_key, version, sha, object_key, mime, bytes, summary, parent_seq) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (dk, ver, img["sha"], img["object_key"], img.get("mime"), img.get("bytes"), img.get("summary") or None, img.get("parent_seq")))
    for msg in s.get("issues", []):
        store.execute("INSERT IGNORE INTO kb_alert (doc_key, version, kind, message) VALUES (%s,%s,'DRAWING_ERP_MISMATCH',%s)", (dk, ver, msg[:1000]))
    vectors.upsert(_milvus_rows(dk, ver, meta, new, embeddings, "PENDING"))
    return len(new)


def _stage_drawing(cur, s: dict, d: dict) -> None:
    dk, ver, meta = s["doc_key"], s["version"], s["meta"]
    t = d["title"]
    cur.execute("INSERT INTO kb_drawing (doc_key, version, dwg_no, name, rev, scale, designer, draw_date, product_code, erp_material, card, needs_review, review_note) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (dk, ver, t.get("DWG_NO"), t.get("NAME"), t.get("REV"), t.get("SCALE"), t.get("DESIGNER"), t.get("DATE"),
                 meta.get("product_code"), meta.get("product_code") if s.get("erp_product_ok") else None, store.dumps(d.get("card")),
                 1 if (d.get("card") or {}).get("needs_review") else 0, ((d.get("card") or {}).get("review_note") or "")[:1000] or None))
    from app.kbase.ingest.parsers import DIM_LABEL
    cur.executemany("INSERT INTO kb_drawing_dim (doc_key, version, feature, label, value, tol_plus, tol_minus) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [(dk, ver, k, DIM_LABEL.get(k, k), v["value"], v.get("plus"), v.get("minus")) for k, v in d["dims"].items()])
    matched = set(s.get("matched", []))
    cur.executemany("INSERT INTO kb_drawing_bom (doc_key, version, seq, material_code, material_name, qty, erp_matched) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    [(dk, ver, int(a) if str(a).isdigit() else None, b.strip(), c.strip(" └"), float(q) if _num(q) else None,
                      1 if b.strip() in matched else 0) for a, b, c, q in d["bom"]])


def _num(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _milvus_rows(doc_key: str, version: str, meta: dict, chunks: list[dict], emb: dict, state: str) -> list[dict]:
    f = _doc_fields(meta)
    return [{"chunk_id": c["chunk_id"], "doc_key": doc_key, "version": version, **f, "section": c["section"][:390], "page": int(c.get("page") or 0),
             "item_name": (c.get("item_name") or "")[:190], "series": (c.get("series") or "")[:30], "parent_id": c["parent_id"], "state": state,
             "content": c["content"][:MAX_CONTENT], "dense_vector": emb["dense"][i], "sparse_vector": emb["sparse"][i]}
            for i, c in enumerate(chunks)]


# ---------------------------------------------------------------- 对账

def _doc_meta(doc_key: str) -> dict:
    d = store.one("SELECT * FROM kb_document WHERE doc_key = %s", (doc_key,))
    return {"doc_type": d["doc_type"], "title": d["title"], "security_level": d["security_level"], "acl_groups": d["acl_groups"]}


def _target(doc_key: str, version: str) -> dict[str, dict]:
    """这个版本应有的切片：在这个版本下登记过元数据、并且不是已失效的"""
    rows = store.query("SELECT chunk_id, chunk_hash, content, state, metas, item_name, series FROM kb_chunk "
                       "WHERE doc_key = %s AND state IN ('PENDING','VALID') AND JSON_CONTAINS_PATH(metas, 'one', %s)", (doc_key, _jpath(version)))
    return {r["chunk_id"]: r for r in rows}


def verify(doc_key: str, version: str, repair: bool = True) -> dict:
    """对账。repair=True 时：缺的从 SQL 原文重新向量化补写，多的删掉，状态不一致的按 SQL 改，然后再核对一次"""
    target = _target(doc_key, version)
    all_sql = {r["chunk_id"]: r["state"] for r in store.query("SELECT chunk_id, state FROM kb_chunk WHERE doc_key = %s", (doc_key,))}
    in_milvus = vectors.ids_of(doc_key)
    missing = sorted(set(target) - set(in_milvus))
    extra = sorted(set(in_milvus) - set(all_sql))                # SQL 里根本没有的才算多余；已失效的保留着（回滚要用）
    state_bad = sorted(i for i, st in in_milvus.items() if i in all_sql and all_sql[i] != st)
    repaired = {"added": 0, "removed": 0, "state_fixed": 0}
    if repair and extra:
        vectors.delete_ids(extra)
        repaired["removed"] = len(extra)
    if repair and missing:
        from app.infra.llm.providers import llm_providers
        meta = _doc_meta(doc_key)
        emb = llm_providers.generate_embeddings([target[i]["content"][:MAX_CONTENT] for i in missing])
        chunks = [{"chunk_id": i, **_loads(target[i]["metas"])[version], "content": target[i]["content"], "item_name": target[i]["item_name"],
                   "series": target[i]["series"]} for i in missing]
        rows = _milvus_rows(doc_key, version, meta, chunks, emb, "PENDING")
        for r in rows:
            r["state"] = target[r["chunk_id"]]["state"]
        vectors.upsert(rows)
        repaired["added"] = len(missing)
    if repair and state_bad:
        vectors.update_fields([{"chunk_id": i, "state": all_sql[i]} for i in state_bad])
        repaired["state_fixed"] = len(state_bad)
    if repair and (missing or extra or state_bad):
        in_milvus = vectors.ids_of(doc_key)
    sample = random.sample(sorted(target), min(SAMPLE, len(target)))
    got = vectors.contents_of(sample)
    bad_hash = [i for i in sample if i in got and content_hash(got[i]) != target[i]["chunk_hash"]]
    still_bad = [i for i, st in in_milvus.items() if i in all_sql and all_sql[i] != st]
    ok = set(target) <= set(in_milvus) and not (set(in_milvus) - set(all_sql)) and not bad_hash and not still_bad
    return {"ok": ok, "sql": len(target), "milvus": sum(1 for i in in_milvus if i in target), "missing": len(missing), "extra": len(extra),
            "state_mismatch": len(state_bad), "hash_mismatch": len(bad_hash), "sampled": len(sample), "repaired": repaired}


# ---------------------------------------------------------------- 切换 / 回滚

def _bump() -> None:
    """知识库内容变了：版本号加一，问答缓存的 key 里带着它，旧缓存自然作废"""
    from app.platform import resilience
    resilience.counter("kb", bump=True)


def _apply(cur, doc_key: str, version: str) -> dict:
    """在事务里把这份文档的切片状态切到 version：在这个版本下登记过元数据的 → VALID（换上这个版本的序号、章节、页码），
    其余原来 VALID 的 → INVALID。别的版本的暂存切片（PENDING 且 valid_from 不是 version）不动"""
    cur.execute("SELECT chunk_id, state, valid_from, metas FROM kb_chunk WHERE doc_key = %s FOR UPDATE", (doc_key,))
    on, off = [], []
    for r in cur.fetchall():
        metas = _loads(r["metas"])
        if version in metas and not (r["state"] == "PENDING" and r["valid_from"] != version):
            m = metas[version]
            cur.execute("UPDATE kb_chunk SET state = 'VALID', valid_to = NULL, version = %s, chunk_seq = %s, section = %s, page = %s, parent_id = %s "
                        "WHERE chunk_id = %s", (version, m["seq"], m["section"], m.get("page"), m["parent_id"], r["chunk_id"]))
            on.append({"chunk_id": r["chunk_id"], "version": version, "page": int(m.get("page") or 0), "section": (m["section"] or "")[:390],
                       "parent_id": m["parent_id"]})
        elif r["state"] == "VALID":
            cur.execute("UPDATE kb_chunk SET state = 'INVALID', valid_to = %s WHERE chunk_id = %s", (version, r["chunk_id"]))
            off.append(r["chunk_id"])
    return {"on": on, "off": off}


def sync_milvus(doc_key: str, on: list[dict], off: list[str]) -> None:
    """把 SQL 里已经提交的状态同步到 Milvus：生效的改成 VALID 并换上新元数据（含文档的权限、密级），失效的改成 INVALID"""
    f = _doc_fields(_doc_meta(doc_key))
    vectors.update_fields([{**x, **f, "state": "VALID"} for x in on] + [{"chunk_id": i, "state": "INVALID"} for i in off])


def switch(doc_key: str, version: str, event: dict | None = None) -> str | None:
    """切换生效版本。返回被替换下来的旧版本号"""
    with store.transaction() as cur:
        cur.execute("SELECT active_version, title, owner_email FROM kb_document WHERE doc_key = %s FOR UPDATE", (doc_key,))
        doc = cur.fetchone()
        old = doc["active_version"]
        changed = _apply(cur, doc_key, version)
        if old and old != version:
            cur.execute("UPDATE kb_doc_version SET status = 'SUPERSEDED', superseded_at = NOW() WHERE doc_key = %s AND version = %s", (doc_key, old))
        cur.execute("UPDATE kb_doc_version SET status = 'ACTIVE', activated_at = NOW(), error = NULL WHERE doc_key = %s AND version = %s", (doc_key, version))
        cur.execute("UPDATE kb_document SET active_version = %s WHERE doc_key = %s", (version, doc_key))
        if event is not None:                                  # 通知事件和切换同一次提交
            events.record(cur, "VERSION_SWITCHED", doc_key, version, {"old": old, "title": doc["title"], "owner_email": doc["owner_email"], **event},
                          events.dedup_id("switch", doc_key, version))
    logger.info(f"[kbase] {doc_key} 生效版本 {old} → {version}：生效 {len(changed['on'])} 块，失效 {len(changed['off'])} 块")
    try:
        sync_milvus(doc_key, changed["on"], changed["off"])
    except Exception as e:                                     # SQL 已经切过去了；Milvus 慢一步不影响正确性（检索按 SQL 状态再过滤），登记补偿任务追上
        logger.warning(f"[kbase] {doc_key} 状态同步到 Milvus 失败，登记补偿任务：{e}")
        add_compensation(doc_key, version, "SYNC_STATE")
    _bump()
    return old if old != version else None


activate = switch                                              # 旧名字


def mark_failed(doc_key: str, version: str, error: str) -> None:
    store.execute("UPDATE kb_doc_version SET status = 'FAILED', error = %s WHERE doc_key = %s AND version = %s AND status NOT IN ('ACTIVE','SUPERSEDED','PURGED')",
                  (error[:1000], doc_key, version))


def rollback(doc_key: str) -> str:
    """回滚到上一个版本：它的切片和每个版本的元数据都还在，一个事务切回去"""
    prev = store.one("SELECT version FROM kb_doc_version WHERE doc_key = %s AND status = 'SUPERSEDED' ORDER BY superseded_at DESC LIMIT 1", (doc_key,))
    if not prev:
        raise ValueError("没有可回滚的旧版本（可能已经超过保留期被清理）")
    switch(doc_key, prev["version"])
    return prev["version"]


# ---------------------------------------------------------------- 清理

def purge(doc_key: str, version: str) -> dict:
    """删掉某个版本的数据：这个版本独有的切片（只在它下面登记过、且已不生效）从 SQL 和 Milvus 删除，共用的切片只去掉这个版本的元数据"""
    with store.transaction() as cur:
        cur.execute("UPDATE kb_chunk SET metas = JSON_REMOVE(metas, %s) WHERE doc_key = %s AND JSON_CONTAINS_PATH(metas, 'one', %s)",
                    (_jpath(version), doc_key, _jpath(version)))
        cur.execute("SELECT chunk_id FROM kb_chunk WHERE doc_key = %s AND state <> 'VALID' AND (metas IS NULL OR JSON_LENGTH(metas) = 0)", (doc_key,))
        dead = [r["chunk_id"] for r in cur.fetchall()]
        if dead:
            cur.execute("DELETE FROM kb_chunk WHERE chunk_id IN %s", (dead,))
        for table in ("kb_parent", "kb_param", "kb_drawing", "kb_drawing_dim", "kb_drawing_bom"):
            cur.execute(f"DELETE FROM {table} WHERE doc_key = %s AND version = %s", (doc_key, version))
        cur.execute("SELECT object_key FROM kb_image WHERE doc_key = %s AND version = %s", (doc_key, version))
        objects = [r["object_key"] for r in cur.fetchall()]
        cur.execute("DELETE FROM kb_image WHERE doc_key = %s AND version = %s", (doc_key, version))
    if dead:
        vectors.delete_ids(dead)
    removed = _remove_objects(objects) + _remove_prefix(f"raw/{doc_key}/v{version}/") + _remove_prefix(f"parsed/{doc_key}/v{version}/")
    return {"chunks": len(dead), "objects": removed}


def _remove_objects(keys: list[str]) -> int:
    """删 MinIO 对象，但别的版本还在用的图片（同一张图内容哈希相同）不删"""
    if not keys:
        return 0
    from app.infra.object_storage.minio_gateway import minio_gateway
    still = {r["object_key"] for r in store.query("SELECT DISTINCT object_key FROM kb_image WHERE object_key IN %s", (keys,))}
    n = 0
    for k in set(keys) - still:
        minio_gateway.remove(k)
        n += 1
    return n


def _remove_prefix(prefix: str) -> int:
    from app.infra.object_storage.minio_gateway import minio_gateway
    return minio_gateway.delete_prefix(prefix)


def cleanup(keep_days: int = KEEP_DAYS) -> int:
    """清理超过保留期的旧版本。失败只影响空间，不影响正确性（查询只认 VALID）"""
    rows = store.query("SELECT doc_key, version FROM kb_doc_version WHERE status = 'SUPERSEDED' AND superseded_at < %s",
                       (datetime.now() - timedelta(days=keep_days),))
    for r in rows:
        purge(r["doc_key"], r["version"])
        store.execute("UPDATE kb_doc_version SET status = 'PURGED' WHERE doc_key = %s AND version = %s", (r["doc_key"], r["version"]))
    return len(rows)


def delete_document(doc_key: str) -> bool:
    if not store.one("SELECT 1 FROM kb_document WHERE doc_key = %s", (doc_key,)):
        return False
    vectors.delete_doc(doc_key)
    for table in ("kb_chunk", "kb_parent", "kb_param", "kb_drawing", "kb_drawing_dim", "kb_drawing_bom", "kb_image", "kb_doc_version"):
        store.execute(f"DELETE FROM {table} WHERE doc_key = %s", (doc_key,))
    store.execute("DELETE FROM kb_document WHERE doc_key = %s", (doc_key,))
    for prefix in ("raw", "images", "parsed"):
        _remove_prefix(f"{prefix}/{doc_key}/")
    _bump()
    return True


# ---------------------------------------------------------------- 补偿任务

def add_compensation(doc_key: str, version: str, kind: str, job_id: str | None = None) -> None:
    store.execute("INSERT INTO kb_compensation (job_id, doc_key, version, kind) VALUES (%s,%s,%s,%s) "
                  "ON DUPLICATE KEY UPDATE status = 'PENDING', attempts = 0, next_run_at = NOW(), error = NULL", (job_id, doc_key, version, kind))


def clean_staged(doc_key: str, version: str) -> dict:
    """清理中断导入的残留：这个版本的 PENDING 切片（SQL + Milvus）、沿用切片上登记的这个版本的元数据、父章节参数图纸、MinIO 原文件和独有图片。
    版本已经生效（或曾经生效）的不清"""
    v = store.one("SELECT status FROM kb_doc_version WHERE doc_key = %s AND version = %s", (doc_key, version))
    if v and v["status"] in ("ACTIVE", "SUPERSEDED"):
        return {"skipped": f"版本状态是 {v['status']}，不清理"}
    pending = [r["chunk_id"] for r in store.query("SELECT chunk_id FROM kb_chunk WHERE doc_key = %s AND valid_from = %s AND state = 'PENDING'", (doc_key, version))]
    # Milvus 里可能有 SQL 没记上的（写完 Milvus、SQL 事务前就崩了的情况不会发生，因为先写 SQL；但写 Milvus 写到一半崩的会有）
    orphan = [i for i, st in vectors.ids_of(doc_key, ("PENDING",)).items()
              if i not in {r["chunk_id"] for r in store.query("SELECT chunk_id FROM kb_chunk WHERE doc_key = %s AND state <> 'PENDING'", (doc_key,))}]
    if pending or orphan:
        vectors.delete_ids(sorted(set(pending) | set(orphan)))
    store.execute("DELETE FROM kb_chunk WHERE doc_key = %s AND valid_from = %s AND state = 'PENDING'", (doc_key, version))
    out = purge(doc_key, version)
    return {"pending": len(pending), "milvus_orphans": len(orphan), **out}


def run_compensations(limit: int = 10) -> list[dict]:
    """执行到期的补偿任务。失败按 30 秒 / 2 分钟 / 10 分钟 / 30 分钟退避，用完发告警事件"""
    rows = store.query("SELECT * FROM kb_compensation WHERE status = 'PENDING' AND next_run_at <= NOW() ORDER BY next_run_at LIMIT %s", (limit,))
    out = []
    for t in rows:
        try:
            if t["kind"] == "CLEAN_STAGED":
                result = clean_staged(t["doc_key"], t["version"])
            else:
                active = (store.one("SELECT active_version FROM kb_document WHERE doc_key = %s", (t["doc_key"],)) or {}).get("active_version")
                result = verify(t["doc_key"], active, repair=True) if active else {"skipped": "文档没有生效版本"}
                if active and not result["ok"]:
                    raise RuntimeError(f"状态同步后对账仍不一致：{result}")
            store.execute("UPDATE kb_compensation SET status = 'DONE', attempts = attempts + 1, result = %s, finished_at = NOW(), error = NULL WHERE id = %s",
                          (store.dumps(result), t["id"]))
            out.append({"id": t["id"], "kind": t["kind"], "ok": True, "result": result})
        except Exception as e:
            n = t["attempts"] + 1
            if n <= len(COMP_BACKOFF):
                store.execute("UPDATE kb_compensation SET attempts = %s, error = %s, next_run_at = %s WHERE id = %s",
                              (n, str(e)[:500], datetime.now() + timedelta(seconds=COMP_BACKOFF[n - 1]), t["id"]))
            else:
                store.execute("UPDATE kb_compensation SET status = 'FAILED', attempts = %s, error = %s, finished_at = NOW() WHERE id = %s", (n, str(e)[:500], t["id"]))
                doc = store.one("SELECT title, owner_email FROM kb_document WHERE doc_key = %s", (t["doc_key"],)) or {}
                events.record(None, "COMPENSATION_FAILED", t["doc_key"], t["version"], {"task": t["kind"], "attempts": n, "error": str(e)[:300], **doc},
                              events.dedup_id("comp", t["id"]))
            out.append({"id": t["id"], "kind": t["kind"], "ok": False, "error": str(e)[:200]})
    return out


def find_orphans(older_than_hours: int = 6) -> int:
    """每日对账的一部分：超过 6 小时还停在 PENDING、且没有任务在跑的暂存版本，登记清理任务"""
    rows = store.query("SELECT DISTINCT c.doc_key, c.valid_from AS version FROM kb_chunk c JOIN kb_doc_version v ON v.doc_key = c.doc_key AND v.version = c.valid_from "
                       "WHERE c.state = 'PENDING' AND v.status IN ('STAGED','FAILED') AND v.created_at < %s "
                       "AND NOT EXISTS (SELECT 1 FROM kb_ingest_job j WHERE j.doc_key = c.doc_key AND j.version = c.valid_from AND j.status IN ('RUNNING','RETRY','QUEUED'))",
                       (datetime.now() - timedelta(hours=older_than_hours),))
    for r in rows:
        add_compensation(r["doc_key"], r["version"], "CLEAN_STAGED")
    return len(rows)


# ---------------------------------------------------------------- 每日对账

def reconcile_all() -> dict:
    """每日对账：所有生效版本逐个核对并自动修复；修不好的发告警事件；顺带登记孤儿暂存数据的清理任务、清理过期旧版本"""
    rows = store.query("SELECT d.doc_key, d.active_version v, d.title, d.owner_email FROM kb_document d WHERE d.active_version IS NOT NULL")
    report = {"documents": len(rows), "consistent": 0, "repaired": 0, "broken": [], "chunks": 0}
    for r in rows:
        v = verify(r["doc_key"], r["v"], repair=True)
        report["chunks"] += v["sql"]
        if v["ok"] and not (v["missing"] or v["extra"] or v["state_mismatch"]):
            report["consistent"] += 1
        elif v["ok"]:
            report["repaired"] += 1
        else:
            report["broken"].append({"doc_key": r["doc_key"], "version": r["v"], **{k: v[k] for k in ("sql", "milvus", "hash_mismatch", "state_mismatch")}})
            events.record(None, "RECONCILE_BROKEN", r["doc_key"], r["v"], {"title": r["title"], "owner_email": r["owner_email"], **v},
                          events.dedup_id("reconcile", r["doc_key"], r["v"], f"{datetime.now():%Y%m%d}"))
    report["orphans"] = find_orphans()
    report["compensations"] = len(run_compensations(50))
    report["purged"] = cleanup()
    logger.info(f"[kbase] 对账完成：{report['documents']} 份文档，一致 {report['consistent']}，已修复 {report['repaired']}，异常 {len(report['broken'])}")
    return report


def active_versions(doc_keys: list[str]) -> dict[str, str]:
    if not doc_keys:
        return {}
    return {r["doc_key"]: r["active_version"] for r in store.query(
        "SELECT doc_key, active_version FROM kb_document WHERE doc_key IN %s AND active_version IS NOT NULL", (list(set(doc_keys)),))}


def valid_chunks(chunk_ids: list[str]) -> dict[str, dict]:
    """检索结果的最后一道过滤：SQL 里状态是 VALID、文档已标注密级和可见范围的切片，连同它在生效版本下的元数据（章节、页码、父章节）"""
    if not chunk_ids:
        return {}
    rows = store.query("SELECT c.chunk_id, c.version, c.section, c.page, c.parent_id, c.item_name, c.series FROM kb_chunk c "
                       "JOIN kb_document d ON d.doc_key = c.doc_key AND d.active_version = c.version WHERE c.chunk_id IN %s AND c.state = 'VALID' "
                       "AND d.sec_level IS NOT NULL AND d.acl_groups <> ''",          # 未标注的文档在这里也拦一道（Milvus 的标量更新有几秒延迟）
                       (list(set(chunk_ids)),))
    return {r["chunk_id"]: r for r in rows}
