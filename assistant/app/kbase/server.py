"""
企业知识库服务（端口 8094）
  问答    POST /ask/stream（SSE）  POST /ask   GET /sessions   GET /history/{id}   DELETE /history/{id}
  反馈    POST /feedback           GET /badcases
  文档    GET /documents   GET /documents/{doc_key}   POST /documents/upload   POST /documents/{doc_key}/rollback   DELETE /documents/{doc_key}
  任务    GET /jobs        POST /jobs/{job_id}/retry
  批量    GET /batches   POST /batches/scan|plm|zip   POST /batches/{id}/pause|resume|cancel|retry-failed
  调度    GET /schedules   POST /schedules/{name}/run   PUT /schedules/{name}
  告警    GET /ops/alerts   POST /ops/alerts/{id}/ack|close   GET /ops/status   GET /metrics
  运维    GET /alerts   GET /outbox   POST /outbox/send   POST /reconcile   POST /kingdee/sync   POST /kg/rebuild   GET /stats
  参数    GET /params?model=
  身份    GET /auth/sso/login → 公司 SSO → GET /auth/sso/callback   GET /auth/me   POST /auth/sso/backchannel-logout（SSO 后端通道注销）
  网络    只在内网和 VPN 网段提供服务（app/kbase/auth/network.py + deploy/nginx）
  图片    GET /images/{doc_key}/{sha}（鉴权后跳转到 MinIO 临时签名链接）
  审计    GET /audit   GET /compensations   POST /compensations/run   GET /events
"""
import asyncio
import json
import queue
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from fastapi.responses import RedirectResponse

from app.api.auth import CurrentUser, current_user
from app.kbase import authz
from app.kbase.auth import network, principal, sso
from app.kbase.auth.deps import Who, current_who, issue, kb_user
from app.api.http.middleware import install_trace_middleware
from app.infra.config.settings import settings
from app.kbase import kg, store, vectors
from app.kbase.ingest import graph as ingest_graph
from app.kbase import scheduler
from app.kbase.ingest import batchjob, events as kb_events, notify, versioning
from app.platform import alerts as ops, health as deps, metrics as pm, mq
from app.platform import resilience as R
from app.kbase.kingdee import sync as kd_sync
from app.kbase.query import graph as query_graph
from app.shared.runtime.logger import PROJECT_ROOT, logger
from app.shared.utils import sse_utils
from app.shared.utils.sse_utils import SSEEvent, close_session, create_sse_queue, push_to_session

app = FastAPI(title="MiniERP 企业知识库", version="2.0.0")
install_trace_middleware(app)
network.install(app)                                         # 只在内网和 VPN 网段提供服务
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Trace-Id"])

GATE = R.Gate("kbase", settings.kbase_rate_per_minute, settings.kbase_concurrency)
MAINTAINERS = {"ADMIN", "ENGINEER", "QUALITY"}
UPLOAD_DIR = PROJECT_ROOT / "output" / "kbase" / "upload"
_ready = {"tables": False, "vectors": False, "models": False}



def _warm_up() -> None:
    try:
        vectors.ensure()
        _ready["vectors"] = True
        from app.kbase.query import retrieve
        emb = retrieve.embed("预热")
        retrieve.rerank_scores("预热", [{"content": "预热"}])
        del emb
        _ready["models"] = True
        logger.info(f"[kbase] 预热完成：{_ready}")
    except Exception:
        logger.exception("[kbase] 预热失败")


@app.on_event("startup")
def startup():
    store.ensure_tables()
    _ready["tables"] = True
    ingest_graph.start_retry_worker()
    notify.start_sender()
    kb_events.start()                                        # 通知事件：转发到 RabbitMQ，消费后经 SMTP 发出
    try:                                                     # 图片桶必须是私有的（早期部署脚本给 images/ 开过匿名读）
        from app.infra.object_storage.minio_gateway import minio_gateway
        minio_gateway.make_private()
    except Exception:
        logger.warning("[kbase] 没能确认 MinIO 桶是私有的")
    ops.ensure()
    batchjob.start_workers()                                 # 消息队列的入库消费线程
    scheduler.start()                                        # 定时任务：EAS 同步、目录扫描、对账、健康检查……
    threading.Thread(target=_warm_up, daemon=True).start()


