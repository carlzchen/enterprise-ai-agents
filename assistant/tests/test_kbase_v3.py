"""
知识库 v3 的单元测试：统一授权、清洗与切片哈希、主题识别和证据校验、切片比对、三路召回与 RRF、上下文预算、主题确认、
图纸解析、飞书身份规则、通知事件。数据库、向量库、大模型都用假的替身，不需要起服务。
"""
import json
import types
from pathlib import Path

import pytest

from app.kbase import authz
from app.kbase.ingest import cad, clean, events, subject, versioning
from app.kbase.query import answer, retrieve, topic

ROOT = Path(__file__).resolve().parents[1]


# ---------- 统一授权 ----------

def test_scope_for_role_keeps_old_visibility():
    sales, eng = authz.Scope.for_role("SALES"), authz.Scope.for_role("ENGINEER")
    assert sales.clearance == 1 and eng.clearance == 2
    assert eng.can_view("CONFIDENTIAL", "ENGINEER,QUALITY,MANAGEMENT") and not sales.can_view("CONFIDENTIAL", "ENGINEER,QUALITY,MANAGEMENT")
    assert sales.can_view("PUBLIC", "ALL") and sales.can_view("INTERNAL", "SALES,ENGINEER")


def test_unlabeled_documents_are_denied_even_for_admin():
    admin = authz.Scope.for_role("ADMIN")
    assert not admin.can_view(None, "ALL") and not admin.can_view("PUBLIC", "") and admin.can_view("CONFIDENTIAL", "QUALITY")


def test_dept_tokens_inherit_along_org_tree_and_clearance_caps():
    # 张工在 伺服研发部（D110），上级是研发中心（D100）：文档写「D:D100」时他能看
    s = authz.Scope.build("u1", ["ENGINEER", "D:D110", "D:D100"], 1, source="sso")
    assert s.can_view("INTERNAL", "D:D100") and not s.can_view("INTERNAL", "D:D200")
    assert not s.can_view("CONFIDENTIAL", "D:D100")                       # 部门对上了，但密级不够


def test_scope_filters_are_consistent():
    s = authz.Scope.build("u1", ["SALES", "D:od-sales"], 1)
    expr = s.milvus_expr()
    assert "level >= 0 and level <= 1" in expr and '"D:od-sales"' in expr and '"ALL"' in expr
    where, args = s.sql("d")
    assert where.count("FIND_IN_SET") == len(s.tokens) and args[0] == 1 and "d.sec_level IS NOT NULL" in where
    assert authz.Scope.for_role("SALES").key() == authz.Scope.for_role("SALES", "另一个人").key()    # 可见范围相同 → 缓存共享
    assert authz.Scope.for_role("SALES").key() != s.key()


# ---------- 清洗、脱敏、术语、切片哈希 ----------

def test_mask_keeps_image_links_and_counts():
    md, stats = clean.mask_sensitive("电话 13912345678 邮箱 a.b@kelang-motor.example.com ![图](images/13912345678.png)")
    assert "139****5678" in md and "a***@kelang-motor.example.com" in md and "(images/13912345678.png)" in md
    assert stats == {"手机号": 1, "邮箱": 1}


def test_unify_terms_only_after_numbers_for_units():
    md, n = clean.unify_terms("额定扭矩 2.39 Nm，Nmax 不变，40 °C，φ14")
    assert md == "额定转矩 2.39 N·m，Nmax 不变，40 ℃，Φ14" and n >= 3


def test_norm_hash_ignores_layout_but_not_content():
    a = "【规格书 V1】技术参数\n<!-- page:3 -->额定转矩 2.39 N·m\n第 3 页"
    b = "【规格书 V2（改了标题）】技术参数\n额定转矩  2.39 N·m"
    c = "【规格书 V2】技术参数\n额定转矩 2.40 N·m"
    assert clean.norm_hash(a) == clean.norm_hash(b) != clean.norm_hash(c)


