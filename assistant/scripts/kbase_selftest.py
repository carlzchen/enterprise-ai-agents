"""
知识库入库链路的集成自测：真实地走 MySQL、Milvus、MinIO、RabbitMQ 和向量模型，验证「失败怎么处理」「版本怎么管理」「权限怎么控制」。
用法：PYTHONPATH=. .venv/bin/python scripts/kbase_selftest.py
测试文档的编号都以 TEST- 开头、只对管理员可见，结束时全部删除。
内存不够同时加载两份模型时，保持知识库服务运行并加上环境变量 KBASE_EVAL_REMOTE=1，自测会借用服务里的模型。
"""
import socketserver
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.infra.config.settings import settings  # noqa: E402
from app.infra.llm.providers import llm_providers  # noqa: E402
from app.kbase import authz, store, vectors  # noqa: E402
from app.kbase.ingest import events, graph, notify, versioning  # noqa: E402
from app.kbase.query import retrieve  # noqa: E402

BAD = Path(__file__).resolve().parents[1] / "data" / "kbase_samples" / "bad"
META = {"title": "自测文档", "doc_type": "MANUAL", "source": "UPLOAD", "security_level": "CONFIDENTIAL", "acl_groups": "ADMIN", "owner_email": "selftest@kb-demo.example.com"}
TORQUE_Q = "自测电机 KL-SV-99-TEST 法兰螺栓的拧紧力矩是多少"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(("  ✓ " if ok else "  ✗ ") + name + (f"　{detail}" if detail else ""))


TRANSPORT = ("## 3 运输要求\n\n自测电机 KL-SV-99-TEST 运输过程中应避免剧烈振动和雨淋，轴伸端须加保护套，整箱跌落高度不得超过 30 cm。\n")
STORAGE = ("## 2 存放要求\n\n自测电机 KL-SV-99-TEST 应存放在干燥通风的仓库内，相对湿度不超过 85%，堆码层数不超过 4 层，"
           "存放超过 12 个月需重新检测绝缘电阻。\n\n")


def doc(tmp: Path, name: str, torque: str, extra: str = "", tail: str = TRANSPORT) -> Path:
    f = tmp / name
    f.write_text(f"# 自测电机 KL-SV-99-TEST 使用说明\n\n## 1 安装要求\n\n自测电机 KL-SV-99-TEST 安装时法兰螺栓的拧紧力矩为 {torque} N·m，分两次对角拧紧，"
                 f"安装面平面度不大于 0.05 mm。{extra}\n\n" + STORAGE + tail, encoding="utf-8")
    return f


def search(text: str, scope="ADMIN") -> list[dict]:
    hits = retrieve.recall({"rewritten": text}, scope, mode="multi")
    return [h for h in hits if h["doc_key"].startswith("TEST-")]


def eventually(fn, timeout: float = 10.0) -> bool:
    """Milvus 的标量更新（切换后的状态）要过几秒检索才读得到，按「最终能做到」来判断"""
    import time
    end = time.time() + timeout
    while True:
        if fn():
            return True
        if time.time() > end:
            return False
        time.sleep(0.5)


def states(doc_key: str) -> dict[str, str]:
    return {r["chunk_id"]: r["state"] for r in store.query("SELECT chunk_id, state FROM kb_chunk WHERE doc_key = %s", (doc_key,))}


def fail_mail(job_id: str) -> dict | None:
    """入库失败的通知：事件 → 渲染成邮件进发件箱（这里直接调消费者，RabbitMQ 那一段在第六节单独验证）"""
    ev = store.one("SELECT event_id FROM kb_event WHERE event_id = %s", (events.dedup_id("fail", job_id),))
    if not ev:
        return None
    events.handle({"event_id": ev["event_id"]})
    return store.one("SELECT subject, body FROM kb_outbox WHERE dedup_key = %s", (f"ev:{ev['event_id']}",))


class _Sink(socketserver.StreamRequestHandler):
    """极简的本地收信服务器，只为验证邮件真的发得出去"""
    received: list[str] = []

    def handle(self):
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 selftest")
        data, in_data = [], False
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if in_data:
                if line == ".":
                    in_data = False
                    _Sink.received.append("\n".join(data))
                    w("250 ok")
                else:
                    data.append(line)
            elif line.upper().startswith(("EHLO", "HELO")):
                w("250 selftest")
            elif line.upper().startswith("DATA"):
                in_data = True
                w("354 go")
            elif line.upper().startswith("QUIT"):
                w("221 bye")
                return
            else:
                w("250 ok")


