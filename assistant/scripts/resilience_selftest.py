"""
高并发保护和降级的故障注入自测：逐个把依赖「弄坏」，检查系统是不是按设计降级，而不是报错或卡死。
用法（知识库服务和问数服务保持运行）：KBASE_EVAL_REMOTE=1 PYTHONPATH=. .venv/bin/python scripts/resilience_selftest.py
弄坏的方式是在本进程里把对应的函数换成会抛错的版本，不会动真实的数据库、向量库和模型服务。
"""
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx  # noqa: E402

from app.infra.config.settings import settings  # noqa: E402
from app.kbase import store, vectors  # noqa: E402
from app.platform import alerts, mq  # noqa: E402
from app.platform import resilience as R  # noqa: E402

results: list[tuple[str, bool]] = []
MOCK = "http://127.0.0.1:8096"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"　{detail}" if detail else ""))


def boom(*a, **k):
    raise ConnectionError("模拟故障")


def feishu() -> list[dict]:
    try:
        return httpx.get(f"{MOCK}/feishu/messages", timeout=5, trust_env=False).json()
    except Exception:
        return []


def wait(cond, seconds: float = 90, gap: float = 1.0) -> bool:
    t = time.time()
    while time.time() - t < seconds:
        if cond():
            return True
        time.sleep(gap)
    return False


def kbase_tests() -> None:
    from app.copilot import llm
    from app.kbase import kg
    from app.kbase.ingest import versioning
    from app.kbase.query import graph, retrieve
    ask = lambda q, **kw: graph.ask(q, "selftest", "ADMIN", options={"no_cache": True}, **kw)      # 故障测试不走回答缓存，否则测不到故障
    Q = "伺服电机过热报警怎么排查"

    print("一、知识库问答：依赖出故障时的表现")
    base = ask(Q)
    check("正常情况能回答并带出处", base["status"] == "OK" and base["sources"] and not base["degraded"], f"{base['elapsed_ms'] / 1000:.1f} 秒")

    real_search, calls = vectors.single_search, {"n": 0}

    def dead_milvus(*a, **k):
        calls["n"] += 1
        raise ConnectionError("模拟：向量库连不上")
    vectors.single_search = dead_milvus
    r = ask(Q)
    check("向量库不可用 → 改用关键词检索，仍然作答", r["status"] in ("OK", "LOW", "EMPTY") and any("关键词" in d for d in r["degraded"]), f"状态 {r['status']}，来源 {len(r['sources'])} 条")
    ask(Q); ask(Q)
    n = calls["n"]
    r = ask(Q)
    check("连续失败后熔断器打开，不再去碰向量库", retrieve.MILVUS.state == "OPEN" and calls["n"] == n and any("关键词" in d for d in r["degraded"]), f"熔断后调用次数没有增加（{n}）")
    vectors.single_search = real_search
    retrieve.MILVUS.opened_at = 0                             # 把「等 30 秒」快进掉
    r = ask(Q)
    check("向量库恢复后自动回到正常路径", retrieve.MILVUS.state == "CLOSED" and not r["degraded"] and r["status"] == "OK")

    pool = retrieve.RERANK_POOL
    retrieve.RERANK_POOL = R.Bulkhead("rerank_test", "重排模型", limit=1, queue=0, wait_seconds=0.1)
    r = ask(Q)
    retrieve.RERANK_POOL = pool
    check("重排排不上队 → 跳过精排，直接用初步检索结果作答", r["status"] in ("OK", "EMPTY") and any("精排" in d for d in r["degraded"]) and r["sources"],
          f"状态 {r['status']}（没有精排时检索质量会下降，答不上来就如实说没找到）")

    real_chat = llm.chat
    llm.chat = boom
    r = ask(Q)
    check("大模型不可用 → 返回原文摘录，不编答案", r["status"] == "DEGRADED" and any("原文摘录" in d for d in r["degraded"]) and "摘录" in r["answer"] or "《" in r["answer"])
    p = ask("KL-SV-60-200W 的额定转矩是多少")
    check("大模型不可用时参数题同样降级为摘录（数值来自数据库，仍然准确）", p["status"] == "DEGRADED" and "0.637" in p["answer"])
    ask(Q)
    check("连续失败后大模型熔断器打开", llm.BREAKER.state == "OPEN")
    object.__setattr__(settings, "llm_fallback_base_url", settings.llm_base_url)
    object.__setattr__(settings, "llm_fallback_api_key", settings.llm_api_key)
    object.__setattr__(settings, "llm_fallback_model", settings.llm_model)
    llm.chat = lambda json_mode=False, fallback=False: real_chat(json_mode, True) if fallback else boom()
    r = ask(Q)
    check("配置了备用模型 → 主模型熔断时自动切过去，回答正常", r["status"] == "OK" and r["sources"], "（自测里备用模型指向同一个服务，只验证切换逻辑）")
    object.__setattr__(settings, "llm_fallback_model", "")
    llm.chat = real_chat
    llm.BREAKER.opened_at = 0
    check("大模型恢复后自动回到正常路径", ask(Q)["status"] == "OK" and llm.BREAKER.state == "CLOSED")

    real_driver = kg.driver
    kg.driver = lambda: None
    r = ask("KL-SV-80-400W 用了哪些物料")
    kg.driver = real_driver
    check("知识图谱不可用 → 跳过图谱，其余照常", r["status"] == "OK")

    real_redis = R.redis_client
    R.redis_client = lambda: None
    r = ask(Q)
    R.redis_client = real_redis
    check("Redis 不可用 → 限流和缓存失效，问答照常", r["status"] == "OK")

    print("二、知识库问答：限流、过载保护、缓存")
    gate = R.Gate("selftest", per_minute=3, concurrent=1)
    user, hits = "u" + uuid.uuid4().hex[:6], []
    for _ in range(4):
        try:
            with gate.enter(user):
                hits.append("ok")
        except R.TooBusy as e:
            hits.append(f"拒绝（{e.retry_after} 秒后再试）")
    check("同一个人一分钟内超过上限会被限流", hits[:3] == ["ok"] * 3 and hits[3].startswith("拒绝"), hits[3])
    out = []

    def one(u):
        try:
            with gate.enter(u):
                time.sleep(0.5)
                out.append("ok")
        except R.TooBusy:
            out.append("busy")
    ts = [threading.Thread(target=one, args=(f"c{i}{uuid.uuid4().hex[:4]}",)) for i in range(3)]
    [t.start() or time.sleep(0.05) for t in ts]
    [t.join() for t in ts]
    check("同时进来的请求超过并发上限，多出来的立刻被告知稍后再试", sorted(out) == ["busy", "busy", "ok"], str(out))

    q = f"KL-SV-60-200W 的额定转速是多少（自测 {uuid.uuid4().hex[:4]}）"
    a = graph.ask(q, "selftest", "ADMIN")
    b = graph.ask(q, "selftest", "ADMIN")
    check("同样的问题第二次直接走缓存", a["status"] == "OK" and b["cache_hit"] and b["answer"] == a["answer"] and b["elapsed_ms"] < a["elapsed_ms"], f"{a['elapsed_ms']} 毫秒 → {b['elapsed_ms']} 毫秒")
    versioning._bump()
    c = graph.ask(q, "selftest", "ADMIN")
    check("知识库内容更新后旧缓存自动失效", not c["cache_hit"])
    store.execute("DELETE FROM kb_answer_log WHERE username = 'selftest'")
    graph._messages().delete_many({"username": "selftest"})