# ---------- 主题识别：模型 + 证据校验 ----------

META = {"title": "KL-SV-80-750W 规格书", "product_code": "KL-SV-80-750W", "series": "SERVO"}
TEXT = "【KL-SV-80-750W 规格书】技术参数\nKL-SV-80-750W 的额定转矩为 2.39 N·m，额定转速 3000 rpm。"


@pytest.mark.parametrize("out, ok, why", [
    ({"models": ["KL-SV-80-750W"], "series": "SERVO", "topic": "参数规格", "evidence": "KL-SV-80-750W 的额定转矩为 2.39 N·m"}, True, ""),
    ({"models": ["KL-SV-80-750W"], "series": "SERVO", "topic": "参数规格", "evidence": "额定转矩为 2.4 N·m"}, False, "证据片段"),
    ({"models": ["KL-SV-80-400W"], "series": "SERVO", "topic": "参数规格", "evidence": "额定转速 3000 rpm"}, False, "原文和文档元数据里都没有"),
    ({"models": ["KL-SV-80-750W"], "series": "STEPPER", "topic": "参数规格", "evidence": "额定转速 3000 rpm"}, False, "型号前缀不一致"),
    ({"models": [], "series": "FOO", "topic": "x", "evidence": "额定转速 3000 rpm"}, False, "系列表"),
])
def test_subject_evidence_check(out, ok, why):
    got, reason = subject.check(out, TEXT, META)
    assert (got is not None) == ok and why in reason


def test_identify_falls_back_to_rules_and_trips_after_failures(monkeypatch):
    calls = {"n": 0}

    def boom(text, meta):
        calls["n"] += 1
        raise TimeoutError("x")
    monkeypatch.setattr(subject, "_call", boom)
    monkeypatch.setattr(subject, "WORKERS", 1)
    chunks = [{"content": TEXT} for _ in range(12)]
    stats = subject.identify(chunks, META, use_llm=True)
    assert stats["rule"] == 12 and calls["n"] == 6                         # 失败过半后不再一块块等超时
    assert all(c["item_name"] == "KL-SV-80-750W" and c["series"] == "SERVO" and c["subject"]["by"] == "rule" for c in chunks)


def test_identify_uses_checked_llm_result(monkeypatch):
    monkeypatch.setattr(subject, "_call", lambda text, meta: {"models": ["KL-SV-80-750W"], "series": "", "topic": "参数规格",
                                                               "evidence": "额定转速 3000 rpm"})
    chunks = [{"content": TEXT}]
    subject.identify(chunks, META, use_llm=True)
    assert chunks[0]["subject"]["by"] == "llm" and chunks[0]["series"] == "SERVO"      # 系列由型号前缀补上


# ---------- 切片比对：沿用 / 新增 / 消失 ----------

def test_diff_reuses_same_hash_and_reports_vanished(monkeypatch):
    old = [{"chunk_id": "A", "norm_hash": "h1", "subject": json.dumps({"by": "llm"}), "item_name": "M1", "series": "SERVO"},
           {"chunk_id": "B", "norm_hash": "h2", "subject": None, "item_name": "", "series": None},
           {"chunk_id": "C", "norm_hash": "h1", "subject": None, "item_name": "", "series": None}]
    monkeypatch.setattr(versioning.store, "query", lambda sql, args=None: [dict(r) for r in old])
    chunks = [{"seq": 0, "norm_hash": "h1"}, {"seq": 1, "norm_hash": "h3"}, {"seq": 2, "norm_hash": "h1"}, {"seq": 3, "norm_hash": "h1"}]
    d = versioning.diff("DOC", "2", chunks)
    assert [c["chunk_id"] for c in chunks[:3]] == ["A", versioning.chunk_id("DOC", "2", 1), "C"]
    assert chunks[0]["reused"] and chunks[0]["subject"] == {"by": "llm"} and chunks[0]["item_name"] == "M1"
    assert not chunks[3]["reused"]                                          # 同样的内容多出一块：一对一复用，多的算新增
    assert d == {"new": 2, "reused": 2, "vanished": 1}                      # B 消失


