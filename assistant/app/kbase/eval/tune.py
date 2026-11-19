"""
单变量调参：固定一组基线配置，每次只改一个变量，看检索和上下文质量怎么变。评测集是固定的 data/kbase_eval/dataset.json（不重新生成，前后可比）。

  变量      召回路数（三路 / 去掉 HyDE / 只稠密 / 只 BM25 / 稠密+稀疏）、每路召回条数、TopK（断崖截断的上限）、阈值（最低分、断崖落差）、
            是否重排、chunk size（400 / 600 / 800，在临时集合里重新切分和向量化，不动线上数据）
  第一层    检索指标，不调大模型：标准章节在最终给模型的章节里的名次 → Hit@1/3/5、MRR；平均保留几个章节；保留章节里标准章节的占比；
            「召回了但被截断丢掉」的比例
  第二层    RAGAS（大模型当评委）：上下文精确率、上下文召回率。只看检索出的上下文，不需要生成回答，所以 chunk size 也能比；
            抽样 N 题（--ragas N），每个变体同一批题

用法（知识库服务保持运行，借用服务里的向量和重排模型）：
  KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python -m app.kbase.eval.tune                 第一层，全部变量
  KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python -m app.kbase.eval.tune --ragas 40      两层都跑，RAGAS 抽 40 题
  ... --only topk,rerank                                                                       只跑部分变量
结果：data/kbase_eval/tune.json、tune.md
"""
import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

from app.kbase import store, vectors
from app.kbase.eval import dataset
from app.kbase.ingest import split as split_service
from app.kbase.query import intent, retrieve, topic
from app.shared.runtime.logger import logger

DIR = dataset.PATH.parent
BASE = {"routes": "multi", "recall_k": 10, "topk": 3, "min_score": 0.05, "gap_abs": 0.25, "rerank": True, "chunk_size": 600}
VARIABLES = {
    "routes": ["multi", "no_hyde", "dense", "bm25", "hybrid"],
    "recall_k": [5, 10, 30, 50],
    "topk": [2, 3, 5, 8],
    "min_score": [0.02, 0.05, 0.1, 0.2],
    "gap_abs": [0.15, 0.25, 0.4],
    "rerank": [True, False],
    "chunk_size": [400, 600, 800],
}
CN = {"routes": "召回路数", "routes_norerank": "召回路数（关掉重排）", "recall_k": "每路召回条数", "topk": "TopK 上限", "min_score": "最低分阈值", "gap_abs": "断崖落差阈值", "rerank": "重排", "chunk_size": "chunk size"}
ROUTE_CN = {"multi": "三路（稠密+HyDE+BM25）", "no_hyde": "稠密+BM25", "dense": "只稠密", "bm25": "只 BM25", "hybrid": "稠密+稀疏（旧方案）"}
_cache_file = DIR / "tune_cache.json"


# ---------------------------------------------------------------- 题目和标准章节

def cases() -> list[dict]:
    """说明类、混合类、且有标准章节的题。标准章节按「文档号 + 章节标题」映射到当前生效版本（文档升过版本后章节编号会变）"""
    rows = [c for c in dataset.load() if c["kind"] in ("DESC", "MIXED") and c.get("gold_parent")]
    old = {r["parent_id"]: r for r in store.query("SELECT parent_id, doc_key, title FROM kb_parent WHERE parent_id IN %s", ([c["gold_parent"] for c in rows],))}
    active = {(r["doc_key"], r["title"]): r["parent_id"] for r in store.query(
        "SELECT p.parent_id, p.doc_key, p.title FROM kb_parent p JOIN kb_document d ON d.doc_key = p.doc_key AND d.active_version = p.version")}
    out = []
    for c in rows:
        o = old.get(c["gold_parent"])
        gold = active.get((o["doc_key"], o["title"])) if o else None
        if gold:
            out.append({**c, "gold": gold})
    return out


def _load_cache() -> dict:
    try:
        return json.loads(_cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"hyde": {}, "rerank": {}}


def _save_cache(c: dict) -> None:
    _cache_file.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")


