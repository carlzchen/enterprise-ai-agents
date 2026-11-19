"""
导入图（LangGraph）：

  node_entry → node_router ─┬─ doc   → node_parse_doc ──┐   文档：MinerU / Docling / pypdf；扫描页和图片文件交给 Qwen-VL
   文件 SHA-256   按类型分流  ├─ table → node_parse_table ─┤   Excel 选型表
   判重·登记                 └─ cad   → node_parse_cad ───┤   DWG → ODA → DXF → ezdxf
                                                          ▼  （每条路线解析完都做：页眉页脚、脱敏、术语和单位统一）
   node_split → node_diff → node_subject → node_quality_gate → node_embed → node_stage → node_verify → node_switch → node_archive → END
    章节切分     切片哈希比对   新增切片的主题     质量门禁          只算新增的     暂存 PENDING   对账           事务切换+事件    Markdown 存 MinIO
                沿用/新增/消失  识别（模型+证据）                    向量

  切片级增量：切片按归一化文本（去页码、页眉页脚、空白）算 SHA-256，和当前生效的切片比对。
    没变的复用原来的向量和主题结果，只更新元数据；新增的才调模型识别主题、算向量；消失的在切换时标成失效。

  任何节点出错都不往外抛，而是把错误写进状态并走到 node_fail：
    文件本身的问题（加密、损坏、类型不支持、缺密级、质量不达标）不重试，直接告警
    临时性问题（网络、接口限流、数据库抖动）按 30 秒 / 2 分钟 / 10 分钟退避重试，耗尽后告警
  解析结果和向量落在任务目录里，重试时已经做完的步骤直接读盘，不重新解析和向量化（解析最贵）
"""
import json
import pickle
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.infra.llm.providers import llm_providers
from app.infra.object_storage.minio_gateway import minio_gateway
from app.kbase import store
from app.kbase.ingest import clean, events, parsers, params as P, subject as subject_service, versioning
from app.kbase.ingest.clean import Rejected
from app.kbase.ingest import split as split_service
from app.shared.runtime.logger import PROJECT_ROOT, logger

WORK_DIR = PROJECT_ROOT / "output" / "kbase"
BACKOFF = [30, 120, 600]                      # 第 1、2、3 次重试前等待的秒数
LEVELS = ("PUBLIC", "INTERNAL", "CONFIDENTIAL")
STAGE_CN = {"node_entry": "登记", "node_clean": "文件检查", "node_router": "路由", "node_parse_doc": "文档解析",
            "node_parse_table": "表格解析", "node_parse_cad": "图纸解析", "node_split": "切片", "node_diff": "切片比对", "node_subject": "主题识别",
            "node_quality_gate": "质量门禁", "node_embed": "向量化", "node_stage": "暂存写入", "node_verify": "对账", "node_switch": "切换版本",
            "node_archive": "归档"}
_PAGE = re.compile(r"<!-- page:(\d+) -->\s*")


class GateFailed(Rejected):
    """质量门禁没过：内容有问题，重试没用"""


class IngestState(TypedDict, total=False):
    job_id: str
    file_path: str
    file_name: str
    work_dir: str
    doc_key: str
    version: str
    meta: dict                 # title / doc_type / source / security_level / acl_groups / owner_email / product_code / series
    file_sha256: str
    file_type: str
    route: str                 # doc / table / cad
    decision: str              # NEW / RESUME / SKIP
    raw_url: str
    parsed_by: str
    markdown: str
    params: list
    images: list               # 图片登记信息（MinIO 对象路径、哈希、摘要、所在章节）
    clean_stats: dict          # 脱敏和术语统一的命中次数
    diff: dict                 # 切片比对：new / reused / vanished
    subject_stats: dict
    drawing: dict
    issues: list
    matched: list
    erp_product_ok: bool
    table_rows: int
    parents: list
    chunks: list
    gate: dict
    verify: dict
    replaced: str
    error: dict                # {stage, message, retryable}
    skipped: str


