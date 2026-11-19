"""
向量检索：多路召回 → RRF 融合 → 交叉编码器重排 → 分数断崖截断（最多 3 条）。

  三路召回，每路 Top10（合并去重后最多 30 条进精排）：
    dense    改写后的问题的 BGE-M3 稠密向量         语义相近
    hyde     假设答案（HyDE）的稠密向量              问题和答案说法差得远时（「发烫怎么办」↔「过热报警处理」）靠它
    bm25     Milvus 内置 BM25（中文分词）            型号、报警代码、参数名这类要字面命中的
  RRF：每路按名次给分 1/(60+名次)，加起来排序。不用分数加权，因为三路的分数不在一个尺度上
  权限前置过滤：先按可见范围（acl + 密级）和状态（只看 VALID）过滤，再取 TopK，不是召回之后再丢
  只认生效的切片：结果回来后按 SQL 的状态再过滤一遍，并换上生效版本的元数据（防止 Milvus 状态同步慢一步的窗口期）
  主题过滤：锁定了型号按 item_name 过滤；只锁定系列时按 series 过滤（没标系列的通用资料不排除）；过滤后太少就放开再召回一次
  降级：HyDE 生成失败就少这一路；向量库不可用退到 MySQL 全文检索；重排排不上队就直接取融合后的前几条
"""
import re
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from app.infra.llm.providers import llm_providers
from app.infra.vector_store.milvus_gateway import escape
from app.kbase import authz, store, vectors
from app.kbase.ingest import versioning
from app.platform import resilience as R
from app.platform import tracing
from app.shared.runtime.logger import logger
from app.kbase.query import intent
from app.copilot import llm

RECALL = 10           # 每一路召回多少条（单变量调参见 eval/tune.py：演示库上每路 10 条时标准章节进候选池的比例和 50 条一样）
FINAL = 3            # 精排后最多留几条（调参：3 条比 5 条噪声少，又不像 2 条那样开始丢命中）
RRF_K = 60
MILVUS = R.Breaker("milvus", "Milvus 向量库", fail_max=3, reset_seconds=30)
RERANK_POOL = R.Bulkhead("rerank", "重排模型", limit=1, queue=4, wait_seconds=45)      # 重排一次只能跑一个；最多 4 个排队，等超过 45 秒就不排了
_model_lock = threading.Lock()          # 向量化和重排都在本机跑模型，并发请求排队用，避免互相抢 CPU
_pool = ThreadPoolExecutor(6, thread_name_prefix="kb-recall")
ROUTES = {"multi": ("dense", "hyde", "bm25"), "no_hyde": ("dense", "bm25"), "dense": ("dense",), "bm25": ("bm25",), "hyde": ("hyde",),
          "hybrid": ("dense", "sparse"), "sparse": ("sparse",), "all": ("dense", "hyde", "bm25", "sparse")}


def dynamic_topk(docs: list[dict], max_k: int = FINAL, min_k: int = 2, gap_abs: float = 0.25, gap_ratio: float = 0.5,
                 min_score: float = 0.05) -> list[dict]:
    """docs 已按分数降序。先按最低分过滤（至少保留 1 条），再在分数断崖处截断：后面的明显差一截就不要了"""
    kept = [d for d in docs if d["score"] >= min_score] or docs[:1]
    upper = min(max_k, len(kept))
    k = upper
    for i in range(min_k - 1, upper - 1):
        cur, nxt = kept[i]["score"], kept[i + 1]["score"]
        if cur - nxt > gap_abs or (cur > 0 and (cur - nxt) / cur > gap_ratio):
            k = i + 1
            break
    return kept[:k]