def maintainer(user: CurrentUser = Depends(kb_user)) -> CurrentUser:
    if user.role not in MAINTAINERS:
        raise HTTPException(status_code=403, detail="仅研发、质量或管理员可以维护知识库")
    return user


def admin(user: CurrentUser = Depends(kb_user)) -> CurrentUser:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可以操作")
    return user


def _rows(rows: list[dict]) -> list[dict]:
    return json.loads(json.dumps(rows, ensure_ascii=False, default=str))


# ---------------------------------------------------------------- 问答

class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None


def _own(session_id: str | None, user: CurrentUser) -> None:
    if session_id:
        owner = query_graph.session_owner(session_id)
        if owner and owner != user.username:
            raise HTTPException(status_code=403, detail="无权访问该会话")


def _busy(e: R.TooBusy) -> HTTPException:
    return HTTPException(status_code=429, detail=str(e), headers={"Retry-After": str(e.retry_after)})


@app.post("/ask")
def ask(req: AskRequest, request: Request, who: Who = Depends(current_who)):
    user = who.user
    _own(req.session_id, user)
    try:
        with GATE.enter(user.username):
            return query_graph.ask(req.query, user.username, user.role, req.session_id, trace_id=request.headers.get("X-Trace-Id", ""), scope=who.scope)
    except R.TooBusy as e:
        raise _busy(e)


