"""
批量入库：一批文件 = 一个批次（kb_batch），每个文件 = 一个任务（kb_ingest_job）= 消息队列里的一条消息。

  来源      共享目录扫描（增量）/ PLM 同步 / 上传压缩包
  执行      消费线程从 kb.ingest 队列领任务，跑完整的入库流程（和单文件上传是同一套流程）
  失败      单个文件失败不影响整批。临时故障进延迟队列（30 秒、2 分钟、10 分钟）重试 3 次；
            重试用完或属于「重试也没用」的问题 → 跳过，进死信队列，等人处理
  熔断      一批里失败比例超过阈值（默认 20%）→ 整批暂停并告警，避免一个坏批次把问题刷满
  通知      批次结束发一封汇总（成功 / 跳过 / 失败各多少，失败的逐条列原因），不逐个文件发
  降级      消息队列连不上 → 这一批改成进程内顺序执行，并告警；功能不中断，只是慢
  幂等      消息可能重复投递：任务已经是终态就直接确认；同一文件同一版本重复入库会被跳过
"""
import csv
import re
import threading
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from app.infra.config.settings import settings
from app.kbase import store
from app.kbase.ingest import clean, graph, notify
from app.kbase.ingest.batch import POLICY, meta_for
from app.platform import alerts, mq
from app.shared.runtime.logger import PROJECT_ROOT, logger

QUEUE = "kb.ingest"
TERMINAL = ("DONE", "SKIPPED", "FAILED", "CANCELLED")
MIN_FOR_BREAKER = 10                    # 至少处理完这么多个才判断失败比例，避免头两个失败就把整批停掉
_NAME = re.compile(r"^(?P<key>[^_].*?)__v(?P<ver>[^_]+)__(?P<title>.+)$")
_stop = threading.Event()
_started = False


# ---------------------------------------------------------------- 建批次

def create(source: str, items: list[dict], trigger_by: str, title: str = "") -> dict:
    """items: [{file_path, doc_key, version, meta}]。返回批次信息"""
    if not items:
        return {"batch_id": None, "total": 0, "message": "没有需要入库的文件"}
    batch_id = "B" + datetime.now().strftime("%y%m%d%H%M%S") + uuid.uuid4().hex[:4]
    use_mq = mq.available()
    store.execute("INSERT INTO kb_batch (batch_id, source, title, trigger_by, status, total, pending, mode) VALUES (%s,%s,%s,%s,'RUNNING',%s,%s,%s)",
                  (batch_id, source, title or f"{source} {datetime.now():%m-%d %H:%M}", trigger_by, len(items), len(items), "MQ" if use_mq else "LOCAL"))
    jobs = [graph.submit(i["file_path"], i["doc_key"], str(i["version"]), i["meta"], batch_id) for i in items]
    if use_mq:
        for j in jobs:
            mq.publish(QUEUE, {"job_id": j, "batch_id": batch_id})
        alerts.resolve("platform:mq_down", "消息队列已恢复")
    else:
        alerts.fire("platform", "mq_down", "P2", "消息队列连不上，批量入库改为进程内执行", f"批次 {batch_id}（{len(items)} 个文件）已降级为顺序执行，速度会慢，功能不受影响。", "platform:mq_down")
        threading.Thread(target=lambda: [handle({"job_id": j, "batch_id": batch_id}, local=True) for j in jobs], daemon=True, name="kb-batch-local").start()
    logger.info(f"[batch] 新批次 {batch_id}：{source}，{len(items)} 个文件，{'消息队列' if use_mq else '进程内'}执行")
    return {"batch_id": batch_id, "total": len(items), "mode": "MQ" if use_mq else "LOCAL"}


# ---------------------------------------------------------------- 消费

def handle(msg: dict, local: bool = False) -> None:
    job = store.one("SELECT job_id, status, batch_id, attempts FROM kb_ingest_job WHERE job_id = %s", (msg.get("job_id"),))
    if not job or job["status"] in TERMINAL:                 # 重复投递或任务已被取消
        return
    batch = store.one("SELECT status FROM kb_batch WHERE batch_id = %s", (job["batch_id"],)) if job["batch_id"] else None
    if batch and batch["status"] == "CANCELLED":
        store.execute("UPDATE kb_ingest_job SET status = 'CANCELLED', finished_at = NOW(), error = '批次已取消' WHERE job_id = %s", (job["job_id"],))
        return refresh(job["batch_id"])
    if batch and batch["status"] == "PAUSED":                # 批次暂停：消息先放回延迟队列，恢复后自然会再来
        if local:
            return
        return mq.publish(QUEUE, msg, delay=mq.DELAYS[0])
    out = graph.run(job["job_id"])
    if out["status"] == "RETRY":
        if local:
            _local_retry(msg)
        else:
            mq.publish(QUEUE, msg, delay=graph.BACKOFF[min(out["attempts"], len(graph.BACKOFF)) - 1])
    elif out["status"] == "FAILED" and not local:
        try:
            mq.publish(QUEUE, {**msg, "error": out["error"], "stage": out["stage"], "file": out["file_name"]}, dead=True)
        except Exception:
            logger.warning(f"[batch] {job['job_id']} 进死信队列失败（任务状态已经记在数据库里，不影响结果）")
    if job["batch_id"]:
        refresh(job["batch_id"])


