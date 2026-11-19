# RAG knowledge base: code reading guide

A reading order for `assistant/app/kbase`, written for someone who wants to understand the project well enough to explain every CV bullet from the code.

## Short answer: do not start with `.env`

`.env` is a list of about 90 settings (addresses, passwords, API keys). It tells you which external systems exist, not how the project works, and it contains real secrets. Use it as a lookup table when a file mentions `settings.xxx`.

Start here instead, in this order:

1. [RAG-01-企业知识库实现说明.md](RAG-01-企业知识库实现说明.md): your own overview (10 minutes).
2. [server.py](../assistant/app/kbase/server.py) lines 1–78: the list of endpoints and what starts at boot.
3. [ingest/graph.py](../assistant/app/kbase/ingest/graph.py) lines 1–16 and [query/graph.py](../assistant/app/kbase/query/graph.py) lines 1–13: the two flow diagrams. Everything else in the project hangs off these two pictures.

## The whole project in one picture

Updated 2026-10-15 for the v3 refactor. What changed and why: [RAG-06-v3重构说明.md](RAG-06-v3重构说明.md). Every CV bullet mapped to a file and line: [RAG-07-模块与代码对照.md](RAG-07-模块与代码对照.md).

```
                    ┌──────────────── INGEST (write side) ────────────────┐
 file ──► file SHA-256 ──► router ──► parse (doc | table | cad) ──► clean / mask / unify terms ──► split by section
                                                                                                    │
      chunk SHA-256 diff (reuse | new | vanished) ◄────────────────────────────────────────────────┘
          │
          ▼
      subject check (new chunks only) ► quality gate ► embed (new only) ► stage as PENDING ► verify
                                                                                              │
      archive (MongoDB) ◄── switch in one transaction + event ─► RabbitMQ ─► SMTP ◄──────────┘

                    ┌──────────────── QUERY (read side) ──────────────────┐
 SSO login ► Scope (roles + department path + clearance)
 question ──► guard ──► understand ──► topic lock ─┬─ PARAM ──► SQL ───────────────────────────────┐
                                                   ├─ DESC  ──► rewrite + HyDE ► 3-way recall (Top10 each) ► RRF ► rerank ► cliff ≤3
                                                   └─ MIXED ──► SQL ► (same as DESC) ────────────────┤
                                                                                                    │
                       finish ◄── verify citations ◄── generate ◄── 32K budget pack ◄── decide ◄────┘
```

Two rules explain most design decisions:

- **MySQL is the source of truth; Milvus is a derived index.** A query only sees chunks whose state is VALID in MySQL (`kb_chunk.state`), and the document version that `kb_document.active_version` points to.
- **One Scope decides visibility everywhere.** `app/kbase/authz.py` produces the Milvus filter, the SQL condition and the per-image check from the same object.
- **Numbers come from SQL, explanations come from vectors.** Vector search cannot tell 750 W from 400 W.

## Before you read: run it once

Reading goes faster after you have seen one upload and one question.

```bash
cd assistant && ./scripts/kbase.sh start
```

Then open http://localhost:5180, go to 企业知识库, and:

1. Ask a parameter question that names a model, for example `KL-SV-80-750W 的额定转矩是多少`. Watch the progress steps appear (输入检查 → 理解问题 → 查询结构化参数 → ...). Those labels are `STEP_CN` in `query/graph.py`.
2. Ask a "why / how" question about a fault or a process. The steps now include 检索文档 and 重排序.
3. Upload one file from `assistant/data/kbase_samples/` with a new version number and watch the job stages.

Keep the log open while you do this; every node prints its name and time:

```bash
tail -f assistant/output/run/kbase.log
```

## Reading stages

Line counts are from the current code. Times are rough estimates for a careful first read.

### Stage 0. Orientation (30 min)

| Order | File | Read | What to get out of it |
|---|---|---|---|
| 1 | [RAG-01-企业知识库实现说明.md](RAG-01-企业知识库实现说明.md) | all | Folder layout, the two flows, what is real and what is mocked (section 6) |
| 2 | [scripts/kbase.sh](../assistant/scripts/kbase.sh) | all, 37 lines | Which processes and ports exist: 8094 knowledge base, 8096 mock Kingdee + Feishu, 8093 Copilot, 8095 Workflow |
| 3 | [server.py](../assistant/app/kbase/server.py) | lines 1–78 | The endpoint list in the docstring, and `startup()`: tables, retry worker, mail sender, queue workers, scheduler, model warm-up |
| 4 | `assistant/.env.example` + [settings.py](../assistant/app/infra/config/settings.py) | skim | Which external systems exist. Read `.env.example`, not `.env` |