def embed_many(texts: list[str]) -> list[dict]:
    """问题、假设答案的向量。同一段文字一天内只算一次"""
    out: list[dict | None] = [None] * len(texts)
    todo = []
    for i, t in enumerate(texts):
        hit = R.cache_get("kbase", "embedding", R.cache_key(t))
        if hit:
            out[i] = {"dense": hit["dense"], "sparse": {int(k): v for k, v in hit["sparse"].items()}}
        else:
            todo.append(i)
    if todo:
        with _model_lock:
            e = llm_providers.generate_embeddings([texts[i] for i in todo])
        for n, i in enumerate(todo):
            v = {"dense": [float(x) for x in e["dense"][n]], "sparse": {int(k): float(x) for k, x in e["sparse"][n].items()}}
            R.cache_set("kbase", "embedding", R.cache_key(texts[i]), v, 86400)
            out[i] = v
    return out  # type: ignore[return-value]


def embed(query: str) -> dict:
    return embed_many([query])[0]


def _expr(scope, models: list[str] | None, series: str | None = None) -> str:
    parts = [authz.as_scope(scope).milvus_expr()]
    if models:
        parts.append("item_name in [" + ", ".join(f'"{escape(m)}"' for m in models) + "]")
    elif series:
        parts.append(f'(series == "{escape(series)}" or series == "")')
    return " and ".join(f"({p})" for p in parts)


def explicit_documents(question: str, scope) -> list[str]:
    """Resolve document names explicitly mentioned in a question within the user's visible corpus."""
    if not re.search(r"[A-Za-z]", question):
        return []
    where, args = authz.as_scope(scope).sql("d")
    rows = store.query(f"SELECT d.doc_key, d.title FROM kb_document d WHERE d.active_version IS NOT NULL AND {where}", args)
    text = question.casefold()
    selected = []
    for row in rows:
        for filename in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.pdf", row["title"], re.I):
            stem = filename[:-4].casefold()
            named_file = filename.casefold() in text
            named_code = len(stem) >= 6 and bool(re.search(r"\d", stem)) and bool(
                re.search(r"(?<![a-z0-9])" + re.escape(stem) + r"(?![a-z0-9])", text)
            )
            if named_file or named_code:
                selected.append(row["doc_key"])
                break
    return list(dict.fromkeys(selected))


def literal_recall(question: str, ranking_query: str, scope, doc_keys: list[str] | None = None,
                   limit: int = RECALL) -> list[dict]:
    """Add exact identifiers and signal names alongside semantic retrieval."""
    terms = [code.split('□')[0].split('■')[0] for code in intent.find_external_identifiers(question)]
    excluded = {'PDF', 'BOM', 'CAD', 'DXF', 'VDC', 'VAC', 'RPM', 'HZ', 'AC', 'DC', 'SQL', 'API',
                'IS', 'OF', 'TO', 'IN', 'ON', 'OR', 'IF', 'AN', 'AT'}
    for word in re.findall(r'(?<![A-Za-z0-9])[A-Z]{2,}(?![A-Za-z0-9])', question):
        if word not in excluded and not any(word in term for term in terms):
            terms.append(word)
    terms += re.findall(r'(?<![0-9.])(\d+(?:\.\d+)?)\s*mm', question, re.I)
    terms = list(dict.fromkeys(term for term in terms if len(term) >= 2))
    if not terms:
        return []
    where, args = authz.as_scope(scope).sql('d')
    if doc_keys:
        titles = store.query(f'SELECT d.title FROM kb_document d WHERE d.doc_key IN %s AND {where}', (doc_keys, *args))
        terms = [term for term in terms if not any(term.casefold() in row['title'].casefold() for row in titles)]
    if not terms:
        return []
    conditions = ' OR '.join('p.content LIKE %s' for _ in terms)
    sql = ('SELECT c.chunk_id,c.doc_key,c.version,d.doc_type,d.title,c.section,c.page,c.item_name,c.series,c.parent_id,'
           'd.security_level,c.content,p.content AS parent_content,MATCH(c.content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS lexical_score '
           'FROM kb_chunk c JOIN kb_document d ON d.doc_key=c.doc_key AND d.active_version=c.version '
           'JOIN kb_parent p ON p.parent_id=c.parent_id '
           f"WHERE c.state='VALID' AND {where} AND ({conditions})")
    params = [ranking_query, *args, *['%' + term + '%' for term in terms]]
    if doc_keys:
        sql += ' AND d.doc_key IN %s'
        params.append(doc_keys)
    rows = store.query(sql + ' ORDER BY lexical_score DESC,c.chunk_id LIMIT %s', (*params, max(240, limit)))
    ranked = []
    seen_parents = set()
    for row in rows:
        if row['parent_id'] in seen_parents:
            continue
        seen_parents.add(row['parent_id'])
        body = re.sub(r'^【[^】]*】', '', row.get('parent_content', row['content'])).upper()
        title = row['title'].upper()
        coverage = sum(bool(re.search(r'(?<![A-Z0-9])' + re.escape(term) + r'(?![A-Z0-9])', body)) if len(term) == 2
                       else term in body for term in terms if term not in title)
        if coverage:
            ranked.append({**row, 'literal_coverage': coverage})
    ranked.sort(key=lambda row: (row['literal_coverage'], float(row['lexical_score'])), reverse=True)
    return ranked[:limit]


