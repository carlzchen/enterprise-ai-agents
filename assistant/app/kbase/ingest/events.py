"""
通知事件：持久化事件 → RabbitMQ → SMTP。

  record()   业务代码在自己的事务里写一行 kb_event（版本切换和事件同一次提交：切换成功就一定有通知，回滚了就一定没有）
  relay      转发线程把 NEW 状态的事件投递到 RabbitMQ 队列 kb.events（发布确认后才标成 PUBLISHED）；队列不可用就留在表里下次再投
  consumer   消费者收到事件 → 渲染成邮件写进发件箱（按 event_id 去重，重复投递只会有一封）→ 立即经 SMTP 发出 → 标成 DONE
  兜底       RabbitMQ 连续 5 分钟不可用时，转发线程直接在本进程里处理积压的事件（降级），保证通知不丢

事件类型：VERSION_SWITCHED 新版本生效 / INGEST_FAILED 入库失败 / DRAWING_MISMATCH 图纸与 ERP 不一致 /
         DRAWING_REVIEW 图纸不完整待复核 / RECONCILE_BROKEN 对账修复失败 / COMPENSATION_FAILED 补偿任务耗尽
"""
import json
import threading
import time
import uuid
from datetime import datetime, timedelta

from app.infra.config.settings import settings
from app.kbase import store
from app.kbase.ingest import notify
from app.shared.runtime.logger import logger

LOCAL_AFTER = 300


def record(cur, kind: str, doc_key: str | None, version: str | None, payload: dict, event_id: str | None = None) -> str:
    """在调用方的事务里写事件。cur 为 None 时单独提交。event_id 相同的事件只记一次"""
    eid = event_id or uuid.uuid4().hex
    sql = "INSERT IGNORE INTO kb_event (event_id, kind, doc_key, version, payload) VALUES (%s,%s,%s,%s,%s)"
    args = (eid[:32], kind, doc_key, version, store.dumps(payload))
    if cur is None:
        store.execute(sql, args)
    else:
        cur.execute(sql, args)
    return eid


def dedup_id(*parts) -> str:
    import hashlib
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


# ---------------------------------------------------------------- 渲染成邮件

def render(ev: dict) -> tuple[str | None, str, str]:
    """返回（收件人, 标题, 正文）。正文只放文档名、版本、任务号、原因摘要，不放文档正文"""
    p = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])
    title, to = p.get("title") or ev.get("doc_key"), p.get("owner_email")
    kind = ev["kind"]
    if kind == "VERSION_SWITCHED":
        d = p.get("diff") or {}
        return to, f"[知识库] 新版本已生效：{title} v{ev['version']}", (
            f"文档 {ev['doc_key']} 生效版本 {p.get('old') or '无'} → {ev['version']}。\n"
            f"切片变化：新增 {d.get('new', 0)}，沿用 {d.get('reused', 0)}（向量和主题结果直接复用），失效 {d.get('vanished', 0)}。\n"
            f"参数 {p.get('params', 0)} 条，图片 {p.get('images', 0)} 张。问答已切换到新版本，旧版本保留 7 天可回滚。")
    if kind == "INGEST_FAILED":
        return to, f"[知识库] 文档入库失败：{title}", (
            f"文档：{title}（{ev['doc_key']} 版本 {ev['version']}）\n文件：{p.get('file_name')}\n任务号：{p.get('job_id')}\n"
            f"失败阶段：{p.get('stage')}\n原因：{p.get('message')}\n说明：{p.get('why')}。线上仍是这份文档之前的版本（如果有）。"
            + ("\n已登记补偿任务清理这次导入留下的暂存数据。" if p.get("compensation") else ""))
    if kind == "DRAWING_MISMATCH":
        return to, f"[知识库] 图纸与 ERP 数据不一致：{title}", (
            f"文档 {ev['doc_key']} 版本 {ev['version']} 已入库，但交叉校验发现 {len(p.get('issues', []))} 处不一致：\n- " + "\n- ".join(p.get("issues", [])))
    if kind == "DRAWING_REVIEW":
        return to, f"[知识库] 图纸信息不完整，需要复核：{title}", (
            f"图纸 {ev['doc_key']} 版本 {ev['version']} 缺少：{'、'.join(p.get('missing', []))}。\n"
            f"已渲染成图片{'并交给视觉模型复核：' + p['vl_note'] if p.get('vl_note') else '，等待人工复核'}。")
    if kind == "RECONCILE_BROKEN":
        return to, f"[知识库] 对账发现不一致：{title}", (
            f"文档 {ev['doc_key']} 版本 {ev['version']}：SQL {p.get('sql')} 个切片，向量库 {p.get('milvus')} 个，内容哈希不一致 {p.get('hash_mismatch')} 个，"
            f"状态不一致 {p.get('state_mismatch', 0)} 个。自动修复没有成功，请处理。")
    if kind == "COMPENSATION_FAILED":
        return to, f"[知识库] 补偿任务失败：{title}", (
            f"文档 {ev['doc_key']} 版本 {ev['version']} 的补偿任务（{p.get('task')}）重试 {p.get('attempts')} 次仍然失败：{p.get('error')}。"
            "MinIO 或 Milvus 里可能留有残留数据，请人工检查。")
    return to, f"[知识库] {kind}：{title}", json.dumps(p, ensure_ascii=False, default=str)[:2000]


