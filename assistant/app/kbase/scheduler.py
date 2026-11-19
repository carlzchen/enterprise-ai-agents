"""
定时任务：进程内调度器（APScheduler）+ Redis 锁（多实例部署时同一时刻只有一个实例执行）。
每个任务可以在页面上启用、停用、手动触发；每次运行记一条日志。任务失败会进告警中心，下次成功自动恢复。
"""
import time
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.infra.config.settings import settings
from app.kbase import store
from app.platform import alerts
from app.shared.runtime.logger import logger

_DDL = ["""CREATE TABLE IF NOT EXISTS kb_schedule (
  name VARCHAR(40) PRIMARY KEY, title VARCHAR(80) NOT NULL, plan VARCHAR(80) NOT NULL, enabled TINYINT NOT NULL DEFAULT 1,
  last_run_at DATETIME, last_status VARCHAR(10), last_message VARCHAR(500), last_ms INT, next_run_at DATETIME
) COMMENT '定时任务'""", """CREATE TABLE IF NOT EXISTS kb_schedule_run (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(40) NOT NULL, started_at DATETIME NOT NULL, ms INT, status VARCHAR(10), message VARCHAR(500),
  trigger_by VARCHAR(40), KEY idx_name (name, id)
) COMMENT '定时任务运行记录'"""]
_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------- 各个任务（返回一句话结果；抛异常算失败）

def _eas_sync() -> str:
    from app.kbase.kingdee import sync
    r = sync.sync_all()
    return f"物料 {r['materials']} 条，BOM {r['bom_lines']} 行"


def _inbox_scan() -> str:
    from app.kbase.ingest import batchjob
    r = batchjob.scan_inbox()
    return r.get("message") or f"扫描 {r['scanned']} 个，没变化 {r['unchanged']} 个，新批次 {r['batch_id'] or '无'}（{r['total']} 个文件）"


def _plm_sync() -> str:
    from app.kbase.ingest import batchjob
    r = batchjob.from_plm()
    return r.get("message") or f"新批次 {r['batch_id']}（{r['total']} 个文件）"


def _reconcile() -> str:
    from app.kbase.ingest import versioning
    r = versioning.reconcile_all()
    if r["broken"]:
        raise RuntimeError(f"{len(r['broken'])} 份文档对账后仍不一致：" + "、".join(b["doc_key"] for b in r["broken"][:5]))
    return (f"{r['documents']} 份文档，一致 {r['consistent']}，自动修复 {r['repaired']}，清理旧版本 {r.get('purged', 0)}，"
            f"登记孤儿暂存清理 {r.get('orphans', 0)}，执行补偿任务 {r.get('compensations', 0)}")


def _compensate() -> str:
    from app.kbase.ingest import versioning
    r = versioning.run_compensations()
    failed = [x for x in r if not x["ok"]]
    return f"执行 {len(r)} 个补偿任务，失败 {len(failed)}" if r else "没有到期的补偿任务"


def _directory_reconcile() -> str:
    from app.kbase.auth import principal
    r = principal.reconcile_all()
    return f"{r['users']} 人，权限变化 {len(r['changed'])}，吊销离职或禁用 {len(r['removed'])}，没匹配到规则 {len(r['no_rule'])}"


def _kg_rebuild() -> str:
    from app.kbase import kg
    r = kg.rebuild()
    return f"{r['nodes']} 个节点，{r['edges']} 条关系"


def _health() -> str:
    from app.platform import health
    return health.check_all()


def _badcase_weekly() -> str:
    since = datetime.now() - timedelta(days=7)
    rows = store.query("SELECT COALESCE(NULLIF(f.reason, ''), '未填') reason, COUNT(*) n FROM kb_feedback f WHERE f.rating < 0 AND f.created_at >= %s GROUP BY 1 ORDER BY n DESC", (since,))
    total = sum(r["n"] for r in rows)
    answers = store.one("SELECT COUNT(*) n FROM kb_answer_log WHERE created_at >= %s", (since,))["n"]
    text = f"近 7 天回答 {answers} 次，点踩 {total} 次。" + ("按原因：" + "，".join(f"{r['reason']} {r['n']}" for r in rows) if rows else "")
    if total:
        top = store.query("SELECT a.question FROM kb_feedback f JOIN kb_answer_log a ON a.answer_id = f.answer_id WHERE f.rating < 0 AND f.created_at >= %s ORDER BY f.id DESC LIMIT 5", (since,))
        alerts.send_feishu("知识库 Bad Case 周报", text + "\n\n最近的问题：\n" + "\n".join(f"- {r['question'][:60]}" for r in top), "P3")
    return text


