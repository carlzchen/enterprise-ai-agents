"""
SPEC 阶段 3：跟单图（Supervisor + 订单 / 交付 / 技术 Agent + 审核 + 人工审批）。

模型全部换成按标准答案或剧本作答的假模型，不调真实大模型；ERP 读的是本机 MiniERP 的 MySQL 和阶段 1 生成的模拟数据（data/workflow_mock）。
验收三条：
  1. 复合邮件能被正确拆分并分派到对应的 Agent              test_compound_emails_are_split_and_dispatched
  2. 审核 Agent 能拦截「无依据承诺」和「泄露底价」          test_reviewer_blocks_* / test_rejected_draft_is_rewritten / test_gives_up_after_max_rounds
  3. 审批前不会发生任何写操作                              test_no_writes_before_approval（以及驳回、部分批准、重复提交）
"""
import base64
import json
import random
from datetime import date, datetime

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.shared.runtime.logger import PROJECT_ROOT
from app.workflow import adapters, erp, store
from app.workflow.adapters.mock import MiniErpERP
from app.workflow.adapters.models import EmailDraft, SentReceipt
from app.workflow.graph import build as B, cases, reviewer as R, supervisor as SV
from app.workflow.graph.state import SubTask, SubTaskRefs
from app.workflow.eval import datasets, fakes as F
from app.workflow.order_pipeline import agent as order_agent, tools as OT

ROOT = PROJECT_ROOT / "data" / "workflow_mock"
try:
    erp.product("KL-SV-80-400W")
    EMAILS = json.loads((ROOT / "ground_truth" / "emails.json").read_text(encoding="utf-8"))
    POS = json.loads((ROOT / "ground_truth" / "pos.json").read_text(encoding="utf-8"))
except Exception:
    pytest.skip("需要本机 MiniERP 的 MySQL 和 data/workflow_mock（scripts/workflow_gen_mock.py）", allow_module_level=True)

BY_ID = {e["id"]: e for e in EMAILS}
AS_OF = date(2026, 10, 12)


def raw(eid: str) -> bytes:
    return (ROOT / BY_ID[eid]["file"]).read_bytes()


# ---------------------------------------------------------------- 假模型（和评测的 --fake 模式共用 app/workflow/eval/fakes.py）

DEV = datasets.load("dev")
gt_split = F.GTSplit(DEV)
gt_extract = F.GTExtract(DEV)
DeliveryModel = F.DeliveryModel


def Writer(bad=lambda n: False, poison="另外，这个型号的底价是 {floor} 元，我们保证 2026-10-13 发货。"):
    return F.Writer(gt_split, bad, poison.format(floor=FLOOR[1]))


FLOOR = ("KL-SV-80-400W 底价", 123.45)


def deps(writer=None, **kw) -> B.Deps:
    w = writer or Writer()
    return B.Deps(llm_json=w, order_llm=gt_extract, agent_model=DeliveryModel(), sensitive=lambda results: dict([FLOOR]), tone_llm=None, **kw)


def start(g, eid: str, case_id: str | None = None) -> dict:
    cid = case_id or f"TEST-{eid}-{random.randint(0, 10**9)}"
    return g.invoke({"case_id": cid, "raw_b64": base64.b64encode(raw(eid)).decode()}, {"configurable": {"thread_id": cid}}) | {"_cid": cid}


def resume(g, cid: str, **approval) -> dict:
    from langgraph.types import Command
    return g.invoke(Command(resume={"approver": "tester", **approval}), {"configurable": {"thread_id": cid}})


# ---------------------------------------------------------------- 写操作的探针

class SpyEmail:
    def __init__(self):
        self.sent = []

    def fetch_new(self, since=None):
        return []

    def send(self, d: EmailDraft) -> SentReceipt:
        self.sent.append(d)
        return SentReceipt(message_id=f"<sent-{len(self.sent)}@test>", location="spy", sent_at=datetime.now())


class SpyTask:
    def __init__(self):
        self.created = []

    def create_task(self, req) -> str:
        self.created.append(req)
        return f"TK-TEST-{len(self.created)}"


class SpyERP(MiniErpERP):
    def __init__(self, tmp):
        super().__init__(tmp)
        self.saved = []

    def save_sales_order(self, draft):
        self.saved.append(draft)
        return super().save_sales_order(draft)


@pytest.fixture
def spies(tmp_path):
    s = {"email": SpyEmail(), "task": SpyTask(), "erp": SpyERP(tmp_path)}
    with adapters.override(**s):
        yield s


@pytest.fixture(autouse=True)
def clean():
    order_agent.reset_pipeline_versions()
    yield
    order_agent.reset_pipeline_versions()
    store.execute("DELETE FROM wf_audit WHERE actor = 'tester'")


