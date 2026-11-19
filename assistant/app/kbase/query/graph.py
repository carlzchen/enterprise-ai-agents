"""
问答图（LangGraph）：

  node_guard → node_understand → node_topic ─┬─ PARAM → node_sql ─────────────────────────────────────────┐
   输入审核     意图·追问改写      主题确认     ├─ DESC  → node_hyde → node_retrieve → node_rerank ─────────┤
               多轮记忆           锁定型号/系列 └─ MIXED → node_sql → node_hyde → node_retrieve → node_rerank ┤
               （有指代才调模型）  不完整就反问       改写+HyDE  三路召回 RRF  重排+断崖截断(≤3)             ▼
                              node_decide → node_generate → node_verify → node_finish → END
                               三种兜底       32K 预算装上下文  引用校验      输出审核·记录
                                             带引用生成        不过就重答一次

  权限：所有检索路径（向量、结构化参数、父章节展开、图片链接）都用同一个 Scope 过滤（app/kbase/authz.py），模型拿不到没权限的内容
  兜底：没搜到 → 请用户补充；搜到的不相关 → 说明并给出最接近的资料；有资料但没权限 → 明确拒答
  降级：改写+HyDE 失败 → 跳过 HyDE 用原问题检索；向量库不可用 → MySQL 全文检索；重排排不上队 → 直接用融合结果；生成失败 → 摘录原文
  链路：每个节点一个 span，节点里的改写、三路召回、重排、生成再各自嵌套 span（@observe），链路上记文档版本、提示词版本和 token
"""
import json
import re
import time
import uuid
from datetime import datetime
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.copilot import llm
from app.infra.persistence.history_repository import history_repository
from app.infra.config.settings import settings
from app.kbase import authz, kg, store
from app.kbase.query import answer as A, memory, prompts
from app.kbase.query import guard, intent, lookup, querylog, retrieve, topic
from app.platform import metrics, tracing
from app.platform import resilience as R
from app.shared.runtime.logger import logger
from app.shared.utils.sse_utils import SSEEvent, push_to_session

STEP_CN = {"node_guard": "输入检查", "node_understand": "理解问题", "node_topic": "确认主题", "node_sql": "查询结构化参数",
           "node_hyde": "改写和假设答案", "node_retrieve": "检索文档",
           "node_rerank": "重排序", "node_decide": "整理资料", "node_generate": "生成回答", "node_verify": "校验引用", "node_finish": "保存记录"}
HISTORY_TURNS = 6


class QueryState(TypedDict, total=False):
    request_id: str
    session_id: str
    trace_id: str
    trace: object             # Langfuse 链路（没配置时是个空壳）
    username: str
    role: str
    scope: object             # authz.Scope：这个人能看什么（角色 / 部门 / 密级）
    question: str
    stream: bool
    options: dict             # mode: hybrid/dense/sparse；rerank: bool；skip_answer；no_log
    usage: llm.Usage
    t0: float
    latency: dict
    history: list
    summary: str
    rewritten: str
    plan: dict                # {route, models, param_keys, series, want_bom, superlative, compare}
    lock: dict                # 主题确认的结果 {models, series, source, candidates}
    hyde: str                 # 假设答案（只用于检索）
    pack: dict                # 上下文预算明细
    notes: list               # 需要告诉用户的说明（例如没有金蝶账号查不了 BOM）
    facts: list               # 结构化事实（SQL / 金蝶 / 图谱）
    hits: list                # 向量召回
    emb: dict
    docs: list                # 重排并展开父章节后的文档
    sources: list
    status: str               # OK / EMPTY / LOW / DENIED / BLOCKED / DEGRADED / CLARIFY
    degraded: list            # 这次请求走了哪些降级（展示给用户，也进监控）
    cache_hit: bool
    answer: str
    verify_note: str
    masked: int
    answer_id: str


# 大模型的熔断、备用模型切换和并发闸门都在 app/copilot/llm.py 里（和问数共用）；这里只负责超时和降级
ANSWER_TTL = 600