def copilot_tests() -> None:
    from app.copilot import db, llm, pipeline
    from app.copilot.permission import UserContext
    from app.copilot.semantic.loader import semantic
    semantic.load()
    ctx = UserContext("selftest", "MANAGEMENT", 1, "自测", 1, "总经办", (1,), ("总经办",), ())
    run = lambda q: pipeline.run(q, ctx, options={"no_history": True})
    fresh = lambda q: pipeline.run(q, ctx, options={"no_history": True, "no_cache": True})

    print("三、问数：依赖出故障时的表现")
    base = run("上个月各产线出勤率")
    check("正常情况能查出数据", base["status"] == "OK" and base["result"] and base["result"]["rows"], f"{len(base['result']['rows'])} 行，路由方式 {base['route_by']}")
    real_chat = llm.chat
    llm.chat = boom
    r = run("上个月各产线出勤率")
    check("大模型不可用 + 10 分钟内问过的数据问题 → 靠路由缓存照常查数", r["status"] == "OK" and r["result"] and r["result"]["rows"] and r["route_by"] == "cache", f"路由方式 {r['route_by']}")
    r = fresh("年假有几天")
    check("大模型不可用 + 制度问题 → 规则路由照常检索，直接给制度原文", r["status"] == "OK" and r["sources"] and any("原文" in d for d in r["degraded"]), "；".join(r["degraded"]))
    r = fresh("帮我看看最近哪些客户的生意值得重点跟一下")
    check("大模型不可用 + 需要模型理解的新问题 → 如实告知并给出可用的问法", r["status"] in ("OK", "CLARIFY") and "暂时不可用" in r["answer"] and r["degraded"])
    r = fresh("把上个月的考勤记录删掉")
    check("大模型不可用时，写操作照样被规则拦住", r["status"] == "DENIED")
    llm.chat = real_chat
    llm.BREAKER.opened_at = 0
    llm.BREAKER.success()

    real_query = db._ro_query
    db._ro_query = boom
    run = fresh
    r = run("上个月各产线出勤率")
    check("数据库查询失败 → 给出能看懂的提示，不暴露报错细节", r["status"] == "FAILED" and "暂时不可用" in r["answer"] and "Traceback" not in r["answer"], r["answer"][:60])
    for _ in range(5):
        run("上个月各产线出勤率")
    check("数据库连续失败后熔断，保护业务库", db.DB.state == "OPEN")
    db._ro_query = real_query
    db.DB.opened_at = 0
    check("数据库恢复后自动回到正常路径", run("上个月各产线出勤率")["status"] == "OK" and db.DB.state == "CLOSED")