def _pack(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/ask/stream")
async def ask_stream(req: AskRequest, request: Request, who: Who = Depends(current_who)):
    user = who.user
    _own(req.session_id, user)
    request_id, session_id = uuid.uuid4().hex, req.session_id or uuid.uuid4().hex
    gate = GATE.enter(user.username)
    try:
        gate.__enter__()                                     # 限流和并发检查在这里做，超了直接返回 429；名额在后台线程结束时归还
    except R.TooBusy as e:
        raise _busy(e)
    q = create_sse_queue(request_id)
    trace_id = request.headers.get("X-Trace-Id", "")

    def work():
        try:
            with logger.contextualize(trace_id=trace_id or request_id[:16]):
                payload = query_graph.ask(req.query, user.username, user.role, session_id, stream=True, request_id=request_id, trace_id=trace_id,
                                          scope=who.scope)
            push_to_session(request_id, SSEEvent.FINAL, payload)
        except Exception as e:
            logger.exception("[kbase] 问答失败")
            push_to_session(request_id, SSEEvent.ERROR, {"error": f"处理失败：{str(e)[:200]}"})
        finally:
            gate.__exit__(None, None, None)
            close_session(request_id)

    threading.Thread(target=work, daemon=True).start()

    async def gen():
        loop = asyncio.get_running_loop()
        yield _pack(SSEEvent.READY, {"session_id": session_id})
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await loop.run_in_executor(None, q.get, True, 1.0)
                except queue.Empty:
                    continue
                if msg["event"] == SSEEvent.CLOSE:
                    break
                yield _pack(msg["event"], msg["data"])
        finally:
            sse_utils._queues.pop(request_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/sessions")
def sessions(user: CurrentUser = Depends(kb_user)):
    return query_graph.sessions(user.username)


@app.get("/history/{session_id}")
def history(session_id: str, user: CurrentUser = Depends(kb_user)):
    _own(session_id, user)
    return query_graph.history(session_id)


@app.delete("/history/{session_id}")
def clear(session_id: str, user: CurrentUser = Depends(kb_user)):
    _own(session_id, user)
    return {"deleted": query_graph.clear(session_id)}


class FeedbackRequest(BaseModel):
    answer_id: str
    rating: int = Field(ge=-1, le=1)
    reason: str | None = Field(default=None, max_length=30)
    correct_answer: str | None = Field(default=None, max_length=2000)
    comment: str | None = Field(default=None, max_length=500)


@app.post("/feedback")
def feedback(req: FeedbackRequest, user: CurrentUser = Depends(kb_user)):
    try:
        query_graph.feedback(req.answer_id, user.username, req.rating, req.reason, req.correct_answer, req.comment)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


@app.get("/badcases")
def badcases(user: CurrentUser = Depends(maintainer)):
    return query_graph.bad_cases()


# ---------------------------------------------------------------- 文档与版本

@app.get("/documents")
def documents(who: Who = Depends(current_who)):
    """只列出当前用户有权看到的文档（和问答用同一个 Scope）"""
    where, args = who.scope.sql("d")
    return _rows(store.query(
        "SELECT d.doc_key, d.title, d.doc_type, d.source, d.security_level, d.acl_groups, d.owner_email, d.product_code, d.active_version, d.updated_at, "
        "v.chunk_count, v.param_count, v.parsed_by, v.file_type, v.route, v.file_name, v.activated_at, "
        "(SELECT COUNT(*) FROM kb_doc_version x WHERE x.doc_key = d.doc_key) AS versions, "
        "(SELECT COUNT(*) FROM kb_alert a WHERE a.doc_key = d.doc_key AND a.version = d.active_version AND a.status = 'OPEN') AS alerts "
        f"FROM kb_document d LEFT JOIN kb_doc_version v ON v.doc_key = d.doc_key AND v.version = d.active_version WHERE {where} "
        "ORDER BY d.doc_type, d.doc_key", args))


@app.get("/documents/{doc_key}")
def document(doc_key: str, who: Who = Depends(current_who)):
    where, args = who.scope.sql("d")
    d = store.one(f"SELECT d.* FROM kb_document d WHERE d.doc_key = %s AND {where}", (doc_key, *args))
    if not d:
        raise HTTPException(status_code=404, detail="文档不存在或无权查看")
    return _rows([{**d,
                   "versions": store.query("SELECT version, status, file_name, file_type, route, parsed_by, chunk_count, param_count, diff, created_at, activated_at, "
                                           "superseded_at, error FROM kb_doc_version WHERE doc_key = %s ORDER BY id DESC", (doc_key,)),
                   "chunk_states": store.query("SELECT state, COUNT(*) AS n FROM kb_chunk WHERE doc_key = %s GROUP BY state", (doc_key,)),
                   "alerts": store.query("SELECT version, kind, message, status, created_at FROM kb_alert WHERE doc_key = %s ORDER BY id DESC", (doc_key,)),
                   "sections": store.query("SELECT seq, title, page, CHAR_LENGTH(content) AS chars FROM kb_parent WHERE doc_key = %s AND version = %s ORDER BY seq",
                                           (doc_key, d["active_version"]))}])[0]


@app.post("/documents/upload")
def upload(background: BackgroundTasks, file: UploadFile = File(...), doc_key: str = Form(...), version: str = Form(...), title: str = Form(...),
           doc_type: str = Form(...), security_level: str = Form(""), acl_groups: str = Form(""), product_code: str = Form(""),
           user: CurrentUser = Depends(maintainer)):
    """上传一个文件。密级和可见范围必须填；任务在后台执行，用返回的任务号查进度"""
    name = Path(file.filename or "").name
    folder = UPLOAD_DIR / uuid.uuid4().hex[:8]
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    with target.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    meta = {"title": title.strip(), "doc_type": doc_type.strip().upper(), "source": "UPLOAD", "security_level": security_level.strip().upper(),
            "acl_groups": ",".join(g.strip() if g.strip().startswith("D:") else g.strip().upper() for g in acl_groups.split(",") if g.strip()),
            "product_code": product_code.strip() or None,
            "owner_email": f"{user.username}@kb-demo.example.com"}
    job_id = ingest_graph.submit(str(target), doc_key.strip(), version.strip(), meta)
    background.add_task(ingest_graph.run, job_id)
    return {"job_id": job_id, "message": "已开始处理"}


@app.post("/documents/{doc_key}/rollback")
def rollback(doc_key: str, user: CurrentUser = Depends(maintainer)):
    try:
        return {"active_version": versioning.rollback(doc_key)}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.delete("/documents/{doc_key}")
def delete(doc_key: str, user: CurrentUser = Depends(admin)):
    if not versioning.delete_document(doc_key):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"deleted": doc_key}


@app.get("/params")
def params(model: str, who: Who = Depends(current_who)):
    where, args = who.scope.sql("d")
    return _rows(store.query(
        "SELECT p.model, p.param_name, p.value_text, p.unit, p.source_ref, d.title, d.doc_type, p.version FROM kb_param p JOIN kb_document d "
        f"ON d.doc_key = p.doc_key AND d.active_version = p.version WHERE p.model = %s AND {where} ORDER BY d.doc_type, p.id", (model, *args)))


# ---------------------------------------------------------------- 身份（公司 SSO）与图片

@app.get("/auth/sso/login")
def sso_login(login_hint: str | None = None):
    """跳到公司 SSO。state、nonce、PKCE 的 code_verifier 存 Redis 10 分钟，回调时核对"""
    import secrets
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    verifier, challenge = sso.pkce()
    if not sso.save_state(state, {"nonce": nonce, "verifier": verifier}):
        raise HTTPException(status_code=503, detail="登录状态暂时无法保存，请稍后再试")
    try:
        return RedirectResponse(sso.authorize_url(state, nonce, challenge, login_hint), status_code=302)
    except sso.SSOUnavailable:
        raise HTTPException(status_code=503, detail="公司 SSO 暂时不可用，请稍后再试")


@app.get("/auth/sso/callback")
def sso_callback(state: str, code: str | None = None, error: str | None = None, error_description: str | None = None, mode: str | None = None):
    """SSO 回跳：核对 state → 授权码换 ID Token 并验签 → 解析身份 → 签发知识库登录凭证，带回前端（mode=json 时直接返回，联调用）"""
    saved = sso.pop_state(state)                              # 取出即删除：state 只能用一次
    if not saved:
        raise HTTPException(status_code=400, detail="登录请求已过期或无效，请重新登录")
    if error or not code:
        raise HTTPException(status_code=403, detail=f"SSO 拒绝登录：{error_description or error}")
    try:
        claims = sso.exchange(code, saved["verifier"], saved["nonce"])
        scope = principal.login(claims)
    except sso.SSOError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except sso.SSOUnavailable:
        raise HTTPException(status_code=503, detail="公司 SSO 暂时不可用，请稍后再试")
    except principal.AuthDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    token = issue(scope, claims.get("name"))
    if mode == "json":
        return {"token": token, "user": principal.describe(scope)}
    return RedirectResponse(f"{settings.sso_after_login}#token={token}", status_code=302)      # 放在 # 后面：不进服务器日志和 Referer


@app.post("/auth/sso/backchannel-logout")
async def sso_backchannel_logout(request: Request):
    """OIDC 后端通道注销：SSO 推来 logout_token（表单字段），验签后吊销这个人的全部知识库登录"""
    form = await request.form()
    try:
        out = principal.on_backchannel_logout(str(form.get("logout_token") or ""))
    except sso.SSOError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return out


@app.get("/auth/me")
def me(who: Who = Depends(current_who)):
    s = who.scope
    if s.source == "sso":
        return principal.describe(s)
    return {"user_id": s.user, "roles": [s.role], "clearance": authz.LEVEL_CN.get(s.clearance), "tokens": list(s.tokens), "notes": []}


@app.get("/images/{doc_key}/{sha}")
def image(doc_key: str, sha: str, who: Who = Depends(current_who)):
    """文档图片：先按统一授权判断能不能看这份文档，再确认图片属于生效版本，最后跳转到几分钟有效的临时签名链接"""
    d = store.one("SELECT security_level, acl_groups, active_version FROM kb_document WHERE doc_key = %s", (doc_key,))
    if not d or not who.scope.can_view(d["security_level"], d["acl_groups"]):
        raise HTTPException(status_code=404, detail="图片不存在或无权查看")
    img = store.one("SELECT object_key FROM kb_image WHERE doc_key = %s AND version = %s AND sha = %s", (doc_key, d["active_version"], sha[:16]))
    if not img:
        raise HTTPException(status_code=404, detail="图片不存在或不属于当前生效版本")
    from app.infra.object_storage.minio_gateway import minio_gateway
    return RedirectResponse(minio_gateway.presigned_url(img["object_key"], settings.kbase_image_ttl), status_code=302)


@app.get("/audit")
def audit_log(limit: int = 100, action: str | None = None, user: CurrentUser = Depends(kb_user)):
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可以查看审计日志")
    sql, args = "SELECT * FROM kb_audit_log", []
    if action:
        sql, args = sql + " WHERE action = %s", [action]
    return _rows(store.query(sql + " ORDER BY id DESC LIMIT %s", (*args, min(limit, 500))))


# ---------------------------------------------------------------- 任务与运维

@app.get("/jobs")
def jobs(limit: int = 50, user: CurrentUser = Depends(maintainer)):
    return _rows(store.query("SELECT job_id, doc_key, version, file_name, stage, status, attempts, next_retry_at, retryable, error, started_at, finished_at "
                             "FROM kb_ingest_job ORDER BY started_at DESC LIMIT %s", (min(limit, 200),)))


@app.get("/jobs/{job_id}")
def job(job_id: str, user: CurrentUser = Depends(maintainer)):
    row = store.one("SELECT job_id, doc_key, version, file_name, stage, status, attempts, next_retry_at, error, finished_at FROM kb_ingest_job WHERE job_id = %s", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _rows([row])[0]


@app.post("/jobs/{job_id}/retry")
def retry(job_id: str, background: BackgroundTasks, user: CurrentUser = Depends(maintainer)):
    if not store.one("SELECT 1 FROM kb_ingest_job WHERE job_id = %s AND status IN ('FAILED', 'RETRY')", (job_id,)):
        raise HTTPException(status_code=409, detail="只有失败或等待重试的任务可以手动重试")
    background.add_task(ingest_graph.run, job_id)
    return {"job_id": job_id, "message": "已重新开始"}


@app.get("/compensations")
def compensations(user: CurrentUser = Depends(maintainer)):
    return _rows(store.query("SELECT * FROM kb_compensation ORDER BY id DESC LIMIT 100"))


@app.post("/compensations/run")
def compensations_run(user: CurrentUser = Depends(maintainer)):
    return _rows(versioning.run_compensations(50))


@app.get("/events")
def kb_event_list(user: CurrentUser = Depends(maintainer)):
    return _rows(store.query("SELECT event_id, kind, doc_key, version, status, tries, last_error, created_at, published_at, handled_at FROM kb_event "
                             "ORDER BY created_at DESC LIMIT 100"))


@app.get("/alerts")
def alerts(user: CurrentUser = Depends(maintainer)):
    return _rows(store.query("SELECT a.id, a.doc_key, a.version, a.kind, a.message, a.status, a.created_at, d.title FROM kb_alert a "
                             "LEFT JOIN kb_document d ON d.doc_key = a.doc_key ORDER BY a.id DESC LIMIT 200"))


@app.get("/outbox")
def outbox(user: CurrentUser = Depends(maintainer)):
    return _rows(store.query("SELECT id, to_email, subject, body, status, tries, last_error, created_at, sent_at FROM kb_outbox ORDER BY id DESC LIMIT 100"))


@app.post("/outbox/send")
def outbox_send(user: CurrentUser = Depends(admin)):
    return notify.send_pending()


@app.post("/reconcile")
def reconcile(user: CurrentUser = Depends(admin)):
    return versioning.reconcile_all()


@app.post("/kingdee/sync")
def kingdee_sync(user: CurrentUser = Depends(maintainer)):
    try:
        return kd_sync.sync_all()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"金蝶 EAS 同步失败：{str(e)[:200]}")


@app.post("/kg/rebuild")
def kg_rebuild(user: CurrentUser = Depends(maintainer)):
    try:
        return kg.rebuild()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"知识图谱不可用：{str(e)[:200]}")


