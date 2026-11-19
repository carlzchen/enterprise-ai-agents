"""
经营与人力数据分析 Copilot 服务（端口 8093）
  POST   /chat/stream           提问，直接返回 SSE：progress / meta / table / chart / sql / delta / final / error
  POST   /chat                  提问，一次性返回 JSON（评测和压测用）
  GET    /sessions              我的会话列表          GET /history/{id}   DELETE /history/{id}
  GET    /semantic/metrics      口径库                GET /semantic/templates   GET /semantic/views（都按角色裁剪）
  POST   /export                发起导出，只返回预览，不生成文件
  POST   /export/{id}/confirm   确认或取消            GET /export/{id}/download
  POST   /feedback              点赞 / 点踩
  GET    /audit  /audit/stats   审计日志（仅管理员）   GET /badcases   PUT /badcases/{id}
  GET    /policy/documents      制度文档列表          POST /policy/upload   DELETE /policy/documents/{key}（人力资源、管理员）
  GET    /health
"""
import asyncio
import json
import queue
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.auth import CurrentUser, current_user
from app.api.http.middleware import install_trace_middleware
from app.copilot import audit, export, llm, pipeline, policy, retriever
from app.copilot.permission import access_level, hidden_columns, load_user, visible_columns
from app.copilot.semantic.loader import semantic
from app.infra.config.settings import settings
from app.platform import metrics as platform_metrics
from app.platform import resilience as RS
from app.shared.runtime.logger import logger
from app.shared.utils.sse_utils import SSEEvent, close_session, create_sse_queue, push_to_session

app = FastAPI(title="MiniERP 经营与人力数据分析 Copilot", version="1.0.0")
install_trace_middleware(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Trace-Id"])

POLICY_ADMIN = {"ADMIN", "HR"}
_ready = {"semantic": False, "vector": False, "policy": False}


def _warm_up():
    """启动后在后台加载语义层、建向量索引、导入制度文档。问数主流程只依赖 semantic，其余没好之前会自动降级"""
    try:
        semantic.load()
        audit.ensure_tables()
        _ready["semantic"] = True
        _ready["vector"] = retriever.ensure_index()
        policy.ingest_all()
        _ready["policy"] = True
        retriever.search("上个月各产线加班率", "template")      # 预热一次检索和模型连接，避免第一个用户等很久
        llm.invoke("回复：好")
        logger.info(f"[copilot] 预热完成：{_ready}")
    except Exception:
        logger.exception("[copilot] 预热失败")


@app.on_event("startup")
def startup():
    semantic.load()
    _ready["semantic"] = True
    threading.Thread(target=_warm_up, daemon=True).start()


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    session_id: str | None = None
    skip_answer: bool = False        # 评测：只要结构化结果，不生成文字回答
    baseline: bool = False           # 对照实验：关闭规则路由、模板筛选、Schema 裁剪、缓存
    no_cache: bool = False


def _ctx(user: CurrentUser):
    return load_user(user.username, user.role)


def _options(req: ChatRequest, user: CurrentUser) -> dict:
    special = user.role == "ADMIN"                       # 对照和评测开关只对管理员开放，普通用户传了也不生效
    return {"skip_answer": req.skip_answer and special, "baseline": req.baseline and special, "no_cache": req.no_cache and special}


def _check_owner(session_id: str | None, user: CurrentUser):
    if session_id:
        owner = pipeline.session_owner(session_id)
        if owner and owner != user.username:
            raise HTTPException(status_code=403, detail="无权访问该会话")


GATE = RS.Gate("copilot", settings.copilot_rate_per_minute, settings.copilot_concurrency)
platform_metrics.install(app)


def _busy(e: RS.TooBusy) -> HTTPException:
    return HTTPException(status_code=429, detail=str(e), headers={"Retry-After": str(e.retry_after)})


@app.get("/ops/status")
def ops_status(user: CurrentUser = Depends(current_user)):
    return {"breakers": RS.breakers(), "limits": {"每人每分钟提问上限": settings.copilot_rate_per_minute, "同时处理的提问上限": settings.copilot_concurrency}}


@app.post("/chat")
def chat(req: ChatRequest, request: Request, user: CurrentUser = Depends(current_user)):
    _check_owner(req.session_id, user)
    try:
        with GATE.enter(user.username):
            return pipeline.run(req.query, _ctx(user), req.session_id, stream=False, trace_id=request.headers.get("X-Trace-Id", ""),
                                options=_options(req, user))
    except RS.TooBusy as e:
        raise _busy(e)


def _pack(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request, user: CurrentUser = Depends(current_user)):
    _check_owner(req.session_id, user)
    ctx = _ctx(user)
    request_id, session_id = uuid.uuid4().hex, req.session_id or uuid.uuid4().hex
    gate = GATE.enter(user.username)
    try:
        gate.__enter__()                                     # 限流和并发检查：超了直接返回 429；名额在后台线程结束时归还
    except RS.TooBusy as e:
        raise _busy(e)
    q = create_sse_queue(request_id)
    trace_id, options = request.headers.get("X-Trace-Id", ""), _options(req, user)

    def work():
        try:
            with logger.contextualize(trace_id=trace_id or request_id[:16]):
                payload = pipeline.run(req.query, ctx, session_id, stream=True, request_id=request_id, trace_id=trace_id, options=options)
            push_to_session(request_id, SSEEvent.FINAL, payload)
        except Exception as e:
            logger.exception("[copilot] 提问处理失败")
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
            from app.shared.utils import sse_utils
            sse_utils._queues.pop(request_id, None)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/sessions")
def sessions(user: CurrentUser = Depends(current_user)):
    return pipeline.list_sessions(user.username)


@app.get("/history/{session_id}")
def history(session_id: str, user: CurrentUser = Depends(current_user)):
    _check_owner(session_id, user)
    return pipeline.history(session_id)


@app.delete("/history/{session_id}")
def clear(session_id: str, user: CurrentUser = Depends(current_user)):
    _check_owner(session_id, user)
    return {"deleted": pipeline.clear_session(session_id)}


# ---------- 语义层（按角色裁剪后展示） ----------

@app.get("/semantic/metrics")
def metrics(user: CurrentUser = Depends(current_user)):
    return [{"name": m.name, "aliases": m.aliases, "type": m.type, "formula": m.formula, "depends_on": m.depends_on, "view": m.view,
             "note": m.note, "dims": m.dims, "unit": m.unit, "sensitivity": m.sensitivity, "templates": m.templates} for m in semantic.metrics]


@app.get("/semantic/templates")
def templates(user: CurrentUser = Depends(current_user)):
    return [{"id": t.id, "name": t.name, "intent": t.intent, "desc": t.desc, "metrics": t.metrics, "dims": list(t.dims), "filters": list(t.filters),
             "time": t.time, "examples": t.examples, "roles": t.roles, "usable": not t.roles or user.role in t.roles}
            for t in semantic.templates.values()]


@app.get("/semantic/views")
def views(user: CurrentUser = Depends(current_user)):
    out = []
    for v in semantic.views.values():
        level = access_level(v, user.role)
        if not level:
            continue
        out.append({"name": v.name, "title": v.title, "desc": v.desc, "access": level, "data_until": semantic.data_until.get(v.name),
                    "columns": [{"name": c, "desc": v.columns[c]} for c in visible_columns(v, user.role)],
                    "hidden": [v.columns.get(c) or c for c in hidden_columns(v, user.role)]})
    return out


# ---------- 导出（人工确认） ----------

class ExportRequest(BaseModel):
    result_id: str
    audit_id: int | None = None


class ConfirmRequest(BaseModel):
    decision: str = Field(pattern="^(confirm|cancel)$")


@app.post("/export")
def export_start(req: ExportRequest, user: CurrentUser = Depends(current_user)):
    try:
        return export.start(req.result_id, user.username, req.audit_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=410, detail=str(e))


@app.post("/export/{export_id}/confirm")
def export_confirm(export_id: str, req: ConfirmRequest, user: CurrentUser = Depends(current_user)):
    try:
        return export.resume(export_id, user.username, req.decision)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/export/{export_id}/download")
def export_download(export_id: str, user: CurrentUser = Depends(current_user)):
    try:
        path, name = export.file_of(export_id, user.username)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return FileResponse(path, filename=name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ---------- 反馈、审计、Bad Case ----------

class FeedbackRequest(BaseModel):
    audit_id: int
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=500)


@app.post("/feedback")
def feedback(req: FeedbackRequest, user: CurrentUser = Depends(current_user)):
    try:
        audit.feedback(req.audit_id, user.username, req.rating, req.comment)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True}