def batch_tests() -> None:
    from app.kbase import scheduler
    from app.kbase.ingest import batchjob, versioning
    from app.kbase.ingest.batch import meta_for
    tmp = Path(tempfile.mkdtemp(prefix="kbbatch_"))
    tag = uuid.uuid4().hex[:5].upper()

    def item(i: int, good: bool = True) -> dict:
        f = tmp / f"T{tag}-{i}.{'md' if good else 'step'}"
        f.write_text(f"# 自测文档 {tag}-{i}\n\n## 1 说明\n\n这是批量入库自测用的第 {i} 份文档，编号 {tag}。存放环境要求干燥通风，相对湿度不超过 85%，堆码不超过 4 层。\n\n"
                     f"## 2 要求\n\n自测文档 {tag}-{i} 规定搬运时轻拿轻放，轴伸端加保护套，整箱跌落高度不超过 30 厘米。\n", encoding="utf-8")
        meta = meta_for(f"自测文档 {tag}-{i}", "MANUAL", "UPLOAD")
        meta.update(security_level="CONFIDENTIAL", acl_groups="ADMIN", owner_email="selftest@kb-demo.example.com")
        return {"file_path": str(f), "doc_key": f"TEST-B{tag}-{i}", "version": "A", "meta": meta}

    def done(bid):
        b = store.one("SELECT status FROM kb_batch WHERE batch_id = %s", (bid,))
        return b and b["status"] in ("DONE", "PARTIAL", "PAUSED", "CANCELLED")

    print("四、批量入库：消息队列、失败跳过、熔断、降级")
    before = len(feishu())
    r = batchjob.create("MANUAL", [item(1), item(2), item(3), item(4, False), item(5, False)], "selftest", f"自测批次 {tag}-小")
    check("批次创建后走消息队列", r["mode"] == "MQ" and r["total"] == 5)
    wait(lambda: done(r["batch_id"]), 180)
    b = batchjob.detail(r["batch_id"])
    check("单个文件失败不影响整批：好的入库，坏的跳过", b["status"] == "PARTIAL" and b["done"] == 3 and b["failed"] == 2, f"成功 {b['done']}，失败 {b['failed']}")
    mails = store.query("SELECT subject FROM kb_outbox WHERE dedup_key = %s", (f"batch:{r['batch_id']}",))
    per_file = store.one("SELECT COUNT(*) n FROM kb_outbox WHERE dedup_key IN %s", ([f"fail:{j['job_id']}" for j in b["jobs"]],))["n"]
    check("整批只发一封汇总，不逐个文件发", len(mails) == 1 and per_file == 0, mails[0]["subject"] if mails else "")
    check("失败的任务进了死信队列", mq.depth(batchjob.QUEUE).get("dlq", 0) >= 2, str(mq.depth(batchjob.QUEUE)))
    check("失败项推送到了飞书", wait(lambda: any(tag in (m.get("title") or "") for m in feishu()[:len(feishu()) - before or None]), 10))
    n = batchjob.retry_failed(r["batch_id"])
    wait(lambda: done(r["batch_id"]) and batchjob.detail(r["batch_id"])["pending"] == 0, 60)
    check("「重试失败项」会把它们重新排队", n == 2 and batchjob.detail(r["batch_id"])["failed"] == 2, "重试后仍然失败（文件类型不支持，重试无效，符合预期）")

    big = batchjob.create("MANUAL", [item(i, i % 2 == 0) for i in range(10, 26)], "selftest", f"自测批次 {tag}-熔断")
    wait(lambda: done(big["batch_id"]), 240)
    b = batchjob.detail(big["batch_id"])
    check("失败比例超过上限 → 整批自动暂停", b["status"] == "PAUSED" and b["pending"] > 0, b.get("pause_reason") or f"状态 {b['status']}")
    al = store.one("SELECT level, notify_result FROM ops_alert WHERE dedup_key = %s", (f"kbase:batch:{big['batch_id']}",))
    check("暂停时触发严重告警并推送飞书", al and al["level"] == "P1" and "飞书已送达" in (al["notify_result"] or ""), (al or {}).get("notify_result") or "")
    batchjob.cancel(big["batch_id"])
    wait(lambda: batchjob.detail(big["batch_id"])["pending"] == 0, 60)
    check("取消后剩余文件不再处理", batchjob.detail(big["batch_id"])["status"] == "CANCELLED")

    real = mq.available
    mq.available = lambda: False
    local = batchjob.create("MANUAL", [item(30), item(31)], "selftest", f"自测批次 {tag}-队列故障")
    mq.available = real
    wait(lambda: done(local["batch_id"]), 120)
    b = batchjob.detail(local["batch_id"])
    check("消息队列不可用 → 降级为进程内执行，照样完成", local["mode"] == "LOCAL" and b["status"] == "DONE" and b["done"] == 2)
    check("队列故障触发了告警", store.one("SELECT 1 FROM ops_alert WHERE dedup_key = 'platform:mq_down'") is not None)
    alerts.resolve("platform:mq_down", "自测结束")

    got, stop = [], threading.Event()

    def flaky(msg):
        got.append(msg["n"])
        if len(got) == 1:
            raise RuntimeError("模拟：处理到一半进程崩了")
        stop.set()
    qn = f"kb.selftest.{tag.lower()}"
    mq.publish(qn, {"n": 1})
    t = threading.Thread(target=mq.consume, args=(qn, flaky), kwargs={"stop": stop})
    t.start()
    t.join(30)
    check("消费者处理到一半崩溃 → 消息不丢，会重新投递", got == [1, 1])

    print("五、定时任务和告警")
    r = scheduler.run("eas_sync", "selftest")
    check("定时任务可以手动触发并记录结果", r["status"] == "OK", r["message"])
    pw = settings.kingdee_password
    object.__setattr__(settings, "kingdee_password", "wrong")
    from app.kbase.kingdee.client import kingdee
    kingdee._session = None
    r = scheduler.run("eas_sync", "selftest")
    al = store.one("SELECT status FROM ops_alert WHERE dedup_key = 'kbase:schedule:eas_sync' ORDER BY id DESC LIMIT 1")
    check("任务失败 → 进告警中心", r["status"] == "FAILED" and al and al["status"] == "OPEN", r["message"][:60])
    again = alerts.fire("kbase", "schedule_eas_sync", "P2", "定时任务失败：同步金蝶 EAS 物料和 BOM", "重复触发", "kbase:schedule:eas_sync")
    hits = store.one("SELECT hits FROM ops_alert WHERE id = %s", (again,))["hits"]
    check("同一个问题重复触发只累加次数，不重复建告警", hits >= 2, f"累计 {hits} 次")
    object.__setattr__(settings, "kingdee_password", pw)
    kingdee._session = None
    r = scheduler.run("eas_sync", "selftest")
    al = store.one("SELECT status FROM ops_alert WHERE dedup_key = 'kbase:schedule:eas_sync' ORDER BY id DESC LIMIT 1")
    check("任务恢复成功 → 告警自动关闭", r["status"] == "OK" and al["status"] == "CLOSED")
    check("飞书收到了带签名的告警和恢复通知", any("已恢复" in (m.get("title") or "") for m in feishu()[:10]), f"模拟接收端共 {len(feishu())} 条")

    for row in store.query("SELECT doc_key FROM kb_document WHERE doc_key LIKE %s", (f"TEST-B{tag}-%",)):
        versioning.delete_document(row["doc_key"])
    ids = [x["batch_id"] for x in store.query("SELECT batch_id FROM kb_batch WHERE title LIKE %s", (f"自测批次 {tag}%",))]
    store.execute("DELETE FROM kb_ingest_job WHERE batch_id IN %s", (ids,))
    store.execute("DELETE FROM kb_doc_version WHERE doc_key LIKE %s", (f"TEST-B{tag}-%",))
    store.execute("DELETE FROM kb_batch WHERE batch_id IN %s", (ids,))
    store.execute("DELETE FROM kb_outbox WHERE dedup_key IN %s", ([f"batch:{i}" for i in ids],))
    store.execute("UPDATE ops_alert SET status = 'CLOSED', closed_at = NOW() WHERE dedup_key LIKE %s OR dedup_key LIKE %s", ("kbase:batch:%", "kbase:batch_items:%"))
    mq.purge_dead(batchjob.QUEUE)
    check("自测数据已清理", store.one("SELECT COUNT(*) n FROM kb_document WHERE doc_key LIKE %s", (f"TEST-B{tag}-%",))["n"] == 0)


def main() -> int:
    if os.environ.get("KBASE_EVAL_REMOTE") == "1":
        from app.kbase.eval.runner import use_server_models
        use_server_models()
    store.ensure_tables()
    alerts.ensure()
    only = set(sys.argv[1:])
    for name, fn in (("kbase", kbase_tests), ("copilot", copilot_tests), ("batch", batch_tests)):
        if not only or name in only:
            fn()
    ok = sum(1 for _, passed in results if passed)
    print(f"\n通过 {ok} / {len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