Check yourself: name the six storage/infra components and say what each stores. (MySQL, Milvus, MongoDB, MinIO, Redis, Neo4j.)

### Stage 1. Ingest pipeline (2 h)

This is the largest part of the CV (bullets 1–7). Read the orchestrator first, then each step it calls.

| Order | File | Lines | Focus |
|---|---|---|---|
| 1 | [ingest/graph.py](../assistant/app/kbase/ingest/graph.py) | 388 | The docstring diagram; `IngestState`; `_safe` (how errors become state instead of exceptions); `_next` and `_build` (routing); `node_quality_gate`; `node_fail` |
| 2 | [ingest/clean.py](../assistant/app/kbase/ingest/clean.py) | 106 | `ROUTE` table; `check_file` (encrypted, corrupt, unsupported); `normalize_model`; `clean_text` |
| 3 | [ingest/parsers.py](../assistant/app/kbase/ingest/parsers.py) | 378 | Three sections, one per route. `parse_doc` chooses pypdf / MinerU / Docling / python-pptx. `parse_table` reads Excel. `read_dxf` + `cross_check` handle drawings |
| 4 | [ingest/params.py](../assistant/app/kbase/ingest/params.py) | 101 | `ALIASES`, `split_header`, `parse_value`: how a cell becomes 型号—参数名—数值—单位 |
| 5 | [ingest/split.py](../assistant/app/kbase/ingest/split.py) | 88 | Parent/child splitting; the constants at the top; why each chunk is prefixed with 【文档标题】章节 |
| 6 | [ingest/versioning.py](../assistant/app/kbase/ingest/versioning.py) | 255 | `chunk_id` (deterministic key), `decide`, `stage`, `verify`, `activate`, `rollback`, `cleanup`, `reconcile_all` |
| 7 | [vectors.py](../assistant/app/kbase/vectors.py) | 117 | The Milvus schema in `ensure()` (these are the metadata fields), `upsert`, `ids_of`, `acl_expr` |
| 8 | [ingest/notify.py](../assistant/app/kbase/ingest/notify.py) | 73 | Outbox pattern: `enqueue` writes a row, `send_pending` sends later |

Second pass, lower priority: [ingest/mineru.py](../assistant/app/kbase/ingest/mineru.py) (MinerU cloud upload/poll/download), [ingest/images.py](../assistant/app/kbase/ingest/images.py) (image upload and vision summaries), [store.py](../assistant/app/kbase/store.py) (table definitions; read the `CREATE TABLE` statements for `kb_document`, `kb_doc_version`, `kb_chunk`, `kb_param`).

Check yourself:

- A file fails at `node_embed` because the network dropped. Which lines decide whether it is retried, and what is not redone on retry?
- A new version fails at `node_verify`. What does a user asking a question see, and why?
- Why does `stage` delete and re-insert rows for the same `doc_key + version` without producing duplicates in Milvus?

### Stage 2. Query pipeline (2 h)

Bullets 8, 9, 11, 12. Again: orchestrator first.

| Order | File | Lines | Focus |
|---|---|---|---|
| 1 | [query/graph.py](../assistant/app/kbase/query/graph.py) | 428 | Docstring diagram; `QueryState`; then the nodes top to bottom: `node_guard`, `node_understand`, `node_sql`, `node_retrieve`, `node_rerank`, `node_decide`, `node_generate`, `node_verify`, `node_finish`; then `_build` and `ask` |
| 2 | [query/intent.py](../assistant/app/kbase/query/intent.py) | 110 | `classify` (rules, no LLM) and `needs_rewrite` / `rewrite` (LLM only for follow-ups) |
| 3 | [query/lookup.py](../assistant/app/kbase/query/lookup.py) | 93 | `_ACTIVE` (version + permission filter in SQL), `params_of`, `rank`, `bom_of` |
| 4 | [query/retrieve.py](../assistant/app/kbase/query/retrieve.py) | 163 | `recall` (two channels + RRF), `rerank` + `dynamic_topk`, `expand_parents`, `fulltext` (fallback), `exists_without_acl` |
| 5 | [query/answer.py](../assistant/app/kbase/query/answer.py) | 116 | `_PROMPT`, `build_sources`, `verify` (citation check), `extractive`, the three fallback messages |
| 6 | [query/guard.py](../assistant/app/kbase/query/guard.py) | 47 | Input sensitive words, output masking |
| 7 | [query/memory.py](../assistant/app/kbase/query/memory.py) | 68 | Last 3 turns verbatim, older turns summarised in a background thread |