def degrade(state: QueryState, kind: str, note: str) -> None:
    state["degraded"].append(note)
    metrics.DEGRADED.labels("kbase", kind).inc()
    emit(state, "degraded", {"notes": state["degraded"]})
    logger.warning(f"[kbase] 降级（{kind}）：{note}")


def emit(state: QueryState, event: str, data: dict) -> None:
    if state.get("stream"):
        push_to_session(state["request_id"], event, data)


def _trace_view(step: str, out: dict) -> dict:
    """每一步往链路里记什么：只记排查问题用得上的，不把整段上下文都塞进去"""
    view = {k: out[k] for k in ("rewritten", "plan", "lock", "hyde", "pack", "status", "verify_note", "cache_hit") if k in out}
    if "facts" in out:
        view["facts"] = [f"{f['title']}：{f['content'][:200]}" for f in out["facts"]]
    if "hits" in out:
        view["hits"] = [{"doc": h.get("title"), "section": h.get("section"), "version": h.get("version"), "routes": h.get("routes"),
                         "rrf": round(h.get("rrf", 0), 4)} for h in out["hits"][:15]]
        view["hit_count"] = len(out["hits"])
    if "docs" in out:
        view["docs"] = [{"doc": d.get("title"), "section": d.get("section"), "score": round(d.get("score", 0), 4)} for d in out["docs"]]
    if "sources" in out:
        view["sources"] = [{"index": x["index"], "type": x["type"], "doc": x.get("title"), "section": x.get("section")} for x in out["sources"]]
    if step in ("generate", "verify", "finish") and "answer" in out:
        view["answer"] = out["answer"][:1500]
    return view


def _timed(fn):
    name = fn.__name__

    def wrapper(state: QueryState) -> dict:
        emit(state, SSEEvent.PROGRESS, {"step": name, "label": STEP_CN.get(name, name)})
        start, tr, short = time.time(), state.get("trace"), name.replace("node_", "")
        token = None
        if tr:
            tr.start(short, {"question": state.get("rewritten") or state["question"]})
            token = tracing._current.set((tr, tr._spans.get(short)))      # 节点里的 @observe 挂在这个节点的 span 下面
        before = len(state["degraded"])
        try:
            out = fn(state) or {}
        finally:
            if token is not None:
                tracing._current.reset(token)
        ms = int((time.time() - start) * 1000)
        if tr:
            tr.end(short, _trace_view(short, out), warning="；".join(state["degraded"][before:]) or None)
        state["latency"][name.replace("node_", "")] = ms
        metrics.STAGE.labels("kbase", name.replace("node_", "")).observe(ms / 1000)
        logger.info(f"[kbase] {name} {ms}ms")
        return out
    wrapper.__name__ = name
    return wrapper


def _messages():
    col = history_repository.col.database["kb_message"]
    col.create_index([("session_id", 1), ("ts", -1)])
    col.create_index([("username", 1), ("ts", -1)])
    return col


@_timed
def node_guard(state: QueryState) -> dict:
    word = guard.check_input(state["question"])
    if word:
        return {"status": "BLOCKED", "answer": f"这个问题涉及敏感内容（{word}），知识库助手不能回答。产品参数、图纸、工艺和故障处理方面的问题可以继续问我。"}
    return {}


@_timed
def node_understand(state: QueryState) -> dict:
    history, summary = memory.load(state["session_id"])       # 最近几轮原文 + 更早轮次的摘要
    q = state["question"]
    rewritten = q
    if intent.needs_rewrite(q, history):
        try:
            rewritten = R.run_with_timeout("追问改写", 20, intent.rewrite, q, history, summary, state["usage"])
        except (R.Unavailable, TimeoutError):
            degrade(state, "rewrite", "大模型暂时不可用，没有结合上文补全问题，按原话检索")
    plan = intent.classify(rewritten)
    if state["options"].get("force_route"):                  # 评测做消融时用：强制所有问题都走向量检索
        plan = {**plan, "route": state["options"]["force_route"]}
    emit(state, "meta", {"rewritten": rewritten if rewritten != q else None, "route": plan["route"], "models": plan["models"],
                         "params": [intent.label(k) for k in plan["param_keys"]]})
    out = {"history": history, "summary": summary, "rewritten": rewritten, "plan": plan}
    if not history and not state["options"]:                 # 新对话的第一句才查缓存：追问依赖上下文，评测带了特殊选项，都不缓存
        hit = R.cache_get("kbase", "answer", R.cache_key(rewritten.strip(), state["scope"].key(), R.counter("kb")))     # 按可见范围分缓存
        if hit:
            return {**out, "cache_hit": True, "status": hit["status"], "answer": hit["answer"], "sources": hit["sources"]}
    return out