def _local_retry(msg: dict) -> None:
    """进程内执行时没有延迟队列：交给数据库轮询的重试线程（retry_due）到点再跑"""
    store.execute("UPDATE kb_ingest_job SET next_retry_at = DATE_SUB(next_retry_at, INTERVAL 15 MINUTE) WHERE job_id = %s", (msg["job_id"],))


def start_workers(n: int | None = None) -> None:
    global _started
    if _started:
        return
    _started = True
    for i in range(n or settings.kbase_workers):
        threading.Thread(target=mq.consume, args=(QUEUE, handle), kwargs={"name": f"kb-ingest-{i + 1}", "stop": _stop}, daemon=True, name=f"kb-ingest-{i + 1}").start()


# ---------------------------------------------------------------- 进度、熔断、收尾

def refresh(batch_id: str) -> dict | None:
    c = store.one("SELECT COUNT(*) total, SUM(status = 'DONE') done, SUM(status = 'SKIPPED') skipped, SUM(status = 'FAILED') failed, "
                  "SUM(status = 'CANCELLED') cancelled, SUM(status NOT IN ('DONE', 'SKIPPED', 'FAILED', 'CANCELLED')) pending FROM kb_ingest_job WHERE batch_id = %s", (batch_id,))
    c = {k: int(v or 0) for k, v in c.items()}
    b = store.one("SELECT * FROM kb_batch WHERE batch_id = %s", (batch_id,))
    if not b:
        return None
    store.execute("UPDATE kb_batch SET done = %s, skipped = %s, failed = %s, pending = %s WHERE batch_id = %s", (c["done"], c["skipped"], c["failed"], c["pending"], batch_id))
    finished = c["done"] + c["skipped"] + c["failed"]
    if b["status"] == "RUNNING" and c["pending"] and finished >= MIN_FOR_BREAKER and c["failed"] / finished > settings.kbase_batch_fail_ratio:
        reason = f"已处理 {finished} 个，失败 {c['failed']} 个（{c['failed'] / finished:.0%}），超过 {settings.kbase_batch_fail_ratio:.0%} 的上限"
        if store.execute("UPDATE kb_batch SET status = 'PAUSED', pause_reason = %s WHERE batch_id = %s AND status = 'RUNNING'", (reason, batch_id)):
            alerts.fire("kbase", "batch_fail_ratio", "P1", f"批量入库已自动暂停：{b['title']}", f"批次 {batch_id}：{reason}。\n最近的失败原因：\n" + _reasons(batch_id, 5) +
                        "\n请到「资料与入库管理 → 批量入库」查看，确认后可以继续或取消。", f"kbase:batch:{batch_id}")
    elif not c["pending"] and b["status"] in ("RUNNING", "PAUSED", "CANCELLED") and not b["finished_at"]:
        status = "CANCELLED" if b["status"] == "CANCELLED" else "PARTIAL" if c["failed"] else "DONE"
        if store.execute("UPDATE kb_batch SET status = %s, finished_at = NOW() WHERE batch_id = %s AND finished_at IS NULL", (status, batch_id)):
            _summary(batch_id, b, c, status)
    return c


def _reasons(batch_id: str, limit: int = 20) -> str:
    rows = store.query("SELECT file_name, stage, error FROM kb_ingest_job WHERE batch_id = %s AND status = 'FAILED' ORDER BY finished_at DESC LIMIT %s", (batch_id, limit))
    return "\n".join(f"- {r['file_name']}（{graph.STAGE_CN.get(r['stage'], r['stage'])}）：{(r['error'] or '')[:150]}" for r in rows) or "（无）"