def hyde_all(items: list[dict], cache: dict) -> None:
    """每道题的改写和假设答案只生成一次，缓存到文件（跑多少遍调参都不用再调模型）"""
    from app.copilot import llm
    todo = [c for c in items if c["id"] not in cache["hyde"]]

    def one(c):
        try:
            r, h = intent.rewrite_hyde(c["question"], [], "", "", llm.Usage())
        except Exception as e:
            logger.warning(f"[tune] {c['id']} HyDE 失败：{e}")
            r, h = c["question"], ""
        return c["id"], {"rewritten": r, "hyde": h}
    with ThreadPoolExecutor(6) as pool:
        for cid, v in pool.map(one, todo):
            cache["hyde"][cid] = v
    if todo:
        _save_cache(cache)
        logger.info(f"[tune] 生成假设答案 {len(todo)} 题")


# ---------------------------------------------------------------- chunk size：临时集合

def build_chunk_collection(size: int) -> str:
    """把当前生效版本的父章节按新的 chunk size 重新切，向量化后写进临时集合 kb_tune_cs<size>。线上集合和 SQL 不动"""
    name = f"kb_tune_cs{size}"
    from app.infra.vector_store.milvus_gateway import milvus_gateway
    c = milvus_gateway.client
    if c.has_collection(name) and c.query(name, filter="", output_fields=["chunk_id"], limit=1):
        return name
    old = (split_service.CHUNK_SIZE, split_service.CHUNK_MIN, split_service.CHUNK_MAX_SIZE)
    split_service.CHUNK_SIZE, split_service.CHUNK_MIN, split_service.CHUNK_MAX_SIZE = size, size // 3, int(size * 1.5)
    try:
        parents = store.query("SELECT p.parent_id, p.doc_key, p.version, p.title AS section, p.page, p.content, d.title, d.doc_type, d.security_level, "
                              "d.sec_level, d.acl_groups, d.product_code FROM kb_parent p JOIN kb_document d ON d.doc_key = p.doc_key AND d.active_version = p.version")
        chunks = []
        for p in parents:
            body = p["content"].partition("\n")[2] if p["content"].startswith("【") else p["content"]
            if not body.strip():                                   # 只有标题、没有正文的章节
                continue
            for i, ch in enumerate(split_service.build_chunks([{"title": p["section"], "content": body}], p["title"])):
                chunks.append({"chunk_id": f"{p['parent_id'][:24]}{size:04d}{i:04d}", "parent": p, "content": ch["content"][:7900]})
    finally:
        split_service.CHUNK_SIZE, split_service.CHUNK_MIN, split_service.CHUNK_MAX_SIZE = old
    if c.has_collection(name):
        c.drop_collection(name)
    idx = c.prepare_index_params()
    milvus_gateway._vector_indexes(idx)
    idx.add_index(field_name="bm25_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    c.create_collection(name, schema=vectors.schema(), index_params=idx)
    from app.infra.llm.providers import llm_providers
    from app.kbase import authz
    for i in range(0, len(chunks), 64):
        part = chunks[i:i + 64]
        emb = llm_providers.generate_embeddings([x["content"] for x in part])
        rows = [{"chunk_id": x["chunk_id"], "doc_key": x["parent"]["doc_key"], "version": x["parent"]["version"], "doc_type": x["parent"]["doc_type"],
                 "title": x["parent"]["title"][:390], "section": x["parent"]["section"][:390], "page": int(x["parent"]["page"] or 0),
                 "item_name": (x["parent"]["product_code"] or "")[:190], "series": "", "parent_id": x["parent"]["parent_id"],
                 "security_level": x["parent"]["security_level"], "level": x["parent"]["sec_level"] if x["parent"]["sec_level"] is not None else -1,
                 "acl": authz.parse_acl(x["parent"]["acl_groups"])[:32], "state": "VALID", "content": x["content"],
                 "dense_vector": emb["dense"][n], "sparse_vector": emb["sparse"][n]} for n, x in enumerate(part)]
        c.insert(name, rows)
    c.flush(name)
    c.load_collection(name)
    logger.info(f"[tune] chunk size {size}：{len(parents)} 个章节切成 {len(chunks)} 块，写入 {name}")
    return name


# ---------------------------------------------------------------- 召回（每题每个集合只查一次，各变体共用）

ROUTE_SETS = {"multi": ("dense", "hyde", "bm25"), "no_hyde": ("dense", "bm25"), "dense": ("dense",), "bm25": ("bm25",), "hybrid": ("dense", "sparse")}


def recall_lists(c: dict, q: dict, collection: str) -> dict[str, list[dict]]:
    """一道题在某个集合里四路各自的 Top50（不融合）。主题过滤和线上一致"""
    plan = intent.classify(q["rewritten"])
    lock = topic.resolve(q["rewritten"], plan, None, False)
    models = lock["models"] or None
    series = lock.get("series") if not models else None
    expr = retrieve._expr(c["role"], models, series)
    e = retrieve.embed_many([x for x in (q["rewritten"], q["hyde"]) if x])
    pay = {"dense": e[0]["dense"], "sparse": e[0]["sparse"], "bm25": q["rewritten"], "hyde": e[1]["dense"] if q["hyde"] else None}
    field = {"dense": "dense_vector", "hyde": "dense_vector", "sparse": "sparse_vector", "bm25": "bm25_vector"}
    old, vectors._ready = settings_name(collection), False
    try:
        out = {r: vectors.search(pay[r], field[r], expr, 50) for r in ("dense", "hyde", "sparse", "bm25") if pay[r] is not None}
        if (models or series) and len({h["chunk_id"] for hs in out.values() for h in hs}) < 3:
            expr = retrieve._expr(c["role"], None, None)
            out = {r: vectors.search(pay[r], field[r], expr, 50) for r in out}
    finally:
        settings_name(old)
        vectors._ready = False
    return out


def settings_name(name: str) -> str:
    """临时把向量集合名换掉（settings 是不可变对象，用 object.__setattr__）"""
    from app.infra.config.settings import settings
    old = settings.kbase_collection
    object.__setattr__(settings, "kbase_collection", name)
    return old


def rerank_scores(c: dict, query: str, hits: list[dict], cache: dict) -> dict[str, float]:
    key = c["id"]
    have = cache["rerank"].setdefault(key, {})
    todo = [h for h in hits if h["chunk_id"] not in have]
    if todo:
        for h, s in zip(todo, retrieve.rerank_scores(query, todo)):
            have[h["chunk_id"]] = float(s)
    return have


# ---------------------------------------------------------------- 一个变体的结果

def run_variant(cfg: dict, items: list[dict], lists: dict, cache: dict) -> dict:
    ranks, kept_n, kept_prec, cut, contexts, in_pool = [], [], [], 0, {}, 0
    for c in items:
        q = cache["hyde"][c["id"]]
        L = lists[(c["id"], cfg["chunk_size"])]
        fused = retrieve.rrf({r: L[r][:cfg["recall_k"]] for r in ROUTE_SETS[cfg["routes"]] if r in L})      # 每路取前 recall_k 条再融合
        if cfg["rerank"]:
            sc = rerank_scores(c, q["rewritten"], fused, cache)
            ranked = sorted(({**h, "score": sc[h["chunk_id"]]} for h in fused), key=lambda x: -x["score"])
            final = retrieve.dynamic_topk(ranked, max_k=cfg["topk"], min_k=2, gap_abs=cfg["gap_abs"], min_score=cfg["min_score"])
        else:
            final = [{**h, "score": h["rrf"]} for h in fused[:cfg["topk"]]]
        order = list(dict.fromkeys(h["parent_id"] for h in final))
        in50 = c["gold"] in {h["parent_id"] for h in fused}
        in_pool += in50
        rank = order.index(c["gold"]) + 1 if c["gold"] in order else None
        if in50 and rank is None:
            cut += 1
        ranks.append(rank)
        kept_n.append(len(order))
        kept_prec.append(1.0 / len(order) if rank else 0.0)
        contexts[c["id"]] = order
    n = len(items)
    return {"hit@1": round(sum(1 for r in ranks if r == 1) / n, 4), "hit@3": round(sum(1 for r in ranks if r and r <= 3) / n, 4),
            "hit@5": round(sum(1 for r in ranks if r and r <= 5) / n, 4), "mrr": round(sum(1 / r for r in ranks if r) / n, 4),
            "kept_avg": round(sum(kept_n) / n, 2), "gold_share": round(sum(kept_prec) / n, 4), "cut_by_cliff": round(cut / n, 4),
            "recall@50": round(in_pool / n, 4), "_contexts": contexts}


def variants(only: list[str] | None) -> list[tuple[str, object, dict]]:
    out = [("baseline", None, dict(BASE))]
    for var, values in VARIABLES.items():
        if only and var not in only:
            continue
        for v in values:
            if v != BASE[var]:
                out.append((var, v, {**BASE, var: v}))
    if not only or "routes" in only:
        # 辅助对比：开着重排时，候选池里只要有标准章节，最终顺序由重排决定，各种召回路数的差别会被抹平；
        # 关掉重排单看召回和融合本身，才能看出每一路的贡献（这一组同时改了两个变量，单独列出）
        out += [("routes_norerank", v, {**BASE, "routes": v, "rerank": False}) for v in VARIABLES["routes"]]
    return out


# ---------------------------------------------------------------- RAGAS（只看上下文）

def ragas_contexts(rows: list[tuple[str, dict]], items: list[dict], n: int) -> dict:
    """每个变体在同一批 n 道题上算 RAGAS 上下文精确率和召回率（评委是 .env 里的主模型）"""
    from app.kbase.eval.runner import _ragas_imports
    _ragas_imports()
    from langchain_openai import ChatOpenAI
    from ragas import EvaluationDataset, evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import LLMContextPrecisionWithReference, LLMContextRecall
    from ragas.run_config import RunConfig
    from app.infra.config.settings import settings
    random.seed(7)
    sample = random.sample(items, min(n, len(items)))
    parents = {r["parent_id"]: r["content"] for r in store.query("SELECT parent_id, content FROM kb_parent WHERE parent_id IN %s",
                                                                  (list({p for _, res in rows for c in sample for p in res["_contexts"][c["id"]]}) or [""],))}
    judge = LangchainLLMWrapper(ChatOpenAI(model=settings.llm_model, base_url=settings.llm_base_url, api_key=settings.llm_api_key, temperature=0, timeout=120, max_retries=3))
    out, done = {}, {}
    cache_file = DIR / "tune_ragas_cache.json"
    try:
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    for label, res in rows:
        # 给模型的上下文和已经评过的某个变体完全一样（例如开着重排时各种召回路数排出来的章节相同）：直接沿用那次的分数
        sig = json.dumps([res["_contexts"][c["id"]] for c in sample])
        if sig in done:
            out[label] = {**out[done[sig]], "same_as": done[sig]}
            continue
        if sig in saved:
            out[label] = saved[sig]
            done[sig] = label
            continue
        data = EvaluationDataset.from_list([{"user_input": c["question"], "reference": c["ground_truth"],
                                             "retrieved_contexts": [parents[p][:3000] for p in res["_contexts"][c["id"]] if p in parents] or ["（没有检索到资料）"]}
                                            for c in sample])
        r = evaluate(data, metrics=[LLMContextPrecisionWithReference(), LLMContextRecall()], llm=judge,
                     run_config=RunConfig(max_workers=16, timeout=300, max_retries=4), raise_exceptions=False, show_progress=False).to_pandas()

        def m(col):
            vals = [float(x) for x in r[col] if x == x] if col in r else []
            return (round(sum(vals) / len(vals), 4) if vals else None), len(sample) - len(vals)
        (p, fp), (rc, fr) = m("llm_context_precision_with_reference"), m("context_recall")
        out[label] = saved[sig] = {"context_precision": p, "context_recall": rc, "failed": fp + fr}
        done[sig] = label
        cache_file.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        logger.info(f"[tune] RAGAS {label}：精确率 {p}，召回率 {rc}")
    return {"n": len(sample), "judge": settings.llm_model, "results": out}


# ---------------------------------------------------------------- 主流程

def main(only: list[str] | None = None, ragas_n: int = 0) -> dict:
    t0 = time.time()
    items = cases()
    cache = _load_cache()
    hyde_all(items, cache)
    vs = variants(only)
    sizes = sorted({cfg["chunk_size"] for _, _, cfg in vs})
    lists = {}
    for size in sizes:
        coll = vectors.name() if size == BASE["chunk_size"] else build_chunk_collection(size)
        for c in items:
            lists[(c["id"], size)] = recall_lists(c, cache["hyde"][c["id"]], coll)
        logger.info(f"[tune] chunk size {size} 召回完成")
    results = []
    for var, val, cfg in vs:
        r = run_variant(cfg, items, lists, cache)
        results.append({"variable": var, "value": val, "config": cfg, **r})
        logger.info(f"[tune] {var}={val}：Hit@1 {r['hit@1']} Hit@3 {r['hit@3']} MRR {r['mrr']} 平均保留 {r['kept_avg']}")
    _save_cache(cache)
    out = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "n": len(items), "baseline": BASE, "seconds": None,
           "results": [{k: v for k, v in r.items() if k != "_contexts"} for r in results]}
    if ragas_n:
        labels = [(_label(r), r) for r in results]
        out["ragas"] = ragas_contexts(labels, items, ragas_n)
    out["seconds"] = int(time.time() - t0)
    (DIR / "tune.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (DIR / "tune.md").write_text(markdown(out), encoding="utf-8")
    return out


def _label(r: dict) -> str:
    return "基线" if r["variable"] == "baseline" else f"{CN[r['variable']]}={ROUTE_CN.get(r['value'], r['value'])}"


def markdown(out: dict) -> str:
    rg = (out.get("ragas") or {}).get("results", {})
    lines = [f"# 单变量调参结果（{out['generated_at']}）", "",
             f"题目：评测集里有标准章节的说明类和混合类问题 {out['n']} 道。基线：三路召回各 Top10、精排后最多 3 条、最低分 0.05、断崖落差 0.25、重排、chunk size 600。每行只改一个变量。", "",
             "| 变量 | 取值 | 召回@K | Hit@1 | Hit@3 | Hit@5 | MRR | 平均保留章节 | 标准章节占比 | 被截断丢掉 |" + (" RAGAS 上下文精确率 | RAGAS 上下文召回率 |" if rg else ""),
             "|---|---|---|---|---|---|---|---|---|---|" + ("---|---|" if rg else "")]
    for r in out["results"]:
        lab = _label(r)
        x = rg.get(lab, {})
        lines.append(f"| {'基线' if r['variable'] == 'baseline' else CN[r['variable']]} | {'—' if r['variable'] == 'baseline' else ROUTE_CN.get(r['value'], r['value'])} | "
                     f"{r['recall@50']:.1%} | {r['hit@1']:.1%} | {r['hit@3']:.1%} | {r['hit@5']:.1%} | {r['mrr']:.3f} | {r['kept_avg']} | {r['gold_share']:.1%} | {r['cut_by_cliff']:.1%} |"
                     + (f" {x.get('context_precision', '-')} | {x.get('context_recall', '-')} |" + (f"（上下文同「{x['same_as']}」）" if x.get("same_as") else "") if rg else ""))
    if rg:
        lines += ["", f"RAGAS 抽样 {out['ragas']['n']} 题（每个变体同一批题），评委模型 {out['ragas']['judge']}。"]
    lines += ["", "说明：Hit@K 和 MRR 按章节算（最终给大模型的是整章）；「标准章节占比」= 命中时 1/保留章节数，反映上下文里的噪声；"
              "「召回@K」= 标准章节进了精排候选池；「被截断丢掉」= 标准章节在候选池里、但被断崖截断或 TopK 去掉了。chunk size 的变体在临时集合里重新切分和向量化，线上数据没动。"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--ragas", type=int, default=0)
    a = ap.parse_args()
    if os.environ.get("KBASE_EVAL_REMOTE") == "1":
        from app.kbase.eval.runner import use_server_models
        use_server_models()
    res = main([x for x in a.only.split(",") if x] or None, a.ragas)
    print((DIR / "tune.md").read_text(encoding="utf-8"))