def _search(*args):
    """向量检索。失败时顺手把连接重置掉：Milvus 重启过的话，旧连接不会自己恢复，要重新连"""
    try:
        return vectors.single_search(*args)
    except Exception:
        from app.infra.vector_store.milvus_gateway import milvus_gateway
        milvus_gateway.reset()
        raise


def _route(name: str, payload, expr: str, limit: int) -> list[dict]:
    field = {"dense": "dense_vector", "hyde": "dense_vector", "sparse": "sparse_vector", "bm25": "bm25_vector"}[name]
    return MILVUS.call(R.run_with_timeout, f"召回·{name}", 8, _search, payload, field, expr, limit)


def rrf(lists: dict[str, list[dict]]) -> list[dict]:
    fused: dict[str, dict] = {}
    for route, hits in lists.items():
        for rank, h in enumerate(hits):
            item = fused.setdefault(h["chunk_id"], {**h, "rrf": 0.0, "routes": {}})
            item["rrf"] += 1.0 / (RRF_K + rank + 1)
            item["routes"][route] = rank + 1
            if route == "literal":
                item["content"] = h["content"]
    return sorted(fused.values(), key=lambda x: -x["rrf"])


def _only_valid(hits: list[dict]) -> list[dict]:
    """按 SQL 的状态再过滤一遍，并换上生效版本的元数据"""
    valid = versioning.valid_chunks([h["chunk_id"] for h in hits])
    return [{**h, **{k: v for k, v in valid[h["chunk_id"]].items() if k != "chunk_id"}} for h in hits if h["chunk_id"] in valid]


def _recall_view(out: list[dict]) -> dict:
    by = {}
    for h in out:
        for r in h.get("routes", {}):
            by[r] = by.get(r, 0) + 1
    return {"fused": len(out), "by_route": by, "top": [{"doc": h.get("title"), "section": h.get("section"), "routes": h.get("routes"),
                                                       "rrf": round(h["rrf"], 4)} for h in out[:10]]}


@tracing.observe("recall", output=_recall_view, input=lambda queries, scope, models=None, series=None, mode="multi", limit=RECALL, **kw:
                 {"queries": {k: (v or "")[:300] for k, v in queries.items()}, "mode": mode, "models": models, "series": series})