def _summary(batch_id: str, b: dict, c: dict, status: str) -> None:
    """批次结束：一封汇总邮件；有失败的同时推飞书"""
    line = f"共 {c['total']} 个文件：成功 {c['done']}，跳过 {c['skipped']}（内容没有变化），失败 {c['failed']}" + (f"，取消 {c['cancelled']}" if c["cancelled"] else "")
    body = f"批次：{b['title']}（{batch_id}，来源 {b['source']}，由 {b['trigger_by']} 触发）\n{line}\n"
    if c["failed"]:
        body += "\n失败的文件（已重试过或属于重试无效的问题，线上仍是它们之前的版本）：\n" + _reasons(batch_id)
    notify.enqueue(f"batch:{batch_id}", None, f"[知识库] 批量入库{'完成' if status == 'DONE' else '结束，有失败项' if status == 'PARTIAL' else '已取消'}：{b['title']}", body)
    if c["failed"]:
        alerts.fire("kbase", "batch_failed_items", "P2", f"批量入库有 {c['failed']} 个文件失败：{b['title']}", body, f"kbase:batch_items:{batch_id}")
    alerts.resolve(f"kbase:batch:{batch_id}", "批次已结束")
    logger.info(f"[batch] 批次 {batch_id} 结束（{status}）：{line}")


# ---------------------------------------------------------------- 人工操作

def pause(batch_id: str, who: str) -> bool:
    return bool(store.execute("UPDATE kb_batch SET status = 'PAUSED', pause_reason = %s WHERE batch_id = %s AND status = 'RUNNING'", (f"{who} 手动暂停", batch_id)))


def resume(batch_id: str) -> bool:
    ok = bool(store.execute("UPDATE kb_batch SET status = 'RUNNING', pause_reason = NULL WHERE batch_id = %s AND status = 'PAUSED'", (batch_id,)))
    if ok:
        alerts.resolve(f"kbase:batch:{batch_id}", "批次已由人工确认继续")
    return ok


def cancel(batch_id: str) -> bool:
    ok = bool(store.execute("UPDATE kb_batch SET status = 'CANCELLED' WHERE batch_id = %s AND status IN ('RUNNING', 'PAUSED')", (batch_id,)))
    if ok:                                                    # 还没开始的任务直接标记取消；正在跑的那个会跑完
        store.execute("UPDATE kb_ingest_job SET status = 'CANCELLED', finished_at = NOW(), error = '批次已取消' WHERE batch_id = %s AND status IN ('QUEUED', 'RETRY')", (batch_id,))
        refresh(batch_id)
    return ok


def retry_failed(batch_id: str) -> int:
    """把这一批里失败的任务重新排队（从死信队列里拿掉对应的消息）"""
    rows = store.query("SELECT job_id FROM kb_ingest_job WHERE batch_id = %s AND status = 'FAILED'", (batch_id,))
    if not rows:
        return 0
    ids = {r["job_id"] for r in rows}
    store.execute("UPDATE kb_ingest_job SET status = 'QUEUED', attempts = 0, error = NULL, finished_at = NULL, next_retry_at = NULL WHERE job_id IN %s", (list(ids),))
    store.execute("UPDATE kb_batch SET status = 'RUNNING', finished_at = NULL, pause_reason = NULL WHERE batch_id = %s", (batch_id,))
    alerts.resolve(f"kbase:batch_items:{batch_id}", "失败项已重新排队")
    if mq.available():
        for m in mq.purge_dead(QUEUE):
            if m.get("job_id") not in ids:
                mq.publish(QUEUE, m, dead=True)
        for j in ids:
            mq.publish(QUEUE, {"job_id": j, "batch_id": batch_id})
    else:
        threading.Thread(target=lambda: [handle({"job_id": j, "batch_id": batch_id}, local=True) for j in ids], daemon=True).start()
    refresh(batch_id)
    return len(ids)


def listing(limit: int = 50) -> list[dict]:
    return store.query("SELECT * FROM kb_batch ORDER BY started_at DESC LIMIT %s", (limit,))


def detail(batch_id: str) -> dict | None:
    b = store.one("SELECT * FROM kb_batch WHERE batch_id = %s", (batch_id,))
    if not b:
        return None
    b["jobs"] = store.query("SELECT job_id, doc_key, version, file_name, stage, status, attempts, next_retry_at, error, started_at, finished_at FROM kb_ingest_job "
                            "WHERE batch_id = %s ORDER BY FIELD(status, 'FAILED', 'RETRY', 'RUNNING', 'QUEUED', 'DONE', 'SKIPPED', 'CANCELLED'), started_at", (batch_id,))
    return b


# ---------------------------------------------------------------- 三种来源

def _unchanged(doc_key: str, version: str, path: Path) -> bool:
    """这个文件的这个版本已经生效且内容没变：不用再排队"""
    row = store.one("SELECT file_sha256 FROM kb_doc_version WHERE doc_key = %s AND version = %s AND status IN ('ACTIVE', 'SUPERSEDED')", (doc_key, str(version)))
    return bool(row) and row["file_sha256"] == clean.sha256(path)