# ---------- 三路召回、RRF ----------

def test_rrf_rewards_agreement_across_routes():
    lists = {"dense": [{"chunk_id": "x"}, {"chunk_id": "y"}], "bm25": [{"chunk_id": "y"}, {"chunk_id": "z"}], "hyde": [{"chunk_id": "y"}]}
    out = retrieve.rrf(lists)
    assert out[0]["chunk_id"] == "y" and out[0]["routes"] == {"dense": 2, "bm25": 1, "hyde": 1}


def test_recall_runs_three_routes_with_filters(monkeypatch):
    seen = []

    def fake_search(vector, field, expr, limit):
        seen.append((field, type(vector).__name__, expr, limit))
        return [{"chunk_id": f"{field}-{i}", "doc_key": "D", "version": "1", "section": "s", "content": "c"} for i in range(5)]
    monkeypatch.setattr(retrieve.vectors, "single_search", fake_search)
    monkeypatch.setattr(retrieve.versioning, "valid_chunks", lambda ids: {i: {"chunk_id": i, "version": "1"} for i in ids})
    embs = {"rewritten": {"dense": [0.1], "sparse": {1: 0.5}}, "hyde": {"dense": [0.2], "sparse": {}}}
    out = retrieve.recall({"rewritten": "伺服过热", "hyde": "过热时检查负载"}, authz.Scope.for_role("SALES"), None, "SERVO", embeddings=embs)
    fields = sorted(f for f, *_ in seen)
    assert fields == ["bm25_vector", "dense_vector", "dense_vector"] and len(out) == 10      # 稠密和 HyDE 搜同一个字段，假数据主键相同，融合后去重
    assert all('series == "SERVO"' in e and "level <= 1" in e for _, _, e, _ in seen) and all(lim == retrieve.RECALL == 10 for *_, lim in seen)
    assert next(t for f, t, *_ in seen if f == "bm25_vector") == "str"     # BM25 直接传问题文本，由 Milvus 分词


def test_recall_without_hyde_skips_that_route(monkeypatch):
    seen = []
    monkeypatch.setattr(retrieve.vectors, "single_search", lambda v, f, e, l: seen.append(f) or [{"chunk_id": f + str(i)} for i in range(3)])
    monkeypatch.setattr(retrieve.versioning, "valid_chunks", lambda ids: {i: {"chunk_id": i} for i in ids})
    retrieve.recall({"rewritten": "q", "hyde": ""}, "ADMIN", embeddings={"rewritten": {"dense": [0.1], "sparse": {}}})
    assert sorted(seen) == ["bm25_vector", "dense_vector"]


def test_recall_drops_chunks_not_valid_in_sql(monkeypatch):
    monkeypatch.setattr(retrieve.vectors, "single_search", lambda v, f, e, l: [{"chunk_id": "keep", "section": "旧"}, {"chunk_id": "stale"}])
    monkeypatch.setattr(retrieve.versioning, "valid_chunks", lambda ids: {"keep": {"chunk_id": "keep", "section": "新章节"}})
    out = retrieve.recall({"rewritten": "q"}, "ADMIN", mode="bm25")
    assert [h["chunk_id"] for h in out] == ["keep"] and out[0]["section"] == "新章节"       # 元数据以 SQL 的生效版本为准


# ---------- 上下文预算 ----------

def test_pack_respects_budget_truncates_then_drops():
    src = [{"index": i, "context": "伺服。" * n, "type": "kb"} for i, n in enumerate([2000, 2000, 2000], start=1)]
    out, hist, st = answer.pack("问题", src, "用户：x", budget=12000, reserve=2000)
    assert st["total"] <= 12000 - 2000 and [s["index"] for s in out] == [1, 2] and out[1].get("truncated") and st["dropped"] == [3]