Check yourself:

- Trace `KL-SV-80-750W 的额定转矩是多少`: which nodes run, and which two are skipped?
- Where exactly is permission enforced? (Three places: the Milvus expression, the SQL `_ACTIVE` clause, and `expand_parents`.)
- An answer contains a number that is not in the cited sources. What happens, step by step?
- What is the difference between the statuses `EMPTY`, `LOW` and `DENIED`?

### Stage 3. Knowledge graph (30 min)

Bullet 10. One file: [kg.py](../assistant/app/kbase/kg.py), 118 lines. Read `rebuild` (five Cypher statements build the graph from MySQL) and `search` (three question patterns, each one Cypher query). Note `USES*1..3`: that is the multi-hop part.

Check yourself: why can vector search not answer "磁钢用在哪些产品上"?

### Stage 4. Serving and the frontend (45 min)

Bullet 14.

| Order | File | Focus |
|---|---|---|
| 1 | [server.py](../assistant/app/kbase/server.py) lines 114–212 | `/ask/stream`: the worker thread, the queue, the SSE generator; `/feedback`, `/badcases` |
| 2 | [sse_utils.py](../assistant/app/shared/utils/sse_utils.py), 64 lines | How nodes push events to the open connection |
| 3 | [frontend/src/api/kbase.js](../frontend/src/api/kbase.js) and [KbaseAsk.vue](../frontend/src/views/KbaseAsk.vue) | How the browser consumes the events and renders citations |
| 4 | [frontend/nginx.conf](../frontend/nginx.conf) | The `proxy_buffering off` line for the 8094 route |

Check yourself: list the SSE event types in the order a normal answer emits them.

### Stage 5. Evaluation (1 h)

Bullet 13.

| Order | File | Focus |
|---|---|---|
| 1 | [RAG-02-评测结果.md](RAG-02-评测结果.md) | Read the caveats at the top first, then the tables |
| 2 | [eval/dataset.py](../assistant/app/kbase/eval/dataset.py), 146 lines | How the 297 questions were generated |
| 3 | [eval/runner.py](../assistant/app/kbase/eval/runner.py), 282 lines | Five steps: `answers`, `ragas`, `ablation`, `param`, `report` |

Do not regenerate `data/kbase_eval/dataset.json`; old and new results would stop being comparable.

Check yourself: which ablations exist in the code, and which ones on the CV do not?

### Stage 6. Production layer (1.5 h, optional on first pass)

Not on the CV bullets, but it is what `server.py` starts at boot. Read after stages 1–5 are solid. Start with [RAG-04-生产化改造说明.md](RAG-04-生产化改造说明.md), then:

| File | Lines | What it is |
|---|---|---|
| [ingest/batchjob.py](../assistant/app/kbase/ingest/batchjob.py) | 287 | Batch ingest over RabbitMQ: delay queues, dead letters, failure-ratio breaker |
| [platform/mq.py](../assistant/app/platform/mq.py) | 136 | RabbitMQ wrapper |
| [platform/resilience.py](../assistant/app/platform/resilience.py) | 239 | `Gate`, `Breaker`, `Bulkhead`, timeouts, Redis cache. `query/graph.py` and `retrieve.py` import these as `R` |
| [scheduler.py](../assistant/app/kbase/scheduler.py) | 148 | APScheduler jobs with a Redis lock, including the 03:30 reconciliation |
| [platform/alerts.py](../assistant/app/platform/alerts.py), [health.py](../assistant/app/platform/health.py), [metrics.py](../assistant/app/platform/metrics.py), [tracing.py](../assistant/app/platform/tracing.py) | ~370 | Feishu/mail alerts, dependency checks, Prometheus counters, Langfuse traces |
| [kingdee/](../assistant/app/kbase/kingdee/) | ~300 | EAS SOAP client, sync, and the mock gateway on 8096 |