@app.get("/stats")
def stats(user: CurrentUser = Depends(kb_user)):
    s = store.one("SELECT (SELECT COUNT(*) FROM kb_document WHERE active_version IS NOT NULL) AS documents, "
                  "(SELECT COUNT(*) FROM kb_chunk c JOIN kb_document d ON d.doc_key = c.doc_key AND d.active_version = c.version) AS chunks, "
                  "(SELECT COUNT(*) FROM kb_param p JOIN kb_document d ON d.doc_key = p.doc_key AND d.active_version = p.version) AS params, "
                  "(SELECT COUNT(*) FROM kb_alert WHERE status = 'OPEN') AS alerts, (SELECT COUNT(*) FROM kb_ingest_job WHERE status = 'FAILED') AS failed_jobs, "
                  "(SELECT COUNT(*) FROM kb_outbox WHERE status = 'PENDING') AS pending_mails, (SELECT COUNT(*) FROM kb_answer_log) AS answers, "
                  "(SELECT COUNT(*) FROM kb_feedback WHERE rating < 0) AS bad_cases, (SELECT COUNT(*) FROM kd_material) AS kd_materials, "
                  "(SELECT MAX(synced_at) FROM kd_material) AS kd_synced_at")
    return {**_rows([s])[0], "kg": kg.available(), "smtp": bool(settings.smtp_host)}