def test_pack_trims_old_history_first():
    hist = "（更早对话的摘要：聊过 KL-SV-80-750W）\n" + "\n".join(f"用户：{'问' * 300}{i}" for i in range(20))
    _, h, _ = answer.pack("q", [], hist, history_cap=1000)
    assert h.startswith("（更早对话的摘要") and answer.tokens(h) <= 1000 and h.endswith("19")


# ---------- 主题确认 ----------

@pytest.fixture
def models(monkeypatch):
    monkeypatch.setattr(topic, "known_models", lambda: {"KL-SV-80-400W", "KL-SV-80-750W", "KL-SV-60-200W", "KL-ST-42x48"})


def _plan(route, models_, keys=()):
    return {"route": route, "models": list(models_), "param_keys": list(keys), "series": None}


def test_topic_exact_and_completion(models):
    assert topic.resolve("", _plan("PARAM", ["KL-SV-80-750W"], ["rated_torque"]), None, False)["source"] == "问题"
    one = topic.resolve("", _plan("PARAM", ["KL-SV-60"], ["rated_torque"]), None, False)
    assert one["models"] == ["KL-SV-60-200W"] and one["source"] == "补全" and not one["clarify"]


def test_topic_ambiguous_param_question_asks_back(models):
    lock = topic.resolve("", _plan("PARAM", ["KL-SV-80"], ["rated_torque"]), None, False)
    assert lock["clarify"] and lock["candidates"] == ["KL-SV-80-400W", "KL-SV-80-750W"]
    desc = topic.resolve("", _plan("DESC", ["KL-SV-80"]), None, False)
    assert not desc["clarify"] and set(desc["models"]) == {"KL-SV-80-400W", "KL-SV-80-750W"}     # 说明类：候选都当过滤条件


def test_topic_follow_up_inherits_session_lock(models):
    lock = topic.resolve("", _plan("DESC", [], ["rated_torque"]), {"models": ["KL-SV-80-750W"], "series": "SERVO"}, True)
    assert lock["source"] == "上文" and topic.apply(_plan("DESC", [], ["rated_torque"]), lock)["route"] == "MIXED"


# ---------- 图纸 ----------

@pytest.mark.parametrize("text, kind, value, plus, minus, fit, count, pitch", [
    ("%%c70", "dia", 70, None, None, None, None, None),
    ("4×%%c6", "dia", 6, None, None, None, 4, None),
    ("4-Φ6.6", "dia", 6.6, None, None, None, 4, None),
    ("M5x0.8-6H", "thread", 5, None, None, "6H", None, 0.8),
    ("M8", "thread", 8, None, None, None, None, None),
    ("R3", "radius", 3, None, None, None, None, None),
    ("Φ22H7", "dia", 22, None, None, "H7", None, None),
    ("Φ22H7/g6", "dia", 22, None, None, "H7/g6", None, None),
    ("50%%p0.1", "linear", 50, 0.1, -0.1, None, None, None),
    ("Φ19+0.009-0.004", "dia", 19, 0.009, -0.004, None, None, None),
    ("<>", "linear", 35, None, None, None, None, None),
])
def test_parse_dim_text(text, kind, value, plus, minus, fit, count, pitch):
    d = cad.parse_dim_text(text, measurement=35.0)
    assert (d["kind"], d["value"], d["plus"], d["minus"], d["fit"], d["count"], d["pitch"]) == (kind, value, plus, minus, fit, count, pitch)


def test_mtext_stacked_tolerance():
    text, stack = cad.mtext_plain("\\A0;%%c70{\\H0.60x;\\S 0.000^ -0.030;}")
    assert text == "%%c70" and stack == (0.0, -0.03)
    d = cad.parse_dim_text(text, stack)
    assert (d["value"], d["plus"], d["minus"]) == (70, 0.0, -0.03)