def handle(msg: dict) -> None:
    """消费一条事件：写发件箱（去重）→ 发送 → 标记完成。发件箱里已经有了说明是重复投递，直接确认"""
    ev = store.one("SELECT * FROM kb_event WHERE event_id = %s", (msg["event_id"],))
    if not ev or ev["status"] == "DONE":
        return
    to, subject, body = render(ev)
    notify.enqueue(f"ev:{ev['event_id']}", to, subject, body)
    try:
        notify.send_pending(limit=5)
    except Exception as e:                                     # SMTP 出问题不影响事件确认：邮件留在发件箱，发送器会补发
        logger.warning(f"[kbase] 事件 {ev['event_id'][:8]} 的邮件暂时没发出去：{e}")
    store.execute("UPDATE kb_event SET status = 'DONE', handled_at = NOW() WHERE event_id = %s", (ev["event_id"],))


# ---------------------------------------------------------------- 转发

def relay_once(limit: int = 50) -> dict:
    from app.platform import mq
    rows = store.query("SELECT event_id, kind, created_at FROM kb_event WHERE status = 'NEW' ORDER BY created_at LIMIT %s", (limit,))
    out = {"published": 0, "local": 0, "failed": 0}
    for r in rows:
        try:
            mq.publish(settings.kbase_events_queue, {"event_id": r["event_id"], "kind": r["kind"]})
            store.execute("UPDATE kb_event SET status = 'PUBLISHED', published_at = NOW(), tries = tries + 1 WHERE event_id = %s AND status = 'NEW'", (r["event_id"],))
            out["published"] += 1
        except Exception as e:
            store.execute("UPDATE kb_event SET tries = tries + 1, last_error = %s WHERE event_id = %s", (str(e)[:300], r["event_id"]))
            if r["created_at"] < datetime.now() - timedelta(seconds=LOCAL_AFTER):   # 队列挂了太久：降级成本进程直接处理
                handle({"event_id": r["event_id"]})
                out["local"] += 1
            else:
                out["failed"] += 1
    return out


_started = False


def start(interval: int = 3) -> None:
    """启动转发线程和消费线程（知识库服务启动时调用）"""
    global _started
    if _started:
        return
    _started = True
    from app.platform import mq

    def loop():
        while True:
            try:
                relay_once()
            except Exception:
                logger.exception("[kbase] 事件转发异常")
            time.sleep(interval)
    threading.Thread(target=loop, daemon=True, name="kb-event-relay").start()
    threading.Thread(target=mq.consume, args=(settings.kbase_events_queue, handle), kwargs={"name": "kb-event-mail"}, daemon=True,
                     name="kb-event-mail").start()
