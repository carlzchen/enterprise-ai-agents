"""
知识库评测。用法（在 assistant 目录下）：
  python -m app.kbase.eval.runner answers    把评测集里的每道题跑一遍完整问答流程，保存回答、引用的上下文和耗时
  python -m app.kbase.eval.runner ragas      用 RAGAS 算四个指标：忠实度、答案相关性、上下文精确率、上下文召回率
  python -m app.kbase.eval.runner ablation   检索消融：只用稠密 / 只用稀疏 / 只用 BM25 / 稠密+BM25 融合 / 再加重排，比 Hit@K 和 MRR
  python -m app.kbase.eval.tune              单变量调参（召回路数、TopK、阈值、重排、chunk size），见 tune.py
  python -m app.kbase.eval.runner param      参数题消融：结构化查询 和 纯向量检索 各答一遍，比准确率
  python -m app.kbase.eval.runner report     汇总成 data/kbase_eval/report.json 和 report.md
  python -m app.kbase.eval.runner all        依次全部执行
每一步的结果都落盘，可以单独重跑某一步。
"""
import json
import re
import statistics
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app.kbase.eval import dataset
from app.shared.runtime.logger import logger

DIR = dataset.PATH.parent
ANSWERED = ("OK", "DEGRADED")
REFUSED = ("EMPTY", "LOW", "DENIED", "BLOCKED")


def use_server_models(base: str = "http://127.0.0.1:8094") -> None:
    """让评测借用知识库服务里已经加载的向量模型和重排模型。本机内存只有 16G，服务和评测各加载一份会把内存撑爆"""
    import time as _t

    import httpx
    import jwt

    from app.infra.config.settings import settings
    from app.infra.llm.providers import llm_providers
    from app.kbase.query import retrieve
    n = len(settings.jwt_secret.encode())
    token = jwt.encode({"sub": "eval", "role": "ADMIN", "exp": int(_t.time() + 86400)}, settings.jwt_secret, algorithm="HS512" if n >= 64 else "HS384" if n >= 48 else "HS256")
    http = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=300, trust_env=False)

    def embeddings(texts):
        out = {"dense": [], "sparse": []}
        for i in range(0, len(texts), 32):
            r = http.post("/internal/embed", json={"texts": list(texts[i:i + 32]), "sparse": True})
            r.raise_for_status()
            out["dense"] += r.json()["dense"]
            out["sparse"] += [{int(k) if k.isdigit() else k: v for k, v in s.items()} for s in r.json()["sparse"]]
        return out

    def scores(query, texts):
        if not texts:
            return []
        r = http.post("/internal/rerank", json={"query": query, "texts": list(texts)})
        r.raise_for_status()
        return r.json()["scores"]
    llm_providers.generate_embeddings = embeddings
    llm_providers.rerank_scores = scores
    logger.info(f"[kbase-eval] 向量化和重排改用知识库服务 {base} 里的模型")