def recall(queries: dict, scope, models: list[str] | None = None, series: str | None = None, mode: str = "multi", limit: int = RECALL,
           embeddings: dict | None = None, doc_keys: list[str] | None = None, literal_question: str | None = None) -> list[dict]:
    """queries：{"rewritten": 改写后的问题, "hyde": 假设答案或空}。mode 见 ROUTES（multi 是默认的三路，其他做消融对比）。
    embeddings 可以把已经算好的向量传进来（{"rewritten": ..., "hyde": ...}）"""
    routes = [r for r in ROUTES[mode] if r != "hyde" or queries.get("hyde")]
    need = (["rewritten"] if {"dense", "sparse"} & set(routes) else []) + (["hyde"] if "hyde" in routes else [])
    embs = dict(embeddings or {})
    missing = [k for k in need if k not in embs]
    if missing:
        for k, v in zip(missing, embed_many([queries[k] for k in missing])):
            embs[k] = v
    payload = {"dense": (embs.get("rewritten") or {}).get("dense"), "hyde": (embs.get("hyde") or {}).get("dense"),
               "sparse": (embs.get("rewritten") or {}).get("sparse"), "bm25": queries["rewritten"]}
    expr = _expr(scope, models, series)
    if doc_keys:
        expr += ' and doc_key in [' + ', '.join(f'"{escape(key)}"' for key in doc_keys) + ']'
    jobs = {r: _pool.submit(_traced_route, tracing.current(), r, payload[r], expr, limit) for r in routes if payload[r]}
    lists = {r: f.result() for r, f in jobs.items()}
    if mode in ('multi', 'all'):
        exact = literal_recall(literal_question or queries['rewritten'], queries['rewritten'], scope, doc_keys, limit)
        if exact:
            lists['literal'] = exact
    out = _only_valid(rrf(lists))                              # 三路各 limit 条，合并去重后全部交给精排
    if (models or series) and len(out) < 3:                    # 按主题过滤后太少：放开主题限制再召回一次
        return recall(queries, scope, None, None, mode, limit, embs, doc_keys, literal_question)
    return out


def _traced_route(ctx: tuple, name: str, payload, expr: str, limit: int) -> list[dict]:
    """每一路在自己的线程里跑；把调用方的链路带过来，三路各记一个子 span"""
    trace, parent = ctx
    token = tracing._current.set((trace, parent)) if trace else None
    try:
        return tracing.observe(f"recall·{name}", output=lambda hits: {"hits": len(hits), "top": [h.get("section") for h in hits[:5]]},
                               input=lambda *a, **k: {"route": name})(_route)(name, payload, expr, limit)
    finally:
        if token is not None:
            tracing._current.reset(token)


def fulltext(query: str, scope, models: list[str] | None = None, limit: int = RECALL, doc_keys: list[str] | None = None) -> list[dict]:
    """降级检索：向量库不可用时，用 MySQL 的全文索引按关键词找。效果不如向量检索，但能用，权限和状态规则不变"""
    where, args = authz.as_scope(scope).sql("d")
    sql = ("SELECT c.chunk_id, c.doc_key, c.version, d.doc_type, d.title, c.section, c.page, c.item_name, c.series, c.parent_id, d.security_level, c.content, "
           "MATCH(c.content) AGAINST(%s IN NATURAL LANGUAGE MODE) AS score FROM kb_chunk c JOIN kb_document d ON d.doc_key = c.doc_key AND d.active_version = c.version "
           f"WHERE c.state = 'VALID' AND MATCH(c.content) AGAINST(%s IN NATURAL LANGUAGE MODE) AND {where}")
    params: list = [query, query, *args]
    if models:
        sql += " AND c.item_name IN %s"
        params.append(models)
    if doc_keys:
        sql += " AND d.doc_key IN %s"
        params.append(doc_keys)
    rows = store.query(sql + " ORDER BY score DESC LIMIT %s", (*params, limit))
    if models and len(rows) < 3:
        return fulltext(query, scope, None, limit, doc_keys)
    return [{**r, "score": float(r["score"]), "rrf": 1.0 / (RRF_K + i + 1), "routes": {"fulltext": i + 1}} for i, r in enumerate(rows)]