# ---------------------------------------------------------------- 批量入库

@app.get("/batches")
def batches(user: CurrentUser = Depends(maintainer)):
    return {"batches": _rows(batchjob.listing()), "queue": mq.depth(batchjob.QUEUE), "mq": mq.available(), "inbox": settings.kbase_inbox}


@app.get("/batches/{batch_id}")
def batch(batch_id: str, user: CurrentUser = Depends(maintainer)):
    b = batchjob.detail(batch_id)
    if not b:
        raise HTTPException(status_code=404, detail="批次不存在")
    return _rows([b])[0]


@app.post("/batches/scan")
def batch_scan(user: CurrentUser = Depends(maintainer)):
    """立即扫描共享目录，把新增和有变化的文件排成一个批次"""
    return batchjob.scan_inbox(user.username)


@app.post("/batches/plm")
def batch_plm(user: CurrentUser = Depends(maintainer)):
    try:
        return batchjob.from_plm(user.username)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"读取 PLM 文档失败：{str(e)[:200]}")


@app.post("/batches/zip")
def batch_zip(file: UploadFile = File(...), title: str = Form(""), user: CurrentUser = Depends(maintainer)):
    """上传一个压缩包：里面按「文档类型/编号__v版本__标题.扩展名」放文件，或者带一份 manifest.csv"""
    name = Path(file.filename or "batch.zip").name
    if not name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 zip 压缩包")
    folder = UPLOAD_DIR / ("zip_" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / name).open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        return batchjob.from_zip(folder / name, user.username, title.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"压缩包处理失败：{str(e)[:200]}")