def cleanup() -> None:
    for r in store.query("SELECT DISTINCT doc_key FROM kb_doc_version WHERE doc_key LIKE 'TEST-%' UNION SELECT doc_key FROM kb_document WHERE doc_key LIKE 'TEST-%'"):
        if not versioning.delete_document(r["doc_key"]):
            vectors.delete_doc(r["doc_key"])
    for table in ("kb_ingest_job", "kb_doc_version", "kb_chunk", "kb_parent", "kb_image", "kb_compensation", "kb_event"):
        store.execute(f"DELETE FROM {table} WHERE doc_key LIKE 'TEST-%'")
    store.execute("DELETE FROM kb_outbox WHERE to_email = %s", (META["owner_email"],))
    from app.infra.object_storage.minio_gateway import minio_gateway
    for prefix in ("raw/TEST-", "images/TEST-", "parsed/TEST-"):            # 测试文档的原文件和图片（包括早期版本自测没清掉的）
        minio_gateway.delete_prefix(prefix)




def main() -> int:
    import os
    if os.environ.get("KBASE_EVAL_REMOTE") == "1":            # 借用正在运行的知识库服务里的模型，不再自己加载一份
        from app.kbase.eval.runner import use_server_models
        use_server_models()
    object.__setattr__(settings, "kbase_subject_llm", False)  # 主题识别用规则（模型识别和证据校验在单元测试里用假模型测）
    store.ensure_tables()
    vectors.ensure()
    cleanup()
    tmp = Path(tempfile.mkdtemp(prefix="kbselftest_"))

    print("一、不合格的文件：直接拒绝、不重试、通知负责人")
    for i, name in enumerate(sorted(p.name for p in BAD.iterdir())):
        r = graph.ingest(str(BAD / name), f"TEST-BAD-{i}", "A", {**META, "title": name})
        mail = fail_mail(r["job_id"])
        check(f"{name} 被拒绝并通知", r["status"] == "FAILED" and r["attempts"] == 1 and mail is not None and r["error"] in mail["body"], r["error"])
    check("被拒绝的文件没有任何切片进入向量库", not search("加密的手册"))

    print("二、临时故障：自动重试，重试时接着上次的进度")
    v1 = doc(tmp, "selftest_v1.md", "12")
    real, calls = llm_providers.generate_embeddings, {"n": 0}

    def flaky(texts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("模拟：向量模型服务暂时连不上")
        return real(texts)
    llm_providers.generate_embeddings = flaky
    r = graph.ingest(str(v1), "TEST-DOC", "A", META)
    check("第一次失败后进入等待重试", r["status"] == "RETRY" and r["attempts"] == 1 and r["next_retry_at"] is not None, f"停在 {r['stage']}，{r['error']}")
    check("失败期间线上没有半成品", not search("拧紧力矩") and store.one("SELECT active_version v FROM kb_document WHERE doc_key = 'TEST-DOC'") in (None, {"v": None}))
    check("没到时间不会重试", graph.retry_due() == [])
    again = graph.retry_due(datetime.now() + timedelta(seconds=graph.BACKOFF[0] + 5))
    llm_providers.generate_embeddings = real
    check("到时间后重试成功", len(again) == 1 and again[0]["status"] == "DONE", f"切片 {again[0]['chunks'] if again else 0} 个，比对 {again[0].get('diff') if again else ''}")
    check("重试成功的任务没有失败通知", fail_mail(r["job_id"]) is None)

    print("三、一直失败：重试三次后停下、通知、登记补偿任务清掉残留")

    def broken(texts):
        raise ConnectionError("模拟：向量模型服务一直连不上")
    llm_providers.generate_embeddings = broken
    r = graph.ingest(str(doc(tmp, "selftest_down.md", "99")), "TEST-DOWN", "A", {**META, "title": "一直失败的文档"})
    for n in range(len(graph.BACKOFF)):
        graph.retry_due(datetime.now() + timedelta(days=1))
    llm_providers.generate_embeddings = real
    job = store.one("SELECT status, attempts FROM kb_ingest_job WHERE job_id = %s", (r["job_id"],))
    mail = fail_mail(r["job_id"])
    check("重试 3 次后标记失败", job["status"] == "FAILED" and job["attempts"] == len(graph.BACKOFF) + 1, f"共尝试 {job['attempts']} 次")
    check("只发一封通知，写明阶段、原因和补偿", mail is not None and "向量化" in mail["body"] and "已重试 3 次" in mail["body"] and "补偿任务" in mail["body"])
    from app.infra.object_storage.minio_gateway import minio_gateway
    raw_before = list(minio_gateway.client.list_objects(minio_gateway.bucket, prefix="raw/TEST-DOWN/", recursive=True))
    comp = versioning.run_compensations()
    raw_after = list(minio_gateway.client.list_objects(minio_gateway.bucket, prefix="raw/TEST-DOWN/", recursive=True))
    check("补偿任务清掉了 MinIO 里的原文件", raw_before and not raw_after and any(c["ok"] for c in comp), f"清理前 {len(raw_before)} 个对象，清理后 {len(raw_after)} 个")

    print("四、切片级增量：没变的复用，新增的先暂存（看不到），切换后生效，消失的失效")
    n_a = states("TEST-DOC")
    r = graph.ingest(str(v1), "TEST-DOC", "A", META)
    check("同一个文件再导一次会跳过（文件级 SHA-256）", r["status"] == "SKIPPED" and states("TEST-DOC") == n_a, r["error"] or "")
    r = graph.ingest(str(doc(tmp, "selftest_v1_changed.md", "13")), "TEST-DOC", "A", META)
    check("版本号没升但内容变了会被拒绝", r["status"] == "FAILED" and "必须升版本号" in (r["error"] or ""), r["error"] or "")
    v2 = doc(tmp, "selftest_v2.md", "15", "B 版新增：拧紧后须用记号笔做防松标记。",
             tail="## 4 质保\n\n自测电机 KL-SV-99-TEST 质保期为出厂后 18 个月，人为损坏不在质保范围内。\n")       # 改了第 1 节、删了第 3 节、加了第 4 节
    real_switch, blocked = versioning.switch, {"n": 0}

    def switch_once_fails(*a, **k):                           # 第一次切换前「掉电」：新切片已经暂存，但还没切换
        blocked["n"] += 1
        if blocked["n"] == 1:
            raise ConnectionError("模拟：切换前进程挂了")
        return real_switch(*a, **k)
    versioning.switch = switch_once_fails
    r = graph.ingest(str(v2), "TEST-DOC", "B", META)
    pend = {k for k, v in states("TEST-DOC").items() if v == "PENDING"}
    hits = search(TORQUE_Q)
    check("暂存的新切片检索不到，线上仍是旧版本", r["status"] == "RETRY" and pend and hits and {h["version"] for h in hits} == {"A"}
          and not any(h["chunk_id"] in pend for h in hits), f"暂存 {len(pend)} 块")
    again = graph.retry_due(datetime.now() + timedelta(days=1))
    versioning.switch = real_switch
    d = (again[0] if again else {}).get("diff") or {}
    st = states("TEST-DOC")
    check("重试后切换成功，比对结果对得上", again and again[0]["status"] == "DONE" and d.get("reused", 0) >= 1 and d.get("new", 0) >= 2 and d.get("vanished", 0) >= 1,
          f"新增 {d.get('new')}，沿用 {d.get('reused')}，消失 {d.get('vanished')}")
    reused = [k for k in n_a if st.get(k) == "VALID"]
    check("沿用的切片主键不变、不重新向量化", len(reused) == d.get("reused") and all(k in n_a for k in reused), f"沿用 {len(reused)} 块")
    vanished = store.query("SELECT chunk_id, valid_to FROM kb_chunk WHERE doc_key = 'TEST-DOC' AND state = 'INVALID'")
    check("消失的切片标成失效并记下 valid_to", len(vanished) == d.get("vanished") and all(v["valid_to"] == "B" for v in vanished))
    eventually(lambda: any("15 N·m" in h["content"] for h in search(TORQUE_Q)))
    hits = search(TORQUE_Q)
    check("检索只返回新版本的内容", hits and {h["version"] for h in hits} == {"B"} and any("15 N·m" in h["content"] for h in hits),
          f"命中 {len(hits)} 条，版本 {sorted({h['version'] for h in hits})}")
    milvus_states = vectors.ids_of("TEST-DOC")
    check("Milvus 里的状态和 SQL 一致", milvus_states == states("TEST-DOC"), f"{len(milvus_states)} 条")
    r = graph.ingest(str(doc(tmp, "selftest_old.md", "11")), "TEST-DOC", "0", META)
    check("比当前版本还旧的文件会被拒绝", r["status"] == "FAILED" and "还旧" in (r["error"] or ""), r["error"] or "")
    back = versioning.rollback("TEST-DOC")
    eventually(lambda: {h["version"] for h in search(TORQUE_Q)} == {"A"})
    hits = search(TORQUE_Q)
    check("可以一键回滚到上一个版本（同一个事务切回去）", back == "A" and hits and {h["version"] for h in hits} == {"A"} and any("12 N·m" in h["content"] for h in hits)
          and {k for k, v in states("TEST-DOC").items() if v == "VALID"} == set(n_a))

    print("五、对账：SQL 和向量库对不上时自动修复")
    ids = sorted(k for k, v in states("TEST-DOC").items() if v == "VALID")
    vectors.delete_ids(ids[:1])
    vectors.update_fields([{"chunk_id": ids[1], "state": "INVALID"}])
    before = versioning.verify("TEST-DOC", "A", repair=False)
    after = versioning.verify("TEST-DOC", "A", repair=True)
    check("向量库少了一条、状态错了一条，都能发现并修好", not before["ok"] and before["missing"] == 1 and before["state_mismatch"] == 1 and after["ok"]
          and vectors.ids_of("TEST-DOC") == states("TEST-DOC"), f"修复前 缺 {before['missing']} / 状态错 {before['state_mismatch']}")
    report = versioning.reconcile_all()
    check("全库对账没有异常", not report["broken"], f"{report['documents']} 份文档，一致 {report['consistent']}，修复 {report['repaired']}，异常 {len(report['broken'])}")
    total = store.one("SELECT COUNT(*) n FROM kb_chunk c JOIN kb_document d ON d.doc_key = c.doc_key AND d.active_version = c.version WHERE c.state = 'VALID'")["n"]
    check("生效切片数和对账结果一致", total == report["chunks"], f"生效切片 {total} 个")

    print("六、通知：持久化事件 → RabbitMQ → SMTP")
    from app.platform import mq
    ev = store.one("SELECT event_id, kind FROM kb_event WHERE doc_key = 'TEST-DOC' AND kind = 'VERSION_SWITCHED' ORDER BY created_at DESC LIMIT 1")
    check("版本切换时写了事件（和切换同一个事务）", ev is not None)
    status = lambda: (store.one("SELECT status FROM kb_event WHERE event_id = %s", (ev["event_id"],)) or {}).get("status")
    service_up = False
    try:
        import httpx
        service_up = httpx.get(f"http://127.0.0.1:{settings.kbase_app_port}/health", timeout=3, trust_env=False).status_code == 200
    except Exception:
        pass
    if mq.available() and service_up:                         # 知识库服务在跑：它的转发线程投递到 RabbitMQ，消费线程收到后写发件箱
        check("事件经 RabbitMQ 投递并被服务的消费者处理", eventually(lambda: status() == "DONE", 20), f"事件状态 {status()}")
    elif mq.available():                                      # 服务没开：自己转发，再从队列里把这条取出来处理
        relayed = events.relay_once()
        ch, got, others_ = mq._channel(), None, []
        for _ in range(500):
            method, _, body = ch.basic_get(settings.kbase_events_queue, auto_ack=False)
            if method is None:
                break
            msg = __import__("json").loads(body)
            if msg["event_id"] == ev["event_id"]:
                ch.basic_ack(method.delivery_tag)
                got = msg
                break
            others_.append(method.delivery_tag)
        for tag in others_:
            ch.basic_nack(tag, requeue=True)
        check("事件投递到了 RabbitMQ", got is not None, f"本轮转发 {relayed}")
        if got:
            events.handle(got)
    else:
        check("事件投递到了 RabbitMQ（队列不可用，跳过）", True, "RabbitMQ 不可用")
        events.handle({"event_id": ev["event_id"]})
    check("事件对应的邮件进了发件箱（按事件号去重）", store.one("SELECT 1 FROM kb_outbox WHERE dedup_key = %s", (f"ev:{ev['event_id']}",)) is not None)
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pending = store.one("SELECT COUNT(*) n FROM kb_outbox WHERE status = 'PENDING' AND to_email = %s", (META["owner_email"],))["n"]
    others = [r["id"] for r in store.query("SELECT id FROM kb_outbox WHERE status = 'PENDING' AND to_email <> %s", (META["owner_email"],))]
    if others:                                                # 别人的待发邮件先挂起，不能让自测把它们发到假服务器上
        store.execute("UPDATE kb_outbox SET status = 'HOLD' WHERE id IN %s", (others,))
    for k, v in (("smtp_host", "127.0.0.1"), ("smtp_port", server.server_address[1]), ("smtp_tls", False), ("smtp_user", "")):
        object.__setattr__(settings, k, v)
    sent = notify.send_pending(limit=50)
    object.__setattr__(settings, "smtp_host", "")
    if others:
        store.execute("UPDATE kb_outbox SET status = 'PENDING' WHERE id IN %s", (others,))
    server.shutdown()
    check("发件箱里的邮件全部经 SMTP 发出", sent["sent"] == pending and sent["failed"] == 0 and len(_Sink.received) == pending, f"发出 {sent['sent']} 封")
    check("同一个事件重复投递不会重复发", notify.enqueue(f"ev:{ev['event_id']}", None, "x", "x") is False)

    print("七、权限：部门（来自公司 SSO，沿组织树继承）+ 密级，未标注默认拒绝")
    dept_doc = tmp / "selftest_dept.md"
    dept_doc.write_text("# 研发中心内部资料\n\n## 伺服样机调试记录\n\n自测样机 KL-SV-99-TEST 在 3000 rpm 下温升 42 K，满足设计要求，编码器零位偏差 0.3°。\n", encoding="utf-8")
    r = graph.ingest(str(dept_doc), "TEST-DEPT", "A", {**META, "title": "研发中心内部资料", "security_level": "INTERNAL", "acl_groups": "D:D100"})
    eng = authz.Scope.build("E1001", ["ENGINEER", "D:D000", "D:D100", "D:D110"], 2, source="sso")     # 伺服研发部，上级是研发中心
    sales = authz.Scope.build("E3001", ["SALES", "D:D000", "D:D400", "D:D410"], 1, source="sso")
    low = authz.Scope.build("E9", ["D:D100"], 0, source="sso")
    q = "自测样机 KL-SV-99-TEST 温升多少"
    check("下级部门的人能看到上级部门授权的文档", r["status"] == "DONE" and eventually(lambda: any(h["doc_key"] == "TEST-DEPT" for h in search(q, eng))))
    check("别的部门看不到", not any(h["doc_key"] == "TEST-DEPT" for h in search(q, sales)))
    check("部门对上了但密级不够也看不到", not any(h["doc_key"] == "TEST-DEPT" for h in search(q, low)))
    store.execute("UPDATE kb_document SET sec_level = NULL WHERE doc_key = 'TEST-DEPT'")
    vectors.update_fields([{"chunk_id": c, "level": -1} for c in states("TEST-DEPT")])
    check("没标注密级的文档谁都看不到（包括管理员）", not any(h["doc_key"] == "TEST-DEPT" for h in search(q, "ADMIN")))

    print("八、图片：私有存储，按生效版本登记，现签临时链接")
    img_dir = tmp / "imgdoc"
    (img_dir / "images").mkdir(parents=True)
    from PIL import Image
    Image.new("RGB", (240, 160), (30, 120, 200)).save(img_dir / "images" / "wiring.png")
    (img_dir / "wiring.md").write_text("# 接线说明\n\n## 动力线接线\n\n按下图接线，U V W 不能接反。\n\n![接线图](images/wiring.png)\n", encoding="utf-8")
    object.__setattr__(settings, "vl_api_key", "")             # 不调视觉模型，只验证存储和登记
    r = graph.ingest(str(img_dir / "wiring.md"), "TEST-IMG", "A", {**META, "title": "接线说明"})
    img = store.one("SELECT sha, object_key, parent_seq FROM kb_image WHERE doc_key = 'TEST-IMG' AND version = 'A'")
    chunk = store.one("SELECT content FROM kb_chunk WHERE doc_key = 'TEST-IMG' AND state = 'VALID' AND content LIKE %s", ("%kbimg://%",))
    check("图片登记在生效版本下、切片里只有 kbimg 引用", r["status"] == "DONE" and img and img["parent_seq"] is not None and chunk is not None and "http" not in chunk["content"])
    import urllib.request
    try:
        urllib.request.urlopen(minio_gateway.public_url(img["object_key"]), timeout=5)
        anonymous = True
    except Exception:
        anonymous = False
    signed = urllib.request.urlopen(minio_gateway.presigned_url(img["object_key"], 60), timeout=5).read()
    check("匿名访问被拒绝，临时签名链接能打开", not anonymous and signed[:4] == b"\x89PNG")

    cleanup()
    check("自测数据已清理", not search("自测电机") and store.one("SELECT COUNT(*) n FROM kb_document WHERE doc_key LIKE 'TEST-%'")["n"] == 0
          and not list(minio_gateway.client.list_objects(minio_gateway.bucket, prefix="raw/TEST-", recursive=True)))
    ok = sum(1 for _, passed, _ in results if passed)
    print(f"\n通过 {ok} / {len(results)}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