### Stage 7. Shared plumbing (30 min, reference only)

Open these when a call site sends you there; do not read them front to back.

- [infra/llm/providers.py](../assistant/app/infra/llm/providers.py), [shared/model/embedding_utils.py](../assistant/app/shared/model/embedding_utils.py), [shared/model/reranker_utils.py](../assistant/app/shared/model/reranker_utils.py): where BGE-M3 and bge-reranker-v2-m3 are loaded.
- [copilot/llm.py](../assistant/app/copilot/llm.py): the LLM call wrapper (`invoke`, `stream`, `Usage`) shared with the Copilot project.
- [infra/vector_store/milvus_gateway.py](../assistant/app/infra/vector_store/milvus_gateway.py): Milvus client and vector index definitions.
- [infra/object_storage/minio_gateway.py](../assistant/app/infra/object_storage/minio_gateway.py), [infra/persistence/history_repository.py](../assistant/app/infra/persistence/history_repository.py): MinIO and MongoDB access.
- [api/auth.py](../assistant/app/api/auth.py): JWT check that produces `CurrentUser` (username + role).

## Hands-on exercises

Do these with the service running and `kbase.log` open. Each one ties a CV bullet to something you saw happen.

1. **Three routes.** Ask one PARAM, one DESC and one MIXED question. For each, write down the node order from the log and compare it with `_build` in `query/graph.py`.
2. **Permission.** Log in as a sales account and ask about a document only engineers can see. You should get the `DENIED` message. Find the line that produced it.
3. **Version switch.** Upload a document as version 1.0, ask a question about it, upload a changed copy as 1.1, ask again, then call rollback from the admin page and ask a third time. Watch `kb_doc_version.status` move through `STAGED → ACTIVE → SUPERSEDED`.
4. **Rejection.** Upload a file from `assistant/data/kbase_samples/bad/`. Find the job in the admin page, read the failure reason, and locate the `Rejected(...)` line that wrote it.
5. **Same file twice.** Upload the same file with the same version again. It should end as `SKIPPED`. Find the reason in `versioning.decide`.
6. **Self-test as a spec.** Read [scripts/kbase_selftest.py](../assistant/scripts/kbase_selftest.py) and [tests/test_kbase.py](../assistant/tests/test_kbase.py) (26 tests). Each case is a short statement of expected behaviour. To run the self-test while the service is up, borrow the service's models so the Mac does not run out of memory:

```bash
cd assistant && KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python scripts/kbase_selftest.py
```

## What to skip on the first pass

- `app/copilot` (except `llm.py`) and `app/workflow`: separate projects.
- `deploy/vllm`, `loadtest`, `deploy/monitoring`: deployment and monitoring configuration.
- `scripts/make_*`: generators for the mock sample files.
- `ingest/batch.py`, `kingdee/forms.py`: small helpers.

## Suggested schedule

| Day | Stages | Goal |
|---|---|---|
| 1 | 0 and 1 | Draw the ingest diagram from memory and explain stage → verify → activate |
| 2 | 2 and 3 | Trace any question through the query graph without opening the code |
| 3 | 4 and 5, exercises 1–5 | Explain SSE, feedback, and what the evaluation does and does not show |
| 4 | 6, exercise 6 | Explain what happens when Milvus, the LLM, or the mail server is down |

## Known gaps between this code and the CV wording

Keep these in mind when you explain the project from this repository:

- PDF goes to pypdf or MinerU; Docling handles Word only; PPT uses python-pptx.
- MongoDB stores chat messages and session summaries; document metadata is in MySQL.
- MIXED questions run SQL then vector search in sequence, not in parallel.
- Retry waits are fixed at 30 s / 2 min / 10 min, and "resume" means cached parse and embedding results on disk.
- docker-compose runs Milvus 2.6.23.
- The FastAPI service runs on the host; only its dependencies are in Docker.
- The Chunk Size / Overlap / TopK / threshold ablation has not been run.
- The sample library is 60 mock documents and about 300 chunks.