def _safe(fn):
    """节点装饰器：记录阶段；异常不外抛，分类后写进 state['error']"""
    name = fn.__name__

    def wrapper(state: IngestState) -> dict:
        store.execute("UPDATE kb_ingest_job SET stage = %s WHERE job_id = %s", (name, state["job_id"]))
        start = time.time()
        try:
            out = fn(state) or {}
            logger.info(f"[kbase] {state['job_id']} {STAGE_CN.get(name, name)} {int((time.time() - start) * 1000)}ms")
            return out
        except Rejected as e:
            return {"error": {"stage": name, "message": str(e), "retryable": False}}
        except Exception as e:
            logger.exception(f"[kbase] {state['job_id']} {STAGE_CN.get(name, name)}失败")
            return {"error": {"stage": name, "message": f"{type(e).__name__}: {str(e)[:300]}", "retryable": True}}
    wrapper.__name__ = name
    return wrapper


def _cache(state: IngestState, name: str) -> Path:
    return Path(state["work_dir"]) / name


@_safe
def node_entry(state: IngestState) -> dict:
    meta = state["meta"]
    if meta.get("security_level") not in LEVELS:
        raise Rejected("没有指定密级，拒绝入库（宁可不入库，也不默认公开）")
    if not meta.get("acl_groups"):
        raise Rejected("没有指定可见范围，拒绝入库")
    path = Path(state["file_path"])
    kind = clean.check_file(path)
    sha = clean.sha256(path)
    decision, why = versioning.decide(state["doc_key"], state["version"], sha)
    if decision in ("CONFLICT", "STALE"):
        raise Rejected(why)
    if decision == "SKIP":
        return {"skipped": why, "decision": decision, "file_sha256": sha, "file_type": kind}
    raw_url = minio_gateway.put_bytes(f"raw/{state['doc_key']}/v{state['version']}/{path.name}", path.read_bytes())
    return {"file_sha256": sha, "file_type": kind, "decision": decision, "raw_url": raw_url}


@_safe
def node_router(state: IngestState) -> dict:
    return {"route": clean.ROUTE[state["file_type"]]}