@app.post("/batches/{batch_id}/{action}")
def batch_action(batch_id: str, action: str, user: CurrentUser = Depends(maintainer)):
    if action == "pause":
        ok = batchjob.pause(batch_id, user.username)
    elif action == "resume":
        ok = batchjob.resume(batch_id)
    elif action == "cancel":
        ok = batchjob.cancel(batch_id)
    elif action == "retry-failed":
        return {"requeued": batchjob.retry_failed(batch_id)}
    else:
        raise HTTPException(status_code=404, detail="不支持的操作")
    if not ok:
        raise HTTPException(status_code=409, detail="批次当前的状态不能做这个操作")
    return {"ok": True}


# ---------------------------------------------------------------- 定时任务、告警、运行状态

@app.get("/schedules")
def schedules(user: CurrentUser = Depends(maintainer)):
    return {"jobs": _rows(scheduler.listing()), "runs": _rows(scheduler.runs(limit=40))}


@app.post("/schedules/{name}/run")
def schedule_run(name: str, background: BackgroundTasks, user: CurrentUser = Depends(maintainer)):
    if name not in scheduler.JOBS:
        raise HTTPException(status_code=404, detail="没有这个任务")
    background.add_task(scheduler.run, name, user.username)
    return {"message": "已开始执行，稍后刷新看结果"}