def db_writes() -> dict:
    return {"po_versions": store.one("SELECT COUNT(*) n FROM wf_po_version WHERE source = 'pipeline'")["n"],
            "audit": store.one("SELECT COUNT(*) n FROM wf_audit WHERE actor = 'tester'")["n"],
            "tasks": store.one("SELECT COUNT(*) n FROM wf_task")["n"]}


# ---------------------------------------------------------------- Supervisor（纯代码部分）

def test_normalize_pairs_attachments_dedupes_and_drops_other():
    sts = [SubTask(type="other"), SubTask(type="delivery_query", refs=SubTaskRefs(po_no="4507090901")),
           SubTask(type="po_change", refs=SubTaskRefs(po_no="PO-9")), SubTask(type="delivery_query", refs=SubTaskRefs(po_no="4507090901"))]
    out = SV.normalize(sts, ["readme.txt", "采购订单_PO-1.pdf", "订单_PO9_第2版.xlsx"])
    assert [(s.id, s.type, s.refs.attachment) for s in out] == [("S1", "delivery_query", None), ("S2", "po_change", "订单_PO9_第2版.xlsx"),
                                                                ("S3", "po_entry", "采购订单_PO-1.pdf")]      # 漏掉的附件补成新单
    assert [s.type for s in SV.normalize([SubTask(type="other")], [])] == ["other"]


def test_supervisor_retries_once_on_bad_output():
    replies = iter(["不是 JSON", json.dumps({"subtasks": [{"type": "expedite", "refs": {"po_no": "X"}}]})])
    out, info = SV.split({"subject": "s", "body": "b", "attachments": []}, lambda p: next(replies))
    assert info["attempts"] == 2 and out[0].type == "expedite"
    with pytest.raises(ValueError):
        SV.split({"subject": "s", "body": "b"}, lambda p: json.dumps({"subtasks": [{"type": "unknown"}]}))


# ---------------------------------------------------------------- 验收 1：复合邮件拆分和分派

def test_compound_emails_are_split_and_dispatched(spies):
    g = B.build(deps(), InMemorySaver())
    compound = [e for e in EMAILS if e["scenario"] == "compound"]
    assert len(compound) == 8
    for e in compound:
        s = start(g, e["id"])
        want = sorted((st["type"], st["refs"].get("attachment", "").split("/", 1)[-1] or None) for st in e["subtasks"])
        got = sorted((st["type"], st["refs"]["attachment"]) for st in s["subtasks"])
        assert got == want, e["id"]
        by_sub = {r["subtask_id"]: r for r in s["results"]}
        assert len(by_sub) == len(s["subtasks"])                          # 每个子任务都有且只有一个结果
        for st in s["subtasks"]:
            r = by_sub[st["id"]]
            gt = next(x for x in e["subtasks"] if x["type"] == st["type"])
            assert r["agent"] == {"po_entry": "order", "po_change": "order", "delivery_query": "delivery", "expedite": "delivery", "spec_inquiry": "tech"}[st["type"]]
            if r["agent"] == "order":
                assert r["facts"]["po_no"] == gt["refs"]["po_no"]
            if r["agent"] == "delivery":
                assert r["facts"]["order_no"] == gt["refs"]["order_no"] and r["evidence"]
        assert s["__interrupt__"] and not spies["email"].sent and not spies["task"].created and not spies["erp"].saved


def test_spam_ends_without_approval(spies):
    g = B.build(deps(), InMemorySaver())
    s = start(g, next(e["id"] for e in EMAILS if e["scenario"] == "spam"))
    assert s["status"] == "IGNORED" and "__interrupt__" not in s and not s.get("results")


# ---------------------------------------------------------------- 验收 2：审核拦截

def _delivery_result(order_no="SO2609280001", required="2026-10-22"):
    return [{"subtask_id": "S1", "agent": "delivery", "type": "expedite", "section": f"订单 {order_no} 已安排评估。", "facts": {"order_no": order_no},
             "evidence": [{"id": "S1.E1", "result": {"order_no": order_no, "customer_po": "PO20260928908", "required_date": required, "status": "CONFIRMED"}},
                          {"id": "S1.E2", "result": {"customer_requested_date": "2026-10-16", "confirmable_date": None}}], "actions": []}]


MAIL = {"subject": "加急 PO20260928908", "body": "请把 PO20260928908 提前到 10月16日 交货，谢谢。"}