def after_understand(state: QueryState) -> str:
    return "node_finish" if state.get("status") == "BLOCKED" or state.get("cache_hit") else "node_topic"


@_timed
def node_topic(state: QueryState) -> dict:
    """主题确认：锁定型号或系列；型号不完整又有多个候选、而且问的是参数 → 先反问，不猜"""
    lock = topic.resolve(state["rewritten"], state["plan"], topic.locked(state["session_id"]), bool(state.get("history")))
    if lock["clarify"]:
        return {"lock": lock, "status": "CLARIFY", "answer": topic.clarify_text(lock, state["rewritten"])}
    plan = topic.apply(state["plan"], lock)
    if lock["models"] or lock.get("series") or lock.get("external_models"):
        topic.remember(state["session_id"], {"models": lock["models"], "series": lock.get("series"),
                                             "external_models": lock.get("external_models", [])})
    emit(state, "meta", {"rewritten": state["rewritten"] if state["rewritten"] != state["question"] else None, "route": plan["route"],
                         "models": plan["models"], "params": [intent.label(k) for k in plan["param_keys"]],
                         "subject": {"models": lock["models"], "series": lock.get("series"), "source": lock["source"]}})
    return {"lock": lock, "plan": plan}


def after_topic(state: QueryState) -> str:
    if state.get("status") == "CLARIFY":
        return "node_finish"
    return "node_hyde" if state["plan"]["route"] == "DESC" else "node_sql"


@_timed
def node_sql(state: QueryState) -> dict:
    p, scope, notes = state["plan"], state["scope"], list(state.get("notes") or [])
    facts = []
    if p["models"]:
        facts += lookup.params_of(p["models"], p["param_keys"], scope)
        if p["want_bom"]:
            facts += lookup.bom_of(p["models"], scope, notes)
        facts += lookup.alerts_of(p["models"], scope)
    elif p["param_keys"] and (p["superlative"] or p["compare"]):
        facts += lookup.rank(p["param_keys"][0], p["series"], scope, p["superlative"], p["compare"])
    facts += kg.search(state["rewritten"], p["models"])
    return {"facts": facts, "notes": notes}


def after_sql(state: QueryState) -> str:
    # 纯参数问题且 SQL 已经查到：不再做向量检索和重排（省掉最慢的两步）
    if state["plan"]["route"] == "PARAM" and state.get("facts"):
        return "node_decide"
    return "node_hyde"


@_timed
def node_hyde(state: QueryState) -> dict:
    """说明类问题：一次调用做改写（口语转术语、指代消解）和 HyDE。失败或关掉时跳过 HyDE，用原问题检索"""
    opts = state["options"]
    if not settings.kbase_hyde or opts.get("hyde") is False or opts.get("mode") in ("dense", "bm25", "hybrid", "sparse", "no_hyde"):
        return {"hyde": ""}
    key = R.cache_key("hyde", prompts.tag(prompts.REWRITE_HYDE), state["rewritten"], topic.describe(state.get("lock") or {}))
    if not state.get("history"):                              # 首轮问题的改写和假设答案可以缓存（和上下文无关）
        hit = R.cache_get("kbase", "hyde", key)
        if hit:
            return {"rewritten": hit["rewritten"], "hyde": hit["hyde"]}
    try:
        rewritten, hyde = R.run_with_timeout("改写+HyDE", 25, intent.rewrite_hyde, state["rewritten"], state.get("history") or [], state.get("summary") or "",
                                             topic.describe(state.get("lock") or {}), state["usage"],
                                             not state["plan"]["models"] and bool(re.search(r"[A-Za-z]", state["question"])))
    except Exception as e:                                    # 模型不可用、超时、输出不是 JSON：都按降级处理
        logger.warning(f"[kbase] 改写+HyDE 失败：{type(e).__name__}: {str(e)[:120]}")
        degrade(state, "hyde", "没能生成假设答案，本次跳过 HyDE，只用原问题检索")
        return {"hyde": ""}
    if not state.get("history"):
        R.cache_set("kbase", "hyde", key, {"rewritten": rewritten, "hyde": hyde}, 86400)
    return {"rewritten": rewritten, "hyde": hyde}