def exists_without_acl(query: str, emb: dict, scope) -> bool:
    """不带权限过滤时能不能搜到强相关的内容。用来区分「知识库里没有」和「有但你没权限看」。
    只看当前用户看不到的那几条，并用重排模型确认是真的相关，避免把闲聊也说成「无权限」"""
    scope = authz.as_scope(scope)
    try:
        hits = MILVUS.call(_search, emb["dense"], "dense_vector", "level >= 0", 8)
    except R.Unavailable:
        return False
    hits = _only_valid(hits)
    if not hits:
        return False
    docs = {r["doc_key"]: r for r in store.query("SELECT doc_key, security_level, acl_groups FROM kb_document WHERE doc_key IN %s",
                                                (list({h["doc_key"] for h in hits}),))}
    hidden = [h for h in hits if h["doc_key"] in docs and not scope.can_view(docs[h["doc_key"]]["security_level"], docs[h["doc_key"]]["acl_groups"])]
    if not hidden:
        return False
    with _model_lock:
        scores = llm_providers.rerank_scores(query, [h["content"] for h in hidden])
    return max(scores) >= 0.5


def rerank_scores(query: str, hits: list[dict]) -> list[float]:
    """只打分不截断（评测算名次用）"""
    if not hits:
        return []
    with _model_lock:
        return llm_providers.rerank_scores(query, [h["content"] for h in hits])


def semantic_evidence_rank(query: str, hits: list[dict], top: int, usage=None) -> list[dict]:
    """Select relevant source pages by their subject and requested field, never by evaluation references."""
    groups = {}
    for h in hits:
        groups.setdefault(h.get('parent_id') or h['chunk_id'], []).append(h)
    candidates = list(groups.values())
    excerpts = []
    for index, group in enumerate(candidates, 1):
        passages = list(dict.fromkeys(h['content'] for h in group))
        excerpts.append({'candidate': index, 'document': group[0].get('title'), 'section': group[0].get('section'),
                         'passages': '\n'.join(p[:1800] for p in passages[:2])})
    prompt = ('你负责技术知识库的证据页面选择，不回答问题。请根据用户所问的实际对象、参数、型号和配置，'
              '从候选原文中选出最能回答问题的页面。型号只出现在侧栏导航而正文主体不同的页面应降低优先级。'
              '连接器随机附件应查随机附件清单，不能用另一系列的配件选购说明替代。'
              '若问题未限定交流/直流或减速机类型，选择覆盖有关配置的不同页面；涉及频率时保留交流规格。'
              '产品线或网络连接概览的问题优先选择相关概览表，不要仅凭系列名称选择尺寸页。'
              '以候选原文为准，不引入外部知识。按相关性从高到低返回最多' + str(top) +
              '个候选编号，JSON格式 {"selected":[编号,...]}；没有直接相关资料则返回空数组。\n问题：' + query +
              '\n候选原文：' + json.dumps(excerpts, ensure_ascii=False))
    response = json.loads(llm.invoke(prompt, usage, app='kbase', json_mode=True).content)
    ids = list(dict.fromkeys(response['selected']))
    assert all(isinstance(n, int) and 1 <= n <= len(candidates) for n in ids)
    selected = []
    for rank, n in enumerate(ids[:top]):
        selected.extend({**h, 'score': 0.99 - rank * 0.01} for h in candidates[n - 1][:2])
    return selected


@tracing.observe("rerank", output=lambda kept: [{"doc": h.get("title"), "section": h.get("section"), "score": round(h["score"], 4)} for h in kept],
                 input=lambda query, hits, top=FINAL, use=True, **kw: {"query": query, "candidates": len(hits), "top": top, "use": use})