def test_gb_drawing_card():
    f = ROOT / "data" / "kbase_samples" / "drawings_gb" / "KL-SV-80-750W-SHAFT.dxf"
    if not f.exists():
        from scripts.make_cad_gb_sample import main
        main()
    d = cad.read(f)
    c = d["card"]
    assert d["title"]["DWG_NO"] == "KL-SV-80-750W-SHAFT" and d["title"]["MATERIAL"] == "40Cr"         # 中文属性名
    assert c["bom_bottom_up"] and c["bom_method"] == "grid" and [r[0] for r in d["bom"]] == ["1", "2", "3"]
    assert d["bom"][1][2] == "深沟球轴承 6202-2RS（含防尘盖，C3 游隙）"                                    # 续行合并
    srcs = {x["source"] for x in c["dims"]}
    assert {"geometry_block", "exploded", "measurement+dimstyle"} <= srcs and c["blocks_expanded"] == 1
    assert any(x["kind"] == "dia" and x["value"] == 14 and x["plus"] == 0.012 and x["minus"] == 0.001 for x in c["dims"])
    assert any(x["kind"] == "linear" and x["value"] == 120 and x["plus"] == 0.1 and x["minus"] == -0.2 for x in c["dims"])   # 块里的标注
    assert {"model", "paper:Layout1"} <= set(c["spaces"]) and d["notes"][0].startswith("1. 调质")


def test_sample_drawings_still_parse_the_same():
    d = cad.read(ROOT / "data" / "kbase_samples" / "drawings" / "KL-SV-80-750W.dxf")
    assert {k: v["value"] for k, v in d["dims"].items()} == {"flange_size": 80, "spigot_dia": 70, "pcd": 90, "hole_dia": 6, "hole_count": 4,
                                                             "shaft_len": 35, "shaft_dia": 19}
    assert d["dims"]["shaft_dia"]["plus"] == 0.009 and len(d["bom"]) == 8 and not d["card"]["bom_bottom_up"]


# ---------- 公司 SSO：身份规则、令牌校验、注销、内网限制 ----------

def _claims(title, level, path, **kw):
    return {"sub": "E1", "name": "x", "employee_no": "KL1", "title": title, "level": level, "dept_path": path, **kw}


def test_sso_rules():
    from app.kbase.auth import principal
    assert principal.derive(_claims("高级研发工程师", "P7", ["D000", "D100", "D110"])) == {"roles": ["ENGINEER"], "clearance": 2, "matched": True}
    assert principal.derive(_claims("销售经理", "M1", ["D000", "D400", "D410"]))["clearance"] == 1          # 销售角色密级封顶「内部」
    assert set(principal.derive(_claims("研发总监", "M3", ["D000", "D100"]))["roles"]) == {"ENGINEER", "MANAGEMENT"}
    assert principal.derive(_claims("质量工程师", "P5", ["D000", "D200"]))["clearance"] == 1               # 职级决定密级
    assert principal.derive(_claims("", "", ["D000", "D600"])) == {"roles": ["FINANCE"], "clearance": 0, "matched": False}


def test_sso_scope_inherits_department_path():
    from app.kbase.auth import principal
    s = principal.to_scope(principal.build(_claims("高级研发工程师", "P7", ["D000", "D100", "D110"], dept_names=["科朗", "研发中心", "伺服研发部"])))
    assert set(s.tokens) == {"ALL", "ENGINEER", "D:D000", "D:D100", "D:D110"} and s.source == "sso" and s.employee_no == "KL1"
    assert s.can_view("INTERNAL", "D:D100") and not s.can_view("INTERNAL", "D:D400")
    lost = principal.to_scope(principal.build(_claims("", "", ["D000", "D600"])))
    assert lost.clearance == 0 and lost.notes and not lost.can_view("INTERNAL", "ALL") and lost.can_view("PUBLIC", "ALL")