@_timed
def node_retrieve(state: QueryState) -> dict:
    """三路召回（改写后问题的稠密向量 + 假设答案的稠密向量 + BM25）各 Top10 → RRF 融合"""
    emb, models, series = None, state["plan"]["models"] or None, state["plan"].get("series")
    doc_keys = retrieve.explicit_documents(state["question"], state["scope"])
    queries = {"rewritten": state["rewritten"], "hyde": state.get("hyde") or ""}
    try:
        vecs = retrieve.embed_many([q for q in queries.values() if q])
        embs = dict(zip([k for k, q in queries.items() if q], vecs))
        emb = embs["rewritten"]
        hits = retrieve.recall(queries, state["scope"], models, series, mode=state["options"].get("mode", "multi"), embeddings=embs,
                               doc_keys=doc_keys, literal_question=state['question'], limit=30 if not models else retrieve.RECALL)
    except (R.Unavailable, TimeoutError) as e:               # 向量库不可用：退到 MySQL 全文检索
        degrade(state, "fulltext", "向量库暂时不可用，本次改用关键词检索，结果可能不如平时准")
        logger.warning(f"[kbase] 向量检索失败：{e}")
        hits = retrieve.fulltext(state["rewritten"], state["scope"], models, doc_keys=doc_keys)
    facts = state.get("facts") or []
    if not facts and state["plan"]["route"] == "DESC" and not doc_keys:  # 指定文档的问题只使用该文档的证据
        facts = kg.search(state["rewritten"], state["plan"]["models"])
    return {"hits": hits, "facts": facts, "emb": emb}       # emb 留着，兜底时判断是不是「有资料但没权限」


@_timed
def node_rerank(state: QueryState) -> dict:
    hits, use = state.get("hits", []), state["options"].get("rerank", True)
    if any("关键词检索" in d for d in state["degraded"]):     # 全文检索的结果已经按相关度排过，量也少，不再重排
        use = False
    opts = state["options"]
    try:
        external = not state["plan"]["models"] and bool(re.search(r"[A-Za-z]", state["question"]))
        cliff = opts.get("cliff") or ({"min_k": 6} if external else None)
        top = retrieve.rerank(state["rewritten"], hits, top=opts.get("topk", 8 if external else retrieve.FINAL), use=use, cliff=cliff, semantic=external and opts.get("semantic_rerank", True), usage=state["usage"])
    except (R.Unavailable, TimeoutError):                     # 重排排不上队或超时：直接取融合后的前几条
        degrade(state, "no_rerank", "当前提问的人多，本次没有做精排，直接用了初步检索的前 8 条，结果可能不如平时准")
        use, top = False, retrieve.rerank(state["rewritten"], hits, top=8, use=False)      # 没有精排把关，多取几条提高命中的机会
    return {"docs": retrieve.expand_parents(top, state["scope"]), "options": {**opts, "rerank": use}}


@_timed
def node_decide(state: QueryState) -> dict:
    facts, docs = state.get("facts", []), state.get("docs", [])
    if state["options"].get("rerank", True):
        docs = [d for d in docs if d["score"] >= A.LOW_SCORE] or ([] if facts else docs[:1])
    weak = not facts and state["options"].get("rerank", True) and (not docs or max(d["score"] for d in docs) < A.LOW_SCORE)
    emb = state.get("emb")
    if (weak or (not facts and not docs)) and emb is not None and not state["scope"].admin \
            and retrieve.exists_without_acl(state["rewritten"], emb, state["scope"]):
        return {"status": "DENIED", "answer": A.denied(), "sources": []}
    if not facts and not docs:
        return {"status": "EMPTY", "answer": A.no_result(state["plan"]["models"]) + "".join(f"\n（{n}）" for n in state.get("notes") or []), "sources": []}
    sources = A.build_sources(facts, docs)
    emit(state, "sources", {"sources": [{k: v for k, v in s.items() if k != "context"} for s in sources]})
    if weak:
        return {"status": "LOW", "answer": A.low_relevance(sources), "sources": sources}
    return {"sources": sources, "docs": docs}