def _save(name: str, data) -> None:
    (DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _load(name: str):
    return json.loads((DIR / name).read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return re.sub(r"[\s,，（）()~～\-—–+＋±]", "", (text or "").upper().replace("Φ", "").replace("∅", ""))


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 2)


# ---------------------------------------------------------------- 第一步：跑问答

def _ask(case: dict, options: dict | None = None) -> dict:
    from app.kbase.query import graph
    try:
        r = graph.ask(case["question"], "eval", case["role"], stream=True, options={"no_log": True, "with_contexts": True, **(options or {})})
        return {"id": case["id"], "status": r["status"], "route": r["route"], "answer": r["answer"], "contexts": r["contexts"] or [],
                "sources": [{k: s.get(k) for k in ("index", "type", "doc_key", "chunk_id", "title", "section", "score")} for s in r["sources"]],
                "elapsed": round(r["elapsed_ms"] / 1000, 2), "first_token": round(r["first_token_ms"] / 1000, 2) if r["first_token_ms"] else None,
                "latency": r["latency"], "tokens": r["usage"]["total"], "verify_note": r["verify_note"]}
    except Exception as e:
        logger.exception(f"[kbase-eval] {case['id']} 出错")
        return {"id": case["id"], "status": "ERROR", "route": None, "answer": f"ERROR: {e}", "contexts": [], "sources": [], "elapsed": 0, "first_token": None,
                "latency": {}, "tokens": 0, "verify_note": None}


def answers(limit: int | None = None, workers: int = 4) -> list[dict]:
    cases = dataset.load()[:limit]
    t = time.time()
    with ThreadPoolExecutor(workers) as pool:
        out = list(pool.map(_ask, cases))
    _save("answers.json", out)
    logger.info(f"[kbase-eval] 问答完成 {len(out)} 题，用时 {time.time() - t:.0f}s")
    return out


# ---------------------------------------------------------------- 第二步：RAGAS

def _ragas_imports():
    import langsmith.run_helpers  # noqa: F401  评测时多线程并发导入它会出现半初始化的模块，先在主线程导入一次
    for name, cls in (("langchain_community.chat_models.vertexai", "ChatVertexAI"), ("langchain_community.llms.vertexai", "VertexAI")):
        try:                                                 # 这个版本的 ragas 还在引用 langchain-community 里已经删掉的模块，补一个空壳让它能导入
            __import__(name)
        except Exception:
            m = types.ModuleType(name)
            setattr(m, cls, type(cls, (), {}))
            sys.modules[name] = m


def ragas(limit: int | None = None) -> dict:
    _ragas_imports()
    from langchain_core.embeddings import Embeddings
    from langchain_openai import ChatOpenAI
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy
    from ragas.run_config import RunConfig

    from app.infra.config.settings import settings
    from app.infra.llm.providers import llm_providers
    from app.kbase.query import retrieve

    class Bge(Embeddings):
        def embed_documents(self, texts):
            with retrieve._model_lock:
                return [list(map(float, v)) for v in llm_providers.generate_embeddings(list(texts))["dense"]]

        def embed_query(self, text):
            return self.embed_documents([text])[0]

    cases = {c["id"]: c for c in dataset.load()}
    rows = [a for a in _load("answers.json") if cases[a["id"]]["kind"] != "REFUSE"][:limit]
    data = EvaluationDataset.from_list([{"user_input": cases[a["id"]]["question"], "response": a["answer"], "retrieved_contexts": [c[:2000] for c in a["contexts"]] or ["（没有检索到资料）"],
                                         "reference": cases[a["id"]]["ground_truth"]} for a in rows])
    judge = LangchainLLMWrapper(ChatOpenAI(model=settings.llm_model, base_url=settings.llm_base_url, api_key=settings.llm_api_key, temperature=0, timeout=120, max_retries=3))
    metrics = [Faithfulness(), ResponseRelevancy(strictness=1), LLMContextPrecisionWithReference(), LLMContextRecall()]
    t = time.time()
    result = evaluate(data, metrics=metrics, llm=judge, embeddings=LangchainEmbeddingsWrapper(Bge()), run_config=RunConfig(max_workers=24, timeout=300, max_retries=4),
                      raise_exceptions=False, show_progress=False)
    df = result.to_pandas()
    cols = {"faithfulness": "faithfulness", "answer_relevancy": "answer_relevancy", "llm_context_precision_with_reference": "context_precision", "context_recall": "context_recall"}
    per = []
    for a, (_, r) in zip(rows, df.iterrows()):
        per.append({"id": a["id"], "kind": cases[a["id"]]["kind"], **{v: (None if r[k] != r[k] else round(float(r[k]), 4)) for k, v in cols.items() if k in df.columns}})
    def mean(items, key):
        vals = [x[key] for x in items if x.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None
    out = {"judge_model": settings.llm_model, "n": len(per), "seconds": int(time.time() - t),
           "overall": {v: mean(per, v) for v in cols.values()},
           "by_kind": {k: {"n": sum(1 for x in per if x["kind"] == k), **{v: mean([x for x in per if x["kind"] == k], v) for v in cols.values()}} for k in ("PARAM", "DESC", "MIXED")},
           "failed_scores": {v: sum(1 for x in per if x.get(v) is None) for v in cols.values()}, "per_case": per}
    _save("ragas.json", out)
    logger.info(f"[kbase-eval] RAGAS 完成 {out['n']} 题：{out['overall']}，用时 {out['seconds']}s")
    return out


# ---------------------------------------------------------------- 第三步：检索消融

def ablation(limit: int | None = None) -> dict:
    from app.kbase.query import intent, retrieve
    cases = [c for c in dataset.load() if c["kind"] in ("DESC", "MIXED")][:limit]
    configs = {"只用稠密向量": ("dense", False), "只用稀疏向量": ("sparse", False), "只用 BM25": ("bm25", False), "稠密 + BM25 + RRF": ("no_hyde", False),
               "稠密 + BM25 + RRF + 重排": ("no_hyde", True)}           # 带 HyDE 的三路对比见 tune.py（要先生成假设答案）
    ranks: dict[str, list] = {k: [] for k in configs}
    recall50, t = [], time.time()
    for c in cases:
        embs = {"rewritten": retrieve.embed(c["question"])}
        models = intent.find_models(c["question"]) or None
        for name, (mode, use_rerank) in configs.items():
            hits = retrieve.recall({"rewritten": c["question"]}, "ADMIN", models, mode=mode, embeddings=embs)
            if name == "稠密 + BM25 + RRF":
                recall50.append(any(h["parent_id"] == c["gold_parent"] for h in hits))
            if use_rerank:
                scores = retrieve.rerank_scores(c["question"], hits)
                hits = [h for h, _ in sorted(zip(hits, scores), key=lambda x: -x[1])]
            seen, order = set(), []
            for h in hits:                                   # 按章节去重后的名次（最终给大模型的是整章）
                if h["parent_id"] not in seen:
                    seen.add(h["parent_id"])
                    order.append(h["parent_id"])
            ranks[name].append(order.index(c["gold_parent"]) + 1 if c["gold_parent"] in order else None)
    def summary(rs):
        n = len(rs)
        return {"hit@1": round(sum(1 for r in rs if r == 1) / n, 4), "hit@3": round(sum(1 for r in rs if r and r <= 3) / n, 4),
                "hit@5": round(sum(1 for r in rs if r and r <= 5) / n, 4), "mrr": round(sum(1 / r for r in rs if r) / n, 4)}
    out = {"n": len(cases), "seconds": int(time.time() - t), "recall@50": round(sum(recall50) / len(recall50), 4),
           "configs": {k: summary(v) for k, v in ranks.items()}, "ranks": {k: v for k, v in ranks.items()}, "ids": [c["id"] for c in cases]}
    _save("ablation.json", out)
    logger.info(f"[kbase-eval] 检索消融完成：{out['configs']}")
    return out


# ---------------------------------------------------------------- 第四步：参数题消融

def _param_ok(case: dict, answer: str) -> bool:
    return _norm(case["expected"]) in _norm(answer)


def param(sample: int = 60, workers: int = 4) -> dict:
    cases = [c for c in dataset.load() if c["kind"] == "PARAM"][:sample]
    with ThreadPoolExecutor(workers) as pool:
        vec = list(pool.map(lambda c: _ask(c, {"force_route": "DESC"}), cases))
    base = {a["id"]: a for a in _load("answers.json")}
    rows = [{"id": c["id"], "question": c["question"], "expected": c["expected"], "sql_ok": _param_ok(c, base[c["id"]]["answer"]), "vector_ok": _param_ok(c, v["answer"]),
             "sql_elapsed": base[c["id"]]["elapsed"], "vector_elapsed": v["elapsed"], "vector_status": v["status"], "vector_answer": v["answer"][:200]} for c, v in zip(cases, vec)]
    out = {"n": len(rows), "结构化查询": {"accuracy": round(sum(r["sql_ok"] for r in rows) / len(rows), 4), "p50_s": _pct([r["sql_elapsed"] for r in rows], 0.5)},
           "纯向量检索": {"accuracy": round(sum(r["vector_ok"] for r in rows) / len(rows), 4), "p50_s": _pct([r["vector_elapsed"] for r in rows], 0.5)}, "rows": rows}
    _save("param_ablation.json", out)
    logger.info(f"[kbase-eval] 参数题消融完成：结构化 {out['结构化查询']}，纯向量 {out['纯向量检索']}")
    return out


# ---------------------------------------------------------------- 第五步：汇总

_CITE = re.compile(r"\[(\d+)\]")


def report() -> dict:
    cases = {c["id"]: c for c in dataset.load()}
    ans = _load("answers.json")
    def of(kind):
        return [a for a in ans if cases[a["id"]]["kind"] == kind]
    answerable = [a for a in ans if cases[a["id"]]["kind"] != "REFUSE"]
    answered = [a for a in answerable if a["status"] in ANSWERED]
    p, d, m, r = of("PARAM"), of("DESC"), of("MIXED"), of("REFUSE")
    hit = lambda a: cases[a["id"]].get("gold_parent") and any(s.get("doc_key") in cases[a["id"]]["gold_docs"] for s in a["sources"])
    cited = [a for a in answered if _CITE.search(a["answer"])]
    valid = [a for a in cited if {int(n) for n in _CITE.findall(a["answer"])} <= {s["index"] for s in a["sources"]}]
    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset": {"total": len(ans), **{k: len(of(k)) for k in ("PARAM", "DESC", "MIXED", "REFUSE")}},
        "status": {s: sum(1 for a in ans if a["status"] == s) for s in sorted({a["status"] for a in ans})},
        "param_accuracy": round(sum(_param_ok(cases[a["id"]], a["answer"]) for a in p) / len(p), 4),
        "mixed_param_accuracy": round(sum(_param_ok(cases[a["id"]], a["answer"]) for a in m) / len(m), 4) if m else None,
        "desc_answered_rate": round(sum(a["status"] in ANSWERED for a in d) / len(d), 4),
        "desc_gold_doc_in_sources": round(sum(bool(hit(a)) for a in [*d, *m]) / len([*d, *m]), 4),
        "refuse_accuracy": round(sum(a["status"] in REFUSED for a in r) / len(r), 4) if r else None,
        "refuse_by_sub": {s: {"n": sum(1 for a in r if cases[a["id"]]["sub"] == s), "refused": sum(1 for a in r if cases[a["id"]]["sub"] == s and a["status"] in REFUSED),
                              "denied": sum(1 for a in r if cases[a["id"]]["sub"] == s and a["status"] == "DENIED")} for s in ("知识库里没有", "没有权限")},
        "wrongly_refused": sum(a["status"] in REFUSED for a in answerable),
        "citation": {"answered": len(answered), "with_citation": len(cited), "all_citations_valid": len(valid), "degraded_to_extract": sum(a["status"] == "DEGRADED" for a in ans),
                     "regenerated_once": sum(1 for a in ans if a.get("verify_note") and a["status"] == "OK")},
        "latency_s": {k: {"n": len(v), "p50": _pct([a["elapsed"] for a in v], 0.5), "p95": _pct([a["elapsed"] for a in v], 0.95),
                          "first_token_p50": _pct([a["first_token"] for a in v if a["first_token"]], 0.5)} for k, v in (("PARAM", p), ("DESC", d), ("MIXED", m), ("全部", ans))},
        "stage_ms_p50": {k: int(statistics.median([a["latency"][k] for a in ans if k in a["latency"]])) for k in ("node_retrieve", "node_rerank", "node_generate", "node_sql")
                         if any(k in a["latency"] for a in ans)},
        "tokens_avg": int(sum(a["tokens"] for a in answered) / max(1, len(answered))),
    }
    for name in ("ragas", "ablation", "param_ablation"):
        if (DIR / f"{name}.json").exists():
            x = _load(f"{name}.json")
            out[name] = {k: v for k, v in x.items() if k not in ("per_case", "ranks", "ids", "rows")}
    _save("report.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    import os
    if os.environ.get("KBASE_EVAL_REMOTE") == "1":
        use_server_models()
    n = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if step in ("answers", "all"):
        answers(n)
    if step in ("param", "all"):
        param(n or 60)
    if step in ("ablation", "all"):
        ablation(n)
    if step in ("ragas", "all"):
        ragas(n)
    if step in ("report", "all"):
        report()