def _parsed(state: IngestState, build) -> dict:
    """解析结果落盘：重试时直接读，不再解析一遍"""
    f = _cache(state, "parsed.json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    out = build()
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _normalize(md: str) -> tuple[str, dict]:
    """每条路线解析完都做：脱敏 → 术语和单位统一"""
    md, masked = clean.mask_sensitive(md)
    md, terms = clean.unify_terms(md)
    return md, {"masked": masked, "terms": terms}


@_safe
def node_parse_doc(state: IngestState) -> dict:
    out = _parsed(state, lambda: parsers.parse_doc(Path(state["file_path"]), state["file_type"], Path(state["work_dir"]), state["doc_key"],
                                                   state["meta"].get("title", "")))
    md, stats = _normalize(clean.clean_text(out["markdown"]))
    meta, model = state["meta"], state["meta"].get("product_code")
    if not model:                                              # 元数据里没给型号：全文只出现一个型号时就认它
        found = set(parsers.MODEL_RE.findall(clean.normalize_model(md[:4000])))
        model = found.pop() if len(found) == 1 else None
    params = P.from_markdown(md, model, meta["title"]) if (model and meta["doc_type"] == "DATASHEET") else []
    return {"markdown": md, "parsed_by": out["parsed_by"], "params": params, "images": out.get("images", []), "clean_stats": stats,
            "meta": {**meta, "product_code": model or meta.get("product_code")}}


@_safe
def node_parse_table(state: IngestState) -> dict:
    out = _parsed(state, lambda: parsers.parse_table(Path(state["file_path"])))
    md, stats = _normalize(f"# {state['meta']['title']}\n\n" + out["markdown"])
    return {"markdown": md, "parsed_by": out["parsed_by"], "params": out["params"], "table_rows": out["table_rows"], "clean_stats": stats}


@_safe
def node_parse_cad(state: IngestState) -> dict:
    out = _parsed(state, lambda: parsers.parse_cad(Path(state["file_path"]), state["file_type"], Path(state["work_dir"])))
    meta = {**state["meta"], "product_code": state["meta"].get("product_code") or out["product_code"]}
    dims = [{"model": meta["product_code"] or out["drawing"]["title"].get("DWG_NO", ""), "param_key": k, "param_name": parsers.DIM_LABEL.get(k, k),
             "value_num": v["value"], "value_text": f"{v['value']:g}" + (f"（{v['plus']:+g}/{v['minus']:+g}）" if v.get("plus") is not None else ""),
             "unit": "个" if k == "hole_count" else "mm", "source_ref": f"图纸 {out['drawing']['title'].get('DWG_NO', '')}"}
            for k, v in out["drawing"]["dims"].items()]
    md, stats = _normalize(out["markdown"])
    return {"markdown": md, "parsed_by": out["parsed_by"], "drawing": out["drawing"], "issues": out["issues"], "clean_stats": stats,
            "matched": out["matched"], "params": dims, "meta": meta,
            "erp_product_ok": not any("在金蝶物料档案里不存在" in i and (meta["product_code"] or "") in i for i in out["issues"])}


@_safe
def node_split(state: IngestState) -> dict:
    dk, ver, title = state["doc_key"], state["version"], state["meta"]["title"]
    sections, pieces = split_service.split_document(state["markdown"], title)
    parents, page = [], 0
    for i, s in enumerate(sections):
        m = _PAGE.search(s["content"])
        page = int(m.group(1)) if m else page
        parents.append({"parent_id": versioning.parent_id(dk, ver, i), "title": s["title"], "page": page or None,
                        "content": _PAGE.sub("", s["content"]).strip()})
    item = state["meta"].get("product_code") or ""
    chunks = []
    for c in pieces:
        text = _PAGE.sub("", c["content"]).strip()
        m = _PAGE.search(c["content"])
        parent = parents[c["parent_index"]]
        models = parsers.MODEL_RE.findall(text)
        chunks.append({"seq": len(chunks), "parent_id": parent["parent_id"], "section": c["parent_title"],
                       "page": int(m.group(1)) if m else parent["page"], "content": text, "norm_hash": clean.norm_hash(text),
                       "item_name": item or (models[0] if len(set(models)) == 1 else "")})
    imgs = []
    for img in state.get("images") or []:                       # 图片挂到它所在的章节上，回答引用这个章节时一起展示
        seq = next((i for i, p in enumerate(parents) if f"kbimg://{img['sha']}" in p["content"]), None)
        imgs.append({**img, "parent_seq": seq})
    return {"parents": parents, "chunks": chunks, "images": imgs}


@_safe
def node_diff(state: IngestState) -> dict:
    chunks = [dict(c) for c in state["chunks"]]
    d = versioning.diff(state["doc_key"], state["version"], chunks)
    logger.info(f"[kbase] {state['job_id']} 切片比对：新增 {d['new']}，沿用 {d['reused']}，消失 {d['vanished']}")
    return {"chunks": chunks, "diff": d}


@_safe
def node_subject(state: IngestState) -> dict:
    """只给新增的切片识别主题；结果落盘，重试时不重复调用模型"""
    f = _cache(state, "subjects.json")
    chunks = [dict(c) for c in state["chunks"]]
    done = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    todo = [c for c in chunks if not c["reused"] and c["chunk_id"] not in done]
    use_llm = None if state["meta"].get("doc_type") != "POLICY" else False   # 制度类文件不讲具体型号，主体直接走规则，不逐块调模型
    stats = subject_service.identify(todo, state["meta"], use_llm=use_llm) if todo else {}
    done.update({c["chunk_id"]: {"subject": c["subject"], "item_name": c["item_name"], "series": c["series"]} for c in todo})
    f.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    for c in chunks:
        if not c["reused"]:
            c.update(done[c["chunk_id"]])
    return {"chunks": chunks, "subject_stats": stats}


@_safe
def node_quality_gate(state: IngestState) -> dict:
    """入库前的质量检查。不达标就不进索引，并告诉负责人为什么"""
    chunks, route, problems = state["chunks"], state["route"], []
    if not chunks:
        raise GateFailed("质量门禁：没有切出任何内容")
    n_short = sum(1 for c in chunks if len(re.sub(r"【.*?】\S*\s*", "", c["content"], count=1)) < 15)
    short = n_short / len(chunks)
    if short > 0.3 and (n_short >= 2 or len(chunks) == 1):                    # 只有两三块的短文档，一块偏短不算质量问题
        problems.append(f"空块和超短块占 {short:.0%}（上限 30%）")
    with_model = sum(1 for c in chunks if c["item_name"]) / len(chunks)
    if state["meta"]["doc_type"] in ("DATASHEET", "DRAWING") and with_model < 0.5:
        problems.append(f"型号识别率只有 {with_model:.0%}（规格书和图纸要求不低于 50%）")
    if state["meta"]["doc_type"] == "DATASHEET" and route == "doc" and not state.get("params"):
        problems.append("规格书里没有抽到任何参数")
    if route == "table":
        models = len({p["model"] for p in state.get("params", [])})
        if models != state.get("table_rows"):
            problems.append(f"表格有 {state.get('table_rows')} 行型号，只解析出 {models} 个，行数不一致")
    if route == "cad":                                         # 图纸缺项不拒收（转视觉模型或人工复核）；什么都读不到才拒收
        d = state["drawing"]
        if not d["title"] and not (d.get("card") or {}).get("dims") and not d["bom"]:
            problems.append("图纸里没有读到标题栏、尺寸和明细表，可能是扫描图或空图")
    gate = {"chunks": len(chunks), "short_ratio": round(short, 3), "model_ratio": round(with_model, 3), "params": len(state.get("params", []))}
    if problems:
        raise GateFailed("质量门禁没有通过：" + "；".join(problems))
    return {"gate": gate}


def _new_chunks(state: IngestState) -> list[dict]:
    return [c for c in state["chunks"] if not c.get("reused")]


@_safe
def node_embed(state: IngestState) -> dict:
    """只给新增的切片算向量（沿用的切片向量已经在 Milvus 里了）。结果连同切片主键落盘，重试时主键一致才直接用"""
    f, new = _cache(state, "embeddings.pkl"), _new_chunks(state)
    ids = [c["chunk_id"] for c in new]
    cached = pickle.loads(f.read_bytes()) if f.exists() else None
    if not cached or cached.get("ids") != ids:
        emb = {"dense": [], "sparse": []}
        if new:
            from app.kbase.query.retrieve import _model_lock   # 模型只有一份：多个入库线程、在线问答都排这一把锁
            with _model_lock:
                emb = llm_providers.generate_embeddings([c["content"][:versioning.MAX_CONTENT] for c in new])
        if any(len(v) != 1024 or any(x != x for x in v) for v in emb["dense"]):
            raise GateFailed("向量维度不对或包含 NaN")
        f.write_bytes(pickle.dumps({"ids": ids, "emb": emb}))
    return {}


@_safe
def node_stage(state: IngestState) -> dict:
    emb = pickle.loads(_cache(state, "embeddings.pkl").read_bytes())["emb"]
    versioning.stage({**state, "file_name": state["file_name"]}, emb)
    return {}


@_safe
def node_verify(state: IngestState) -> dict:
    v = versioning.verify(state["doc_key"], state["version"], repair=True)
    if not v["ok"]:
        raise RuntimeError(f"对账没有通过：SQL {v['sql']} 个切片，向量库 {v['milvus']} 个，内容不一致 {v['hash_mismatch']} 个")
    return {"verify": v}


@_safe
def node_switch(state: IngestState) -> dict:
    """事务切换（切片状态 + 版本指针 + 通知事件一次提交），然后把任务标成完成"""
    meta, dk, ver = state["meta"], state["doc_key"], state["version"]
    old = versioning.switch(dk, ver, event={"diff": state.get("diff"), "params": len(state.get("params", [])), "images": len(state.get("images") or []),
                                           "file_name": state["file_name"], "job_id": state["job_id"]})
    store.execute("UPDATE kb_ingest_job SET status = 'DONE', stage = 'ACTIVE', finished_at = NOW(), error = NULL WHERE job_id = %s", (state["job_id"],))
    base = {"title": meta["title"], "owner_email": meta.get("owner_email")}
    if state.get("issues"):                                    # 入库成功，但图纸和 ERP 对不上：单独通知，不算失败
        events.record(None, "DRAWING_MISMATCH", dk, ver, {**base, "issues": state["issues"]}, events.dedup_id("mismatch", dk, ver))
    card = (state.get("drawing") or {}).get("card") or {}
    if card.get("needs_review"):
        events.record(None, "DRAWING_REVIEW", dk, ver, {**base, "missing": card.get("missing", []), "vl_note": card.get("review_note")},
                      events.dedup_id("review", dk, ver))
    return {"replaced": old or ""}


node_activate = node_switch                                     # 旧名字


@_safe
def node_archive(state: IngestState) -> dict:
    """解析后的 Markdown 和每块的主体识别结果（含证据）存 MinIO（parsed/<文档>/v<版本>/），供复核和重建索引。归档失败不影响已经生效的版本"""
    try:
        prefix = f"parsed/{state['doc_key']}/v{state['version']}/"
        minio_gateway.put_bytes(prefix + "document.md", state["markdown"].encode("utf-8"), "text/markdown; charset=utf-8")
        detail = {"doc_key": state["doc_key"], "version": state["version"], "title": state["meta"]["title"], "parsed_by": state.get("parsed_by"),
                  "diff": state.get("diff"), "clean_stats": state.get("clean_stats"), "subject_stats": state.get("subject_stats"),
                  "drawing_card": (state.get("drawing") or {}).get("card"), "archived_at": datetime.now().isoformat(timespec="seconds"),
                  "chunks": [{"chunk_id": c["chunk_id"], "seq": c["seq"], "section": c["section"], "page": c.get("page"), "reused": c.get("reused"),
                              "subject": c.get("subject")} for c in state["chunks"]]}
        minio_gateway.put_bytes(prefix + "chunks.json", json.dumps(detail, ensure_ascii=False, default=str).encode("utf-8"), "application/json")
    except Exception as e:
        logger.warning(f"[kbase] {state['job_id']} 归档到 MinIO 失败（不影响入库）：{e}")
    return {}


def node_skip(state: IngestState) -> dict:
    store.execute("UPDATE kb_ingest_job SET status = 'SKIPPED', stage = 'SKIPPED', finished_at = NOW(), error = %s WHERE job_id = %s",
                  (state["skipped"], state["job_id"]))
    return {}


def node_fail(state: IngestState) -> dict:
    """统一的失败出口：能重试的排期重试，不能重试或重试耗尽的写发件箱通知负责人"""
    err, job_id = state["error"], state["job_id"]
    job = store.one("SELECT attempts, batch_id FROM kb_ingest_job WHERE job_id = %s", (job_id,))
    attempts = job["attempts"] + 1
    stage_cn = STAGE_CN.get(err["stage"], err["stage"])
    if err["retryable"] and attempts <= len(BACKOFF):
        wait = BACKOFF[attempts - 1]
        store.execute("UPDATE kb_ingest_job SET status = 'RETRY', attempts = %s, retryable = 1, error = %s, next_retry_at = %s WHERE job_id = %s",
                      (attempts, err["message"][:1000], datetime.now() + timedelta(seconds=wait), job_id))
        logger.warning(f"[kbase] {job_id} 在{stage_cn}阶段失败，{wait} 秒后第 {attempts} 次重试：{err['message'][:120]}")
        return {}
    store.execute("UPDATE kb_ingest_job SET status = 'FAILED', attempts = %s, retryable = %s, error = %s, finished_at = NOW() WHERE job_id = %s",
                  (attempts, 1 if err["retryable"] else 0, err["message"][:1000], job_id))
    compensate = state.get("decision") in ("NEW", "RESUME")
    if compensate:                                           # 原文件已经进了 MinIO，可能还写了暂存切片和向量：登记补偿任务清理
        versioning.mark_failed(state["doc_key"], state["version"], err["message"])
        versioning.add_compensation(state["doc_key"], state["version"], "CLEAN_STAGED", job_id)
    if job["batch_id"]:                                      # 批量任务不逐个发邮件，批次结束时发一封汇总
        return {}
    why = "已重试 %d 次仍然失败" % len(BACKOFF) if err["retryable"] else "这类问题重试没有用，需要人工处理"
    events.record(None, "INGEST_FAILED", state["doc_key"], state["version"],
                  {"title": state["meta"].get("title") or state["file_name"], "owner_email": state["meta"].get("owner_email"), "file_name": state["file_name"],
                   "job_id": job_id, "stage": stage_cn, "message": err["message"], "why": why, "compensation": compensate}, events.dedup_id("fail", job_id))
    return {}


def _next(after: str):
    def route(state: IngestState) -> str:
        if state.get("error"):
            return "node_fail"
        if state.get("skipped"):
            return "node_skip"
        if after == "node_router":
            return {"doc": "node_parse_doc", "table": "node_parse_table", "cad": "node_parse_cad"}[state["route"]]
        return _ORDER[after]
    return route


_ORDER = {"node_entry": "node_router", "node_parse_doc": "node_split", "node_parse_table": "node_split", "node_parse_cad": "node_split",
          "node_split": "node_diff", "node_diff": "node_subject", "node_subject": "node_quality_gate", "node_quality_gate": "node_embed",
          "node_embed": "node_stage", "node_stage": "node_verify", "node_verify": "node_switch", "node_switch": "node_archive", "node_archive": END}


def _build():
    g = StateGraph(IngestState)
    nodes = [node_entry, node_router, node_parse_doc, node_parse_table, node_parse_cad, node_split, node_diff, node_subject, node_quality_gate,
             node_embed, node_stage, node_verify, node_switch, node_archive, node_fail, node_skip]
    for fn in nodes:
        g.add_node(fn.__name__, fn)
    g.set_entry_point("node_entry")
    for name in ["node_router", *_ORDER]:
        targets = {"node_fail", "node_skip"} | ({"node_parse_doc", "node_parse_table", "node_parse_cad"} if name == "node_router" else {_ORDER[name]})
        g.add_conditional_edges(name, _next(name), sorted(t for t in targets if t != END) + ([END] if END in targets else []))
    g.add_edge("node_fail", END)
    g.add_edge("node_skip", END)
    return g.compile()


ingest_app = _build()


def submit(file_path: str, doc_key: str, version: str, meta: dict, batch_id: str | None = None) -> str:
    """登记一个导入任务，返回任务号。属于某个批次的任务先排队（QUEUED），由消息队列的消费者来执行"""
    job_id = "J" + datetime.now().strftime("%y%m%d%H%M%S") + uuid.uuid4().hex[:6]
    store.execute("INSERT INTO kb_ingest_job (job_id, doc_key, version, file_name, file_path, meta, stage, status, batch_id) VALUES (%s,%s,%s,%s,%s,%s,'NEW',%s,%s)",
                  (job_id, doc_key, str(version), Path(file_path).name, str(file_path), store.dumps(meta), "QUEUED" if batch_id else "RUNNING", batch_id))
    return job_id


def run(job_id: str) -> dict:
    """执行（或重试）一个任务。返回任务的最新状态"""
    job = store.one("SELECT * FROM kb_ingest_job WHERE job_id = %s", (job_id,))
    work = WORK_DIR / job_id
    work.mkdir(parents=True, exist_ok=True)
    store.execute("UPDATE kb_ingest_job SET status = 'RUNNING', next_retry_at = NULL WHERE job_id = %s", (job_id,))
    meta = json.loads(job["meta"]) if isinstance(job["meta"], str) else job["meta"]
    final = ingest_app.invoke({"job_id": job_id, "file_path": job["file_path"], "file_name": job["file_name"], "work_dir": str(work),
                               "doc_key": job["doc_key"], "version": job["version"], "meta": meta}, {"recursion_limit": 40})
    out = store.one("SELECT job_id, doc_key, version, file_name, stage, status, attempts, error, next_retry_at FROM kb_ingest_job WHERE job_id = %s", (job_id,))
    out.update(chunks=len(final.get("chunks", [])), params=len(final.get("params", [])), route=final.get("route"), parsed_by=final.get("parsed_by"),
               diff=final.get("diff"), subject=final.get("subject_stats"), images=len(final.get("images") or []), clean=final.get("clean_stats"),
               gate=final.get("gate"), verify=final.get("verify"), issues=final.get("issues", []), replaced=final.get("replaced"))
    return out


def ingest(file_path: str, doc_key: str, version: str, meta: dict) -> dict:
    return run(submit(file_path, doc_key, version, meta))


def retry_due(now: datetime | None = None) -> list[dict]:
    """把到了重试时间的任务再跑一遍"""
    now = now or datetime.now()
    # 单个上传的任务到点就重试；批量任务的重试由消息队列的延迟队列负责，这里只兜底「到点 15 分钟还没人处理」的（队列出过故障）
    rows = store.query("SELECT job_id, batch_id FROM kb_ingest_job WHERE status = 'RETRY' AND ((batch_id IS NULL AND next_retry_at <= %s) "
                       "OR (batch_id IS NOT NULL AND next_retry_at <= %s)) ORDER BY next_retry_at LIMIT 10", (now, now - timedelta(minutes=15)))
    out = [run(r["job_id"]) for r in rows]
    for b in {r["batch_id"] for r in rows if r["batch_id"]}:
        from app.kbase.ingest import batchjob
        batchjob.refresh(b)
    return out


_worker_started = False


def start_retry_worker(interval: int = 15) -> None:
    global _worker_started
    if _worker_started:
        return
    _worker_started = True

    def loop():
        while True:
            try:
                retry_due()
            except Exception:
                logger.exception("[kbase] 重试任务异常")
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="kb-retry").start()