def rerank(query: str, hits: list[dict], top: int = FINAL, use: bool = True, cliff: dict | None = None, semantic: bool = False, usage=None) -> list[dict]:
    """按最佳片段为章节排序，最多选 top 个章节，每章保留至多两个命中片段。"""
    if not hits:
        return []
    if semantic and use:
        try:
            return semantic_evidence_rank(query, hits, top, usage)
        except Exception as exc:
            logger.warning(f"[kbase] 语义证据选择失败，使用本地重排：{type(exc).__name__}")
    if use:
        groups = {}
        for h in hits:
            groups.setdefault(h.get('parent_id') or h['chunk_id'], []).append(h)
        candidates = [min(group, key=lambda h: h.get('routes', {}).get('literal', 10000)) for group in groups.values()]
        with RERANK_POOL.enter(), _model_lock:
            scores = llm_providers.rerank_scores(query, [h["content"] for h in candidates])
        parent_scores = {(h.get('parent_id') or h['chunk_id']): float(score) for h, score in zip(candidates, scores)}
        ranked = sorted(({**h, "score": parent_scores[h.get('parent_id') or h['chunk_id']]} for h in hits), key=lambda x: -x['score'])
    else:
        ranked = [{**h, "score": h["rrf"]} for h in hits]
    by_parent = {}
    for h in ranked:
        by_parent.setdefault(h.get("parent_id") or h["chunk_id"], []).append(h)
    representatives = [group[0] for group in by_parent.values()]
    if cliff and cliff.get('min_k', 2) > 2:
        family_match = re.search(r'(?<![A-Za-z])([A-Z]{2,4})(?:[0-9][A-Za-z0-9_-]*)?\s*(?:系列|Series)', query)
        if family_match:
            family = family_match[1]
            for h in representatives:
                if re.search(r'(?<![A-Za-z])' + re.escape(family) + r'[A-Za-z]*(?:[-_ ]|\d)', h.get('title', '')):
                    h['score'] += 0.12
            representatives.sort(key=lambda h: -h['score'])
        near = [h for h in representatives if h['score'] >= representatives[0]['score'] - 0.15]
        if len({h.get('doc_key') for h in near}) > 1:
            counts, diverse, excess = {}, [], []
            for h in representatives:
                key = h.get('doc_key')
                counts[key] = counts.get(key, 0) + 1
                (diverse if counts[key] <= 3 else excess).append(h)
            representatives = diverse + excess
    selected = dynamic_topk(representatives, max_k=top, **({"min_k": 2, **(cliff or {})})) if use else representatives[:top]
    if cliff and cliff.get('min_k', 2) > 2:
        literal = [h for h in representatives if h.get('routes', {}).get('literal')]
        signals = [code for code in intent.find_external_identifiers(query)
                   if '+' in code or code.endswith('-') or re.match(r'(?:IN|OUT)-', code)]
        if signals:
            exact = sorted(literal, key=lambda h: h['routes']['literal'])[:6]
            selected = exact + [h for h in selected if h not in exact]
        else:
            for h in literal[:2]:
                if h not in selected:
                    selected.append(h)
    kept = [h for representative in selected
            for h in by_parent[representative.get("parent_id") or representative["chunk_id"]][:2]]
    logger.info(f"[kbase] 重排 {len(hits)} → {len(kept)}：" + "，".join(f"{h['section'][:12]}={h['score']:.2f}" for h in kept))
    return kept


def expand_parents(hits: list[dict], scope) -> list[dict]:
    """命中切片后取它所属的完整章节作为上下文；同一章节只保留一次；展开时再核对一次权限"""
    ids = list({h["parent_id"] for h in hits if h.get("parent_id")})
    if not ids:
        return hits
    where, args = authz.as_scope(scope).sql("d")
    rows = store.query(
        "SELECT p.parent_id, p.content, p.title, p.seq FROM kb_parent p JOIN kb_document d ON d.doc_key = p.doc_key AND d.active_version = p.version "
        f"WHERE p.parent_id IN %s AND {where}", (ids, *args))
    parents = {r["parent_id"]: r for r in rows}
    grouped = {}
    for h in hits:
        pid = h.get("parent_id")
        if pid not in parents:                               # 父章节取不到（没权限或已不是生效版本）的切片丢弃
            continue
        grouped.setdefault(pid, []).append(h)
    out = []
    for pid, matches in grouped.items():
        parent = parents[pid]
        matched_text = "\n\n".join(dict.fromkeys(h["content"] for h in matches))
        # Put every retrieved passage first, including passages beyond a stored parent's truncation.
        context = matched_text
        if parent["content"] not in matched_text:
            context += "\n\n【章节上下文】\n" + parent["content"]
        out.append({**matches[0], "type": "kb", "parent_seq": parent["seq"], "context": context})
    return out