def after_decide(state: QueryState) -> str:
    return "node_finish" if state.get("status") in ("EMPTY", "LOW", "DENIED") or state["options"].get("skip_answer") else "node_generate"


def _history_text(state: QueryState) -> str:
    head = f"（更早对话的摘要：{state['summary']}）\n" if state.get("summary") else ""
    return head + "\n".join(f"{'用户' if h['role'] == 'user' else '助手'}：{h['text'][:200]}" for h in state.get("history", [])[-4:])


@_timed
def node_generate(state: QueryState) -> dict:
    def on_delta(text: str) -> None:
        if state["usage"].first_token_ms is None:
            state["usage"].first_token_ms = int((time.time() - state["t0"]) * 1000)
        emit(state, SSEEvent.DELTA, {"delta": text})

    subject_line = topic.describe(state.get("lock") or {})
    sources, history, budget = A.pack(state["rewritten"], state["sources"], _history_text(state), subject_line)   # 32K 预算装上下文
    if budget["dropped"] or budget["truncated"]:
        logger.info(f"[kbase] 上下文预算：装入 {budget['sources_in']} 条，截断 {budget['truncated']}，丢弃 {budget['dropped']}")
    emit(state, "sources", {"sources": [{k: v for k, v in x.items() if k != "context"} for x in sources]})

    def call():
        return A.generate(state["rewritten"], sources, history, state["usage"], on_delta if state.get("stream") else None, subject_line=subject_line)
    try:
        t, before = time.time(), state["usage"].as_dict()
        text = R.run_with_timeout("生成回答", 90, call)
        if state.get("stream"):
            emit(state, "replace", {"answer": text})
        after = state["usage"].as_dict()
        tracing.generation("生成回答", A.prompt(state["rewritten"], sources, history, subject_line=subject_line), text,
                           {"input": after["input"] - before["input"], "output": after["output"] - before["output"]}, time.time() - t,
                           {"prompt_version": prompts.tag(prompts.ANSWER), "budget": budget})
        return {"answer": text, "sources": sources, "pack": budget}
    except (R.Unavailable, TimeoutError) as e:               # 大模型不可用：不编答案，把检索到的原文直接给用户
        logger.warning(f"[kbase] 生成失败：{e}")
        degrade(state, "extractive", "大模型暂时不可用，下面是检索到的原文摘录，没有经过整理")
        text = A.extractive(sources)
        emit(state, "replace", {"answer": text})
        return {"answer": text, "status": "DEGRADED", "sources": sources, "pack": budget}


@_timed
def node_verify(state: QueryState) -> dict:
    """引用校验：不通过 → 更严格地重答一次 → 还不通过 → 降级成摘录原文"""
    if state.get("status") == "DEGRADED":                    # 已经是原文摘录了，不用再校验
        return {}
    ok, problem = A.verify(state["answer"], state["sources"], state["rewritten"])
    if A.no_answer(state["answer"]):
        # 模型看完资料后表示答不了：再看一眼是不是「有资料但没权限」，是的话明确告诉用户
        emb = state.get("emb") or retrieve.embed(state["rewritten"])
        if not state["scope"].admin and retrieve.exists_without_acl(state["rewritten"], emb, state["scope"]):
            emit(state, "replace", {"answer": A.denied()})
            return {"status": "DENIED", "answer": A.denied(), "sources": []}
        return {"status": "EMPTY"}
    if ok:
        return {"status": "OK"}
    logger.warning(f"[kbase] 引用校验没通过，重答：{problem}")
    try:
        again = R.run_with_timeout("重新生成", 60, A.generate, state["rewritten"], state["sources"], _history_text(state), state["usage"], None, problem,
                                   topic.describe(state.get("lock") or {}))
    except (R.Unavailable, TimeoutError):
        again = ""
    ok2, problem2 = A.verify(again, state["sources"], state["rewritten"]) if again else (False, "重新生成失败")
    if ok2:
        emit(state, "replace", {"answer": again})
        return {"answer": again, "status": "OK", "verify_note": f"第一次回答没有通过引用校验（{problem}），已重新生成"}
    text = A.extractive(state["sources"])
    emit(state, "replace", {"answer": text})
    return {"answer": text, "status": "DEGRADED", "verify_note": f"两次回答都没有通过引用校验（{problem2}），已降级为摘录原文"}