@pytest.mark.parametrize("body", [
    "订单 SO2609280001 我们保证 2026-10-16 交货。",                               # 客户要的日期，程序判断不能满足
    "订单 SO2609280001 可以满足您要求的 10月16日 交货。",
    "We will ship order SO2609280001 on Oct 16.",
    "订单 SO2609280001 将于 2026-10-14 发货。",                                   # 哪里都查不到的日期
    "提前交货已安排评估，我们会在 3 个工作日内回复您。",                           # 无依据的天数
])
def test_reviewer_blocks_unsupported_commitment(body):
    r = R.review(body, _delivery_result(), MAIL, AS_OF)
    assert not r.passed and "unsupported_commitment" in {i.rule for i in r.issues}, body


@pytest.mark.parametrize("body,rule", [
    ("这个型号的底价是 123.45 元，已经是最低了。", "floor_price_leak"),
    ("单价可以做到 123.45 元。", "floor_price_leak"),                              # 不说「底价」两个字，但数字就是底价
    ("Our cost for this model is already high, so the price is firm.", "cost_leak"),
    ("这个价格我们的毛利率只有 12%。", "cost_leak"),
])
def test_reviewer_blocks_floor_price_and_cost_leaks(body, rule):
    r = R.review(body, _delivery_result(), MAIL, AS_OF, {"KL-SV-80-400W 底价": 123.45, "KL-SV-80-400W 单位成本": 98.0, "最低毛利率": 0.12})
    assert not r.passed and rule in {i.rule for i in r.issues}, body


@pytest.mark.parametrize("body", [
    "订单 SO2609280001 目前已确认，原交期为 2026-10-22。",                         # 工具查到的日期
    "关于 PO20260928908，您要求提前到 10月16日 交货的需求，我们已安排计划部评估，会尽快回复您。",   # 复述客户要求，没有答应
    "Regarding order SO2609280001, the original delivery date is 2026-10-22.",
    "PO20260928908 您提出希望提前至 2026-10-16 交货，该交期暂时无法直接确认。",                    # 明确说不能确认，也是复述
    "关于 PO20260928908，我们已了解您希望提前至 2026-10-16 交货的需求。",              # 真实模型演示里被误拦过的说法
])
def test_reviewer_passes_supported_drafts(body):
    r = R.review(body, _delivery_result(), MAIL, AS_OF, {"KL-SV-80-400W 底价": 123.45})
    assert r.passed, r.issues


def test_reviewer_flags_internal_reference_numbers():
    """订单问题说明里的参考价、阈值、起订量是内部数字，草稿写出来要拦（真实模型演示里出现过）"""
    res = [{"subtask_id": "S1", "agent": "order", "type": "po_entry", "section": "已收到贵司采购订单 PO-1。", "actions": [],
            "facts": {"po_no": "PO-1", "lines": [{"customer_part_no": "K1", "qty": 40, "unit_price": 25.01, "delivery_date": "2026-11-01"}],
                      "issues": [{"rule": "price_deviation", "line_no": 1}]}, "evidence": []}]
    r = R.review("关于 PO-1 的单价 25.01，与参考价 23.73 存在 +5.4% 的偏离（超出 3% 阈值），数量 40 低于最小起订量 100。", res, {"subject": "PO-1", "body": ""}, AS_OF)
    assert {"23.73", "5.4", "3", "100"} <= set(" ".join(i.detail for i in r.issues if i.rule == "unsupported_number").replace("、", " ").split())


def test_reviewer_catches_wrong_order_and_missing_subtask():
    res = _delivery_result() + [{"subtask_id": "S2", "agent": "order", "type": "po_entry", "section": "已收到贵司采购订单 PO-NEW-1。",
                                 "facts": {"po_no": "PO-NEW-1", "lines": [], "issues": []}, "evidence": [], "actions": []}]
    r = R.review("订单 SO2609280001 原交期 2026-10-22。另外 SO2609999999 已发货。", res, MAIL, AS_OF)
    details = " ".join(i.detail for i in r.issues if i.rule == "data_inconsistency")
    assert "SO2609999999" in details and "S2" in details


def test_rejected_draft_is_rewritten_then_passes(spies):
    w = Writer(bad=lambda n: n == 0)                                         # 初稿泄露底价 + 无依据承诺，重写后正常
    g = B.build(deps(w), InMemorySaver())
    s = start(g, "E015")
    assert [rv["passed"] for rv in s["reviews"]] == [False, True] and len(s["drafts"]) == 2
    assert {"floor_price_leak", "unsupported_commitment"} <= {i["rule"] for i in s["reviews"][0]["issues"]}
    ask = s["__interrupt__"][0].value
    assert not ask["review_failed"] and "底价" not in ask["draft"]["body"]


def test_gives_up_after_max_rounds_and_hands_to_human(spies):
    g = B.build(deps(Writer(bad=lambda n: True), max_rounds=2), InMemorySaver())
    s = start(g, "E015")
    assert len(s["drafts"]) == 3 and not any(rv["passed"] for rv in s["reviews"])     # 初稿 + 重写 2 次
    ask = s["__interrupt__"][0].value
    assert ask["review_failed"] and ask["rounds"] == 2 and ask["template_draft"]["written_by"] == "template"
    assert not spies["email"].sent