def _from_folder(root: Path, source: str) -> tuple[list[dict], list[str]]:
    """
    目录约定：<根目录>/<文档类型>/<文档编号>__v<版本>__<标题>.<扩展名>，文档类型决定默认的密级和可见范围。
    根目录下有 manifest.csv 时以它为准（列：file, doc_key, version, title, doc_type, security_level, acl_groups, owner_email, product_code）。
    返回（要入库的条目, 不符合约定被忽略的文件）
    """
    items, ignored = [], []
    manifest = root / "manifest.csv"
    if manifest.exists():
        with manifest.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                path = root / (r.get("file") or "").strip()
                kind = (r.get("doc_type") or "").strip().upper()
                if not path.is_file() or kind not in POLICY or not r.get("doc_key") or not r.get("version"):
                    ignored.append(f"{r.get('file')}（清单里这一行不完整或文件不存在）")
                    continue
                meta = meta_for((r.get("title") or path.stem).strip(), kind, source, product_code=(r.get("product_code") or "").strip() or None)
                for k in ("security_level", "acl_groups", "owner_email"):
                    if (r.get(k) or "").strip():
                        meta[k] = r[k].strip().upper() if k != "owner_email" else r[k].strip()
                items.append({"file_path": str(path), "doc_key": r["doc_key"].strip(), "version": r["version"].strip(), "meta": meta})
        return items, ignored
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
        kind, m = path.parent.name.upper(), _NAME.match(path.stem)
        if kind not in POLICY or not m:
            ignored.append(f"{path.relative_to(root)}（目录名不是文档类型，或文件名不是「编号__v版本__标题」的格式）")
            continue
        items.append({"file_path": str(path), "doc_key": m["key"], "version": m["ver"], "meta": meta_for(m["title"], kind, source)})
    return items, ignored


def scan_inbox(trigger_by: str = "定时任务") -> dict:
    """扫描共享目录，只把新增和有变化的文件排进一个批次"""
    root = Path(settings.kbase_inbox)
    root = root if root.is_absolute() else PROJECT_ROOT / root
    if not root.exists():
        return {"batch_id": None, "total": 0, "message": f"共享目录 {root} 不存在"}
    items, ignored = _from_folder(root, "FOLDER")
    fresh = [i for i in items if not _unchanged(i["doc_key"], i["version"], Path(i["file_path"]))]
    out = create("INBOX", fresh, trigger_by, f"共享目录增量 {datetime.now():%m-%d %H:%M}")
    out.update(scanned=len(items), unchanged=len(items) - len(fresh), ignored=ignored)
    if ignored:
        alerts.fire("kbase", "inbox_ignored", "P3", f"共享目录里有 {len(ignored)} 个文件不符合命名约定，没有入库", "\n".join(f"- {x}" for x in ignored[:20]), "kbase:inbox_ignored")
    return out


def from_plm(trigger_by: str = "定时任务") -> dict:
    """从 PLM 同步现行文档"""
    from app.infra.erp.minierp_client import minierp_client
    items = []
    for d in minierp_client.current_documents():
        target = PROJECT_ROOT / "output" / "plm" / d["doc_no"] / f"v{d['revision']}" / d["file_name"]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(minierp_client.download(d["id"]))
        if _unchanged(d["doc_no"], str(d["revision"]), target):
            continue
        items.append({"file_path": str(target), "doc_key": d["doc_no"], "version": str(d["revision"]),
                      "meta": meta_for(d["title"], d["doc_type"], "PLM", owner=d.get("uploaded_by") or "eng01", product_code=d.get("product_code"), series=d.get("series"))})
    return create("PLM", items, trigger_by, f"PLM 同步 {datetime.now():%m-%d %H:%M}")


def from_zip(zip_path: Path, trigger_by: str, title: str = "") -> dict:
    """上传的压缩包：解压后按和共享目录一样的约定识别"""
    target = zip_path.parent / "unzipped"
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename
            try:                                              # 压缩包里的中文文件名常常是 GBK 编码
                name = name.encode("cp437").decode("gbk") if not info.flag_bits & 0x800 else name
            except Exception:
                pass
            dest = (target / name).resolve()
            if not str(dest).startswith(str(target.resolve())) or info.is_dir() or "__MACOSX" in name:      # 防止路径穿越
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(info))
    roots = [p for p in target.iterdir() if p.is_dir()] if target.exists() else []
    root = roots[0] if len(roots) == 1 and not (target / "manifest.csv").exists() and roots[0].name.upper() not in POLICY else target
    items, ignored = _from_folder(root, "UPLOAD")
    out = create("ZIP", items, trigger_by, title or f"压缩包 {zip_path.name}")
    out["ignored"] = ignored
    return out