@_timed
def node_finish(state: QueryState) -> dict:
    text, masked = guard.clean_output(state.get("answer", ""))
    if masked:
        emit(state, "replace", {"answer": text})
    status = state.get("status") or "OK"
    if state.get("notes") and status not in ("EMPTY", "BLOCKED"):
        text += "".join(f"\n\n（{n}）" for n in state["notes"])
    if (status in ("BLOCKED", "EMPTY", "LOW", "DENIED", "CLARIFY") or state.get("cache_hit")) and state.get("stream"):
        emit(state, SSEEvent.DELTA, {"delta": text})
    answer_id = uuid.uuid4().hex
    if not state["options"].get("no_log"):
        sources = state.get("sources", [])
        store.execute(
            "INSERT INTO kb_answer_log (answer_id, session_id, trace_id, username, role, question, rewritten, route, status, chunk_ids, "
            "doc_versions, latency, answer, prompt_version, scope_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (answer_id, state["session_id"], state.get("trace_id"), state["username"], state["role"], state["question"], state.get("rewritten"),
             (state.get("plan") or {}).get("route"), status, store.dumps([s["chunk_id"] for s in sources if s.get("chunk_id")]),
             store.dumps(sorted({f"{s['doc_key']}@{s['version']}" for s in sources if s.get("doc_key")})),
             store.dumps({**state["latency"], "total": int((time.time() - state["t0"]) * 1000), "first_token": state["usage"].first_token_ms,
                          "tokens": state["usage"].total, "pack": state.get("pack")}), text, _prompt_tag(state), state["scope"].key()))
        now = datetime.now().timestamp()
        _messages().insert_many([
            {"session_id": state["session_id"], "username": state["username"], "role": "user", "text": state["question"], "ts": now},
            {"session_id": state["session_id"], "username": state["username"], "role": "assistant", "text": text, "ts": now + 0.001,
             "payload": {"answer_id": answer_id, "status": status, "route": (state.get("plan") or {}).get("route"),
                         "sources": [{k: v for k, v in s.items() if k != "context"} for s in sources], "verify_note": state.get("verify_note"),
                         "degraded": state["degraded"], "cache_hit": bool(state.get("cache_hit"))}}])
        if status == "OK" and not state.get("cache_hit") and not state.get("history") and not state["degraded"] and not state["options"]:
            R.cache_set("kbase", "answer", R.cache_key(state["rewritten"].strip(), state["scope"].key(), R.counter("kb")),
                        {"status": status, "answer": text, "sources": sources}, ANSWER_TTL)
        querylog.write({**state, "prompt_version": _prompt_tag(state)}, answer_id, text, status)    # MongoDB 查询日志（Bad Case 池从这里筛）
        memory.compress_later(state["session_id"])
    return {"answer": text, "masked": masked, "status": status, "answer_id": answer_id}


def _prompt_tag(state: QueryState) -> str:
    used = [prompts.ANSWER] if state.get("answer") and state.get("status") in ("OK", "DEGRADED", None) else []
    if state.get("hyde"):
        used.append(prompts.REWRITE_HYDE)
    if state.get("rewritten") and state["rewritten"] != state["question"] and not state.get("hyde"):
        used.append(prompts.REWRITE)
    return prompts.tag(*used)