JOBS = {
    "eas_sync": ("同步金蝶 EAS 物料和 BOM", "每天 02:00", CronTrigger(hour=2, minute=0), _eas_sync, "P2"),
    "inbox_scan": ("扫描共享目录增量入库", "每天 02:30", CronTrigger(hour=2, minute=30), _inbox_scan, "P2"),
    "plm_sync": ("同步 PLM 现行文档", "每天 02:40", CronTrigger(hour=2, minute=40), _plm_sync, "P2"),
    "reconcile": ("全库对账并清理旧版本", "每天 03:30", CronTrigger(hour=3, minute=30), _reconcile, "P1"),
    "kg_rebuild": ("重建知识图谱", "每天 04:00", CronTrigger(hour=4, minute=0), _kg_rebuild, "P3"),
    "compensate": ("执行到期的补偿任务（清理中断导入的残留）", "每 1 分钟", IntervalTrigger(minutes=1), _compensate, "P2"),
    "directory_reconcile": ("公司目录全量对账，刷新权限缓存、吊销离职人员", "每天 03:00", CronTrigger(hour=3, minute=0), _directory_reconcile, "P2"),
    "health": ("依赖健康检查和告警规则", "每 1 分钟", IntervalTrigger(minutes=1), _health, "P2"),
    "badcase_weekly": ("Bad Case 周报", "每周一 09:00", CronTrigger(day_of_week="mon", hour=9, minute=0), _badcase_weekly, "P3"),
}


# ---------------------------------------------------------------- 调度

def _lock(name: str, seconds: int) -> bool:
    try:
        import redis
        return bool(redis.Redis.from_url(settings.redis_url, socket_timeout=2).set(f"kb:sched:{name}", "1", nx=True, ex=seconds))
    except Exception:
        return True                                          # Redis 不可用时不拦：单实例部署下照常执行


def run(name: str, trigger_by: str = "定时") -> dict:
    title, _, _, fn, level = JOBS[name]
    row = store.one("SELECT enabled FROM kb_schedule WHERE name = %s", (name,))
    if trigger_by == "定时" and (not row or not row["enabled"] or not _lock(name, 50)):
        return {"name": name, "status": "SKIP"}
    started, t = datetime.now().replace(microsecond=0), time.time()
    try:
        message, status = str(fn())[:500], "OK"
        alerts.resolve(f"kbase:schedule:{name}", f"定时任务「{title}」已恢复正常")
    except Exception as e:
        message, status = f"{type(e).__name__}: {e}"[:500], "FAILED"
        logger.exception(f"[schedule] {title} 失败")
        alerts.fire("kbase", f"schedule_{name}", level, f"定时任务失败：{title}", message, f"kbase:schedule:{name}")
    ms = int((time.time() - t) * 1000)
    nxt = _scheduler.get_job(name).next_run_time.replace(tzinfo=None) if _scheduler and _scheduler.get_job(name) else None
    store.execute("UPDATE kb_schedule SET last_run_at = %s, last_status = %s, last_message = %s, last_ms = %s, next_run_at = %s WHERE name = %s",
                  (started, status, message, ms, nxt, name))
    if name != "health" or status != "OK" or trigger_by != "定时":       # 健康检查每分钟一次，正常时不记流水
        store.execute("INSERT INTO kb_schedule_run (name, started_at, ms, status, message, trigger_by) VALUES (%s,%s,%s,%s,%s,%s)", (name, started, ms, status, message, trigger_by))
    return {"name": name, "status": status, "message": message, "ms": ms}


def start() -> None:
    global _scheduler
    if _scheduler:
        return
    for ddl in _DDL:
        store.execute(ddl)
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300})
    for name, (title, plan, trigger, _, _) in JOBS.items():
        store.execute("INSERT INTO kb_schedule (name, title, plan) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE title = VALUES(title), plan = VALUES(plan)", (name, title, plan))
        _scheduler.add_job(run, trigger, args=[name], id=name)
    _scheduler.start()
    for name in JOBS:
        store.execute("UPDATE kb_schedule SET next_run_at = %s WHERE name = %s", (_scheduler.get_job(name).next_run_time.replace(tzinfo=None), name))
    logger.info(f"[schedule] 调度器已启动，{len(JOBS)} 个任务")


def listing() -> list[dict]:
    return store.query("SELECT * FROM kb_schedule ORDER BY FIELD(name, 'health', 'eas_sync', 'inbox_scan', 'plm_sync', 'reconcile', 'kg_rebuild', 'badcase_weekly')")


def runs(name: str | None = None, limit: int = 50) -> list[dict]:
    if name:
        return store.query("SELECT * FROM kb_schedule_run WHERE name = %s ORDER BY id DESC LIMIT %s", (name, limit))
    return store.query("SELECT r.*, s.title FROM kb_schedule_run r JOIN kb_schedule s ON s.name = r.name ORDER BY r.id DESC LIMIT %s", (limit,))


def toggle(name: str, enabled: bool) -> None:
    store.execute("UPDATE kb_schedule SET enabled = %s WHERE name = %s", (1 if enabled else 0, name))