def admin_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="仅管理员可以查看")
    return user


@app.get("/audit")
def audit_list(page: int = 1, size: int = 20, username: str | None = None, status: str | None = None, keyword: str | None = None,
               user: CurrentUser = Depends(admin_user)):
    return audit.list_audit(page, size, username, status, keyword)


@app.get("/audit/stats")
def audit_stats(user: CurrentUser = Depends(admin_user)):
    return audit.stats()


@app.get("/badcases")
def badcases(user: CurrentUser = Depends(admin_user)):
    return audit.bad_cases()


class BadCaseUpdate(BaseModel):
    cause: str | None = None
    fixed: bool = False


@app.put("/badcases/{feedback_id}")
def badcase_update(feedback_id: int, req: BadCaseUpdate, user: CurrentUser = Depends(admin_user)):
    audit.mark_bad_case(feedback_id, req.cause, req.fixed)
    return {"ok": True}


# ---------- 制度文档 ----------

def policy_admin(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.role not in POLICY_ADMIN:
        raise HTTPException(status_code=403, detail="仅人力资源或管理员可以维护制度文档")
    return user


@app.get("/policy/documents")
def policy_documents(user: CurrentUser = Depends(current_user)):
    return policy.list_documents()


@app.post("/policy/upload")
def policy_upload(file: UploadFile = File(...), user: CurrentUser = Depends(policy_admin)):
    name = Path(file.filename or "").name
    if Path(name).suffix.lower() not in (".md", ".txt", ".docx"):
        raise HTTPException(status_code=400, detail="只支持 .md、.txt、.docx")
    policy.POLICY_DIR.mkdir(parents=True, exist_ok=True)
    target = policy.POLICY_DIR / name
    with target.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return policy.ingest_file(target, uploaded_by=user.username)


@app.delete("/policy/documents/{doc_key}")
def policy_delete(doc_key: str, user: CurrentUser = Depends(policy_admin)):
    if not policy.delete_document(doc_key):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok", **_ready}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.app_host, port=settings.copilot_app_port)