def _build():
    g = StateGraph(QueryState)
    for fn in (node_guard, node_understand, node_topic, node_sql, node_hyde, node_retrieve, node_rerank, node_decide, node_generate, node_verify, node_finish):
        g.add_node(fn.__name__, fn)
    g.set_entry_point("node_guard")
    g.add_conditional_edges("node_guard", lambda s: "node_finish" if s.get("status") == "BLOCKED" else "node_understand", ["node_finish", "node_understand"])
    g.add_conditional_edges("node_understand", after_understand, ["node_topic", "node_finish"])
    g.add_conditional_edges("node_topic", after_topic, ["node_sql", "node_hyde", "node_finish"])
    g.add_conditional_edges("node_sql", after_sql, ["node_hyde", "node_decide"])
    g.add_edge("node_hyde", "node_retrieve")
    g.add_edge("node_retrieve", "node_rerank")
    g.add_edge("node_rerank", "node_decide")
    g.add_conditional_edges("node_decide", after_decide, ["node_generate", "node_finish"])
    g.add_edge("node_generate", "node_verify")
    g.add_edge("node_verify", "node_finish")
    g.add_edge("node_finish", END)
    return g.compile()


query_app = _build()


def ask(question: str, username: str, role: str, session_id: str | None = None, *, stream: bool = False, request_id: str | None = None,
        trace_id: str = "", options: dict | None = None, scope: authz.Scope | None = None) -> dict:
    scope = scope or authz.Scope.for_role(role, username)
    state: QueryState = {"request_id": request_id or uuid.uuid4().hex, "session_id": session_id or uuid.uuid4().hex, "trace_id": trace_id,
                         "username": username, "role": role, "scope": scope, "question": question.strip(), "stream": stream, "options": options or {},
                         "usage": llm.Usage(), "t0": time.time(), "latency": {}, "facts": [], "hits": [], "docs": [], "sources": [],
                         "degraded": list(scope.notes), "notes": []}
    if not (options or {}).get("no_log"):                    # 评测不记链路
        state["trace_id"] = state["trace_id"] or state["request_id"]
        state["trace"] = tracing.Trace("kbase", "知识库问答", state["trace_id"], username, state["session_id"], state["question"],
                                       {"role": role, "scope": scope.key(), "source": scope.source, "clearance": scope.clearance})
    token = tracing.bind(state["trace"]) if state.get("trace") else None
    try:
        final = query_app.invoke(state, {"recursion_limit": 30})
    finally:
        if token is not None:
            tracing.unbind(token)
    if state.get("trace"):
        plan_ = final.get("plan") or {}
        srcs = final.get("sources", [])
        state["trace"].finish(final.get("answer", ""), [plan_.get("route"), final.get("status"), *(["降级"] if state["degraded"] else []), *(["缓存"] if final.get("cache_hit") else [])],
                              {"degraded": state["degraded"], "latency_ms": state["latency"], "models": plan_.get("models"), "answer_id": final.get("answer_id"),
                               "doc_versions": sorted({f"{x['doc_key']}@{x['version']}" for x in srcs if x.get("doc_key")}),
                               "prompt_version": _prompt_tag(final), "tokens": state["usage"].as_dict(), "budget": final.get("pack"),
                               "subject": final.get("lock")})
    plan = final.get("plan") or {}
    seconds = time.time() - state["t0"]
    metrics.REQUESTS.labels("kbase", plan.get("route") or "-", final.get("status") or "OK").inc()
    metrics.LATENCY.labels("kbase", plan.get("route") or "-").observe(seconds)
    if state["usage"].first_token_ms:
        metrics.FIRST_TOKEN.labels("kbase").observe(state["usage"].first_token_ms / 1000)
    metrics.TOKENS.labels("kbase", "input").inc(state["usage"].input)
    metrics.TOKENS.labels("kbase", "output").inc(state["usage"].output)
    return {
        "degraded": state["degraded"], "cache_hit": bool(final.get("cache_hit")), "trace_url": tracing.url(state["trace_id"]) if state.get("trace") and state["trace"].on else None,
        "session_id": state["session_id"], "answer_id": final.get("answer_id"), "status": final.get("status"), "answer": final.get("answer", ""),
        "rewritten": final.get("rewritten") if final.get("rewritten") != state["question"] else None, "route": plan.get("route"),
        "models": plan.get("models", []), "params": [intent.label(k) for k in plan.get("param_keys", [])],
        "sources": [{k: v for k, v in s.items() if k != "context"} for s in final.get("sources", [])],
        "contexts": [s["context"] for s in final.get("sources", [])] if (options or {}).get("with_contexts") else None,
        "verify_note": final.get("verify_note"), "masked": final.get("masked", 0), "latency": state["latency"], "hyde": final.get("hyde") or None,
        "subject": {k: (final.get("lock") or {}).get(k) for k in ("models", "series", "source", "candidates")} if final.get("lock") else None,
        "prompt_version": _prompt_tag(final), "budget": final.get("pack"),
        "elapsed_ms": int((time.time() - state["t0"]) * 1000), "first_token_ms": state["usage"].first_token_ms, "usage": state["usage"].as_dict(),
    }