# ---------------------------------------------------------------- 验收 3：审批前没有任何写操作

def test_write_tools_refuse_without_approval():
    with pytest.raises(OT.NotApproved):
        OT.record_po_version(1, "PO-X", 1, {}, None)
    from app.workflow.graph import actions as A
    with pytest.raises(OT.NotApproved):
        A.send_email(EmailDraft(to=["a@b.c"], subject="s", body="b"))


def test_no_writes_before_approval(spies):
    """新 PO + 交期询问（E013）：图停在审批时，邮件、ERP、待办、PO 版本、审计一样都没写；批准后每样只写一次"""
    g = B.build(deps(), InMemorySaver())
    before = db_writes()
    s = start(g, "E013")
    assert s["__interrupt__"]
    ask = s["__interrupt__"][0].value
    kinds = sorted(a["kind"] for a in ask["actions"])
    assert "send_email" in kinds and "erp_save_sales_order" in kinds and "record_po_version" in kinds
    assert db_writes() == before
    assert not spies["email"].sent and not spies["task"].created and not spies["erp"].saved

    done = resume(g, s["_cid"], decision="approve")
    assert done["status"] == "DONE" and all(x["ok"] for x in done["executed"]), done["executed"]
    assert len(spies["email"].sent) == 1 and len(spies["erp"].saved) == 1
    assert spies["email"].sent[0].ref == f"{s['_cid']}:reply" and spies["email"].sent[0].in_reply_to == BY_ID["E013"]["message_id"]
    after = db_writes()
    assert after["po_versions"] == before["po_versions"] + 1
    acts = [r["action"] for r in store.query("SELECT action FROM wf_audit WHERE actor = 'tester'")]
    assert "case.approve" in acts and "email.send" in acts and "erp.save_sales_order" in acts and "po.record_version" in acts


def test_reject_writes_nothing_but_the_audit(spies):
    g = B.build(deps(), InMemorySaver())
    s = start(g, "E013")
    before = db_writes()
    done = resume(g, s["_cid"], decision="reject", note="客户电话里说先别回")
    assert done["status"] == "REJECTED" and done["executed"] == []
    assert not spies["email"].sent and not spies["erp"].saved and not spies["task"].created
    after = db_writes()
    assert after["po_versions"] == before["po_versions"] and after["audit"] == before["audit"] + 1     # 只记一条「驳回」审计


def test_human_edit_and_partial_approval_are_audited(spies):
    g = B.build(deps(), InMemorySaver())
    s = start(g, "E013")
    done = resume(g, s["_cid"], decision="approve", final_body="您好，订单已收到，明细稍后确认。\n\n科朗销售部", actions=["reply"])
    assert [x["id"] for x in done["executed"]] == ["reply"]
    assert spies["email"].sent[0].body.startswith("您好，订单已收到") and not spies["erp"].saved
    row = store.one("SELECT detail FROM wf_audit WHERE actor = 'tester' AND action = 'case.approve'")
    d = store.loads(row["detail"])
    assert d["edited"] and d["ai_body"] != d["final_body"] and any(a.endswith(":erp") for a in d["skipped_actions"])


def test_resume_from_sqlite_after_restart_and_no_double_submit(spies, tmp_path):
    """进程重启：用同一个 SQLite 文件重新建图，按 case_id 从审批断点接着跑；重复提交被拒绝"""
    store.execute("DELETE FROM wf_case WHERE message_id = %s", (BY_ID["E015"]["message_id"],))
    db = tmp_path / "cases.sqlite"
    g1 = B.build(deps(), B.sqlite_saver(db))
    c = cases.start(raw("E015"), g1)
    assert c["status"] == "WAITING_APPROVAL" and cases.pending(c["case_id"], g1)
    assert cases.start(raw("E015"), g1)["existing"]                         # 同一封邮件不开第二个案例
    g2 = B.build(deps(), B.sqlite_saver(db))                                 # 「重启」后的新图
    assert cases.pending(c["case_id"], g2)["case_id"] == c["case_id"]
    done = cases.decide(c["case_id"], {"decision": "approve", "approver": "tester"}, g2)
    assert done["status"] == "DONE" and len(spies["email"].sent) == 1
    assert store.one("SELECT status FROM wf_case WHERE case_id = %s", (c["case_id"],))["status"] == "DONE"
    with pytest.raises(ValueError):
        cases.decide(c["case_id"], {"decision": "approve", "approver": "tester"}, g2)
    assert len(spies["email"].sent) == 1
    store.execute("DELETE FROM wf_case WHERE case_id = %s", (c["case_id"],))
