"""
依赖健康检查和运行类告警规则，每分钟由定时任务执行一次。每条规则：有问题就触发告警，恢复了就自动关闭。
"""
from datetime import datetime, timedelta

import httpx

from app.infra.config.settings import settings
from app.kbase import store
from app.platform import alerts, mq

QUEUE_BACKLOG = 200            # 入库队列积压超过多少条告警
STUCK_MINUTES = 30             # 一个入库任务跑了多久算卡住
P95_SECONDS = 30               # 近 15 分钟问答 P95 超过多少秒告警
REFUSE_RATIO = 0.5             # 近 15 分钟拒答比例超过多少告警
MIN_SAMPLES = 20


def _probe(name: str, fn) -> tuple[str, bool, str]:
    try:
        fn()
        return name, True, ""
    except Exception as e:
        return name, False, f"{type(e).__name__}: {str(e)[:150]}"


def _milvus():
    from app.infra.vector_store.milvus_gateway import milvus_gateway
    try:
        milvus_gateway.client.list_collections(timeout=3)
    except Exception:
        milvus_gateway.reset()                               # 旧连接可能已经失效：重置后再试一次，仍然不行才算不可用
        milvus_gateway.client.list_collections(timeout=3)


def _redis():
    import redis
    redis.Redis.from_url(settings.redis_url, socket_timeout=2).ping()


def _neo4j():
    from app.kbase import kg
    if kg.driver() is None:
        raise RuntimeError("连不上")


def _llm():
    r = httpx.get(settings.llm_base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {settings.llm_api_key}"}, timeout=6)
    if r.status_code >= 500:
        raise RuntimeError(f"HTTP {r.status_code}")


def _eas():
    last = store.one("SELECT MAX(synced_at) t FROM kd_material")["t"]
    if not last or datetime.now() - last > timedelta(hours=36):
        raise RuntimeError(f"物料镜像上次同步是 {last}，已经超过 36 小时")


def _mq():
    if not mq.available():
        raise RuntimeError("连不上")


DEPS = {"MySQL": (lambda: store.one("SELECT 1"), "P1"), "Milvus 向量库": (_milvus, "P1"), "大模型服务": (_llm, "P1"), "Redis": (_redis, "P2"),
        "RabbitMQ 消息队列": (_mq, "P2"), "Neo4j 知识图谱": (_neo4j, "P3"), "金蝶 EAS 数据": (_eas, "P2")}
DEGRADE = {"Milvus 向量库": "问答已降级为只用结构化参数和全文检索", "大模型服务": "问答已降级为返回原文摘录", "RabbitMQ 消息队列": "批量入库已降级为进程内执行",
           "Neo4j 知识图谱": "问答跳过图谱，其余不受影响", "金蝶 EAS 数据": "继续使用上次同步的数据", "Redis": "限流和缓存暂时失效"}


def snapshot() -> list[dict]:
    """各依赖当前是否正常（页面展示用，不触发告警）"""
    return [{"name": n, "ok": ok, "error": err, "level": DEPS[n][1], "degrade": DEGRADE.get(n, "")} for n, ok, err in (_probe(n, f) for n, (f, _) in DEPS.items())]


def check_all() -> str:
    problems = []
    for item in snapshot():
        key = f"platform:dep:{item['name']}"
        if item["ok"]:
            alerts.resolve(key, f"{item['name']} 已恢复")
        else:
            problems.append(item["name"])
            alerts.fire("platform", "dependency_down", item["level"], f"{item['name']} 不可用", f"{item['error']}\n影响：{item['degrade']}", key)

    def rule(key: str, bad: bool, level: str, title: str, detail: str):
        if bad:
            problems.append(title)
            alerts.fire("kbase", key, level, title, detail, f"kbase:{key}")
        else:
            alerts.resolve(f"kbase:{key}")

    q = mq.depth("kb.ingest")
    rule("queue_backlog", q.get("ready", 0) > QUEUE_BACKLOG, "P2", f"入库队列积压 {q.get('ready', 0)} 条", f"超过 {QUEUE_BACKLOG} 条。可以调大 KBASE_WORKERS 或检查消费线程是否正常。")
    stuck = store.query("SELECT job_id, file_name, stage FROM kb_ingest_job WHERE status = 'RUNNING' AND started_at < %s LIMIT 10", (datetime.now() - timedelta(minutes=STUCK_MINUTES),))
    rule("job_stuck", bool(stuck), "P2", f"{len(stuck)} 个入库任务运行超过 {STUCK_MINUTES} 分钟", "\n".join(f"- {s['job_id']} {s['file_name']}（停在 {s['stage']}）" for s in stuck))
    mails = store.one("SELECT COUNT(*) n FROM kb_outbox WHERE status = 'FAILED'")["n"]
    rule("mail_failed", mails > 0, "P3", f"{mails} 封通知邮件发送失败", "请检查邮件服务器配置（SMTP_HOST 等）。")
    since = datetime.now() - timedelta(minutes=15)
    rows = store.query("SELECT status, CAST(JSON_EXTRACT(latency, '$.total') AS UNSIGNED) ms FROM kb_answer_log WHERE created_at >= %s", (since,))
    if len(rows) >= MIN_SAMPLES:
        ms = sorted(r["ms"] or 0 for r in rows)
        p95 = ms[int(0.95 * (len(ms) - 1))] / 1000
        refused = sum(r["status"] in ("EMPTY", "LOW") for r in rows) / len(rows)
        rule("slow", p95 > P95_SECONDS, "P2", f"知识库问答变慢：近 15 分钟 P95 为 {p95:.1f} 秒", f"共 {len(rows)} 次回答，阈值 {P95_SECONDS} 秒。")
        rule("refuse_spike", refused > REFUSE_RATIO, "P2", f"知识库拒答比例偏高：{refused:.0%}", f"近 15 分钟 {len(rows)} 次回答里有 {refused:.0%} 没有作答，可能是资料缺失或检索异常。")
    try:
        from app.platform import resilience
        for b in resilience.breakers():
            rule(f"breaker_{b['name']}", b["state"] == "OPEN", "P2", f"熔断器已打开：{b['title']}", f"连续失败 {b['failures']} 次，{b['reset_seconds']} 秒后放一个请求试探。期间走降级路径。")
    except ImportError:
        pass
    return "一切正常" if not problems else "发现问题：" + "；".join(problems)