def sessions(username: str, limit: int = 30) -> list[dict]:
    pipeline = [{"$match": {"username": username, "role": "user"}}, {"$sort": {"ts": -1}},
                {"$group": {"_id": "$session_id", "last_ts": {"$first": "$ts"}, "title": {"$last": "$text"}}}, {"$sort": {"last_ts": -1}}, {"$limit": limit}]
    return [{"session_id": r["_id"], "last_ts": r["last_ts"], "title": r["title"][:40]} for r in _messages().aggregate(pipeline)]


def session_owner(session_id: str) -> str | None:
    doc = _messages().find_one({"session_id": session_id}, {"username": 1})
    return doc.get("username") if doc else None


def history(session_id: str) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "_id"} for r in _messages().find({"session_id": session_id}).sort("ts", 1).limit(200)]


def clear(session_id: str) -> int:
    memory.forget(session_id)
    return _messages().delete_many({"session_id": session_id}).deleted_count


def feedback(answer_id: str, username: str, rating: int, reason: str | None, correct_answer: str | None, comment: str | None) -> None:
    row = store.one("SELECT username FROM kb_answer_log WHERE answer_id = %s", (answer_id,))
    if not row or row["username"] != username:
        raise PermissionError("只能评价自己的提问")
    metrics.FEEDBACK.labels("kbase", "up" if rating > 0 else "down").inc()
    log = store.one("SELECT trace_id FROM kb_answer_log WHERE answer_id = %s", (answer_id,))
    tracing.score((log or {}).get("trace_id") or "", "用户反馈", float(rating), reason or comment)
    store.execute("INSERT INTO kb_feedback (answer_id, username, rating, reason, correct_answer, comment) VALUES (%s,%s,%s,%s,%s,%s) "
                  "ON DUPLICATE KEY UPDATE rating = VALUES(rating), reason = VALUES(reason), correct_answer = VALUES(correct_answer), comment = VALUES(comment)",
                  (answer_id, username, rating, (reason or "")[:30], correct_answer, (comment or "")[:500]))
    querylog.set_feedback(answer_id, rating, reason, correct_answer, comment)          # 反馈回写到 MongoDB 查询日志


def bad_cases(limit: int = 100) -> list[dict]:
    """Bad Case 池：从 MongoDB 查询日志筛点踩的记录，带当时的改写、召回、精排、引用，能回溯到原始 chunk"""
    out = []
    for r in querylog.bad_cases(limit):
        fb = r.get("feedback") or {}
        out.append({"id": r["answer_id"], "created_at": fb.get("at"), "reason": fb.get("reason"), "correct_answer": fb.get("correct_answer"),
                    "comment": fb.get("comment"), "status": "NEW", "answer_id": r["answer_id"], "trace_id": r.get("trace_id"),
                    "trace_url": tracing.url(r.get("trace_id") or ""), "username": r.get("username"), "question": r.get("question"),
                    "rewritten": r.get("rewritten"), "hyde": r.get("hyde"), "route": r.get("route"), "answer_status": r.get("status"),
                    "chunk_ids": [c["chunk_id"] for c in r.get("citations") or [] if c.get("chunk_id")],
                    "doc_versions": sorted({f"{c['doc_key']}@{c['version']}" for c in r.get("citations") or [] if c.get("doc_key")}),
                    "recall": r.get("recall"), "reranked": r.get("reranked"), "answer": r.get("answer")})
    return json.loads(json.dumps(out, ensure_ascii=False, default=str))