class ToggleRequest(BaseModel):
    enabled: bool


@app.put("/schedules/{name}")
def schedule_toggle(name: str, req: ToggleRequest, user: CurrentUser = Depends(admin)):
    scheduler.toggle(name, req.enabled)
    return {"ok": True}


@app.get("/ops/alerts")
def ops_alerts(user: CurrentUser = Depends(maintainer)):
    return _rows(ops.listing())


@app.post("/ops/alerts/{alert_id}/{action}")
def ops_alert_action(alert_id: int, action: str, user: CurrentUser = Depends(maintainer)):
    (ops.ack if action == "ack" else ops.close)(alert_id, user.username)
    return {"ok": True}


@app.get("/ops/status")
def ops_status(user: CurrentUser = Depends(maintainer)):
    """运行状态一览：各依赖是否正常、熔断器、队列、限流配置"""
    return {"dependencies": deps.snapshot(), "breakers": R.breakers(), "queue": mq.depth(batchjob.QUEUE),
            "limits": {"每人每分钟提问上限": settings.kbase_rate_per_minute, "同时处理的提问上限": settings.kbase_concurrency,
                       "入库消费线程": settings.kbase_workers, "批次失败比例上限": settings.kbase_batch_fail_ratio},
            "feishu": bool(settings.feishu_webhook), "smtp": bool(settings.smtp_host)}


def _refresh_gauges() -> None:
    for k, v in mq.depth(batchjob.QUEUE).items():
        pm.QUEUE.labels(k).set(v)
    for r in store.query("SELECT level, COUNT(*) n FROM ops_alert WHERE status <> 'CLOSED' GROUP BY level"):
        pm.ALERTS.labels(r["level"]).set(r["n"])
    for r in store.query("SELECT status, COUNT(*) n FROM kb_ingest_job WHERE started_at >= CURDATE() GROUP BY status"):
        pm.INGEST.labels(r["status"]).set(r["n"])


pm.install(app, _refresh_gauges)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(max_length=64)
    sparse: bool = False


class RerankRequest(BaseModel):
    query: str
    texts: list[str] = Field(max_length=100)


@app.post("/internal/embed")
def internal_embed(req: EmbedRequest, user: CurrentUser = Depends(admin)):
    """评测脚本用：借用服务里已经加载的向量模型，免得每个脚本都再加载一份（一份模型两个多 G）"""
    from app.infra.llm.providers import llm_providers
    from app.kbase.query import retrieve
    with retrieve._model_lock:
        e = llm_providers.generate_embeddings(req.texts)
    return {"dense": [list(map(float, v)) for v in e["dense"]], "sparse": [{str(k): float(v) for k, v in s.items()} for s in e["sparse"]] if req.sparse else None}


@app.post("/internal/rerank")
def internal_rerank(req: RerankRequest, user: CurrentUser = Depends(admin)):
    from app.kbase.query import retrieve
    return {"scores": retrieve.rerank_scores(req.query, [{"content": t} for t in req.texts])}


@app.get("/health")
def health():
    return {"status": "ok", **_ready}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.app_host, port=settings.kbase_app_port)