@pytest.fixture
def idp(monkeypatch):
    """用模拟 SSO 的密钥签令牌，公钥直接交给校验函数（不走网络）"""
    from app.kbase.auth import sso, sso_mock
    key = sso_mock.private()
    monkeypatch.setattr(sso, "discovery", lambda: {"issuer": "https://sso.example", "jwks_uri": "x"})
    monkeypatch.setattr(sso, "_key_for", lambda token: key.public_key())
    monkeypatch.setattr(sso, "settings", types.SimpleNamespace(sso_client_id="kbase"))

    def sign(**kw):
        import time
        now = int(time.time())
        return sso_mock.jwt.encode({"iss": "https://sso.example", "aud": "kbase", "iat": now, "exp": now + 300, **kw}, key, algorithm="RS256")
    return sso, sign


def test_id_token_signature_issuer_audience_are_checked(idp):
    sso, sign = idp
    assert sso.verify(sign(sub="E1"))["sub"] == "E1"
    with pytest.raises(sso.SSOError):
        sso.verify(sign(sub="E1", aud="other-app"))
    with pytest.raises(sso.SSOError):
        sso.verify(sign(sub="E1", iss="https://evil.example"))
    from cryptography.hazmat.primitives.asymmetric import rsa
    forged = sso_mock_jwt().encode({"iss": "https://sso.example", "aud": "kbase", "sub": "E1", "exp": 9999999999},
                                   rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm="RS256")
    with pytest.raises(sso.SSOError):
        sso.verify(forged)                                                  # 别人的私钥签的


def sso_mock_jwt():
    import jwt
    return jwt


def test_logout_token_must_carry_event(idp):
    sso, sign = idp
    ok = sign(sub="E1", jti="j1", events={sso.BACKCHANNEL_EVENT: {}})
    assert sso.verify_logout_token(ok)["sub"] == "E1"
    for bad in (sign(sub="E1"), sign(sub="E1", nonce="n", events={sso.BACKCHANNEL_EVENT: {}})):
        with pytest.raises(sso.SSOError):
            sso.verify_logout_token(bad)


def test_pkce_challenge_matches_verifier():
    import base64
    import hashlib
    from app.kbase.auth import sso
    v, c = sso.pkce()
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode() and len(v) >= 43


def test_intranet_only_and_forwarded_for():
    from app.kbase.auth import network

    def req(peer, xff=""):
        return types.SimpleNamespace(client=types.SimpleNamespace(host=peer), headers={"X-Forwarded-For": xff} if xff else {})
    assert network.client_ip(req("10.8.3.4")) == "10.8.3.4"
    assert network.client_ip(req("127.0.0.1", "10.66.1.20")) == "10.66.1.20"                 # 经过 Nginx：采信它写的来源地址
    assert network.client_ip(req("8.8.8.8", "10.0.0.1")) == "8.8.8.8"                       # 不是受信任的代理：请求头不算数
    assert network.client_ip(req("127.0.0.1", "10.0.0.1, 8.8.4.4")) == "8.8.4.4"            # 客户端伪造的前缀不算数
    assert network._in("10.66.1.20", network.ALLOWED) and not network._in("8.8.4.4", network.ALLOWED)


# ---------- 通知事件 ----------

def test_version_switch_event_renders_diff():
    to, subj, body = events.render({"kind": "VERSION_SWITCHED", "doc_key": "D1", "version": "2",
                                    "payload": {"title": "规格书", "owner_email": "a@x", "old": "1", "diff": {"new": 3, "reused": 40, "vanished": 2}}})
    assert to == "a@x" and "v2" in subj and "新增 3" in body and "沿用 40" in body and "失效 2" in body


def test_numbers_from_the_question_do_not_fail_citation_check():
    src = [{"index": 1, "context": "50 万（含）至 200 万元加总经理审批"}]
    assert not answer.verify("80 万元的订单要总经理审批 [1]", src)[0]
    assert answer.verify("80 万元的订单要总经理审批 [1]", src, "一张 80 万元的采购订单需要谁审批？")[0]
