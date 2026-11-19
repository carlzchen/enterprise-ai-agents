"""
SPEC 阶段 1：模拟数据的完整性和可复现。需要本机 MiniERP 的 MySQL（数据从 ERP 读、补充数据写 wf_ 表）。
生成到临时目录，不动 data/workflow_mock。
"""
import io
import json
from collections import Counter
from datetime import date

import pdfplumber
import pytest
from openpyxl import load_workbook

from app.workflow import erp, mailbox, store
from app.workflow.mockdata import emails as G, world

try:
    erp.product("KL-SV-80-400W")
except Exception:
    pytest.skip("需要本机 MiniERP 的 MySQL", allow_module_level=True)

SCENARIOS = {"new_po": 24, "po_change": 8, "duplicate": 2, "delivery_query": 8, "expedite": 6, "spec_inquiry": 8, "compound": 8, "spam": 6}


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    w = world.load(42)
    world.seed_db(w)
    out = tmp_path_factory.mktemp("mock")
    manifest = G.generate(w, 42, out)
    em = json.loads((out / "ground_truth" / "emails.json").read_text(encoding="utf-8"))
    pos = json.loads((out / "ground_truth" / "pos.json").read_text(encoding="utf-8"))
    return {"w": w, "out": out, "manifest": manifest, "emails": em, "pos": pos}


def test_same_seed_gives_byte_identical_files(gen, tmp_path):
    again = G.generate(world.load(42), 42, tmp_path)
    assert again["files"] == gen["manifest"]["files"]                        # 每个文件的 sha256 都一样
    other = G.generate(world.load(7), 7, tmp_path / "s7")
    assert other["files"] != gen["manifest"]["files"]


def test_scenario_mix(gen):
    assert Counter(e["scenario"] for e in gen["emails"]) == SCENARIOS
    assert gen["manifest"]["layouts"]["A"] and gen["manifest"]["layouts"]["B"] and gen["manifest"]["layouts"]["C"] and gen["manifest"]["layouts"]["D"]
    for e in gen["emails"]:
        if e["scenario"] == "compound":
            assert len({s["type"] for s in e["subtasks"]}) == 2
        if e["scenario"] == "spam":
            assert e["customer_id"] is None and e["intents"] == ["other"]
    langs = Counter(e["lang"] for e in gen["emails"] if e["customer_id"])
    assert langs["zh"] and langs["en"]


def test_every_email_parses_and_matches_ground_truth(gen):
    ids, dates = set(), []
    for e in gen["emails"]:
        m = mailbox.parse((gen["out"] / e["file"]).read_bytes())
        assert m["message_id"] == e["message_id"] and m["message_id"] not in ids
        ids.add(m["message_id"])
        assert sorted(a["name"] for a in m["attachments"]) == sorted(k.split("/", 1)[1] for k in e["attachments"])
        assert all(a["parsed"] for a in m["attachments"])                    # 现有的附件转文字能读出内容
        dates.append(m["received_at"])
    assert dates == sorted(dates)


def test_sender_is_identified_as_the_ground_truth_customer(gen):
    for e in gen["emails"]:
        who = mailbox.identify(e["from"])
        assert who["id"] == e["customer_id"], (e["id"], e["from"], who)


def test_every_po_document_has_ground_truth_and_is_readable(gen):
    files = {str(p.relative_to(gen["out"] / "attachments")) for p in (gen["out"] / "attachments").rglob("*") if p.is_file()}
    assert files == set(gen["pos"])                                          # 每个附件都有标准答案，反之亦然
    for key, po in gen["pos"].items():
        data = (gen["out"] / "attachments" / key).read_bytes()
        if key.endswith(".pdf"):
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        else:
            wb = load_workbook(io.BytesIO(data), data_only=True)
            text = "\n".join(str(c) for ws in wb.worksheets for row in ws.iter_rows(values_only=True) for c in row if c is not None)
        assert po["po_no"] in text
        for ln in po["lines"]:
            assert ln["customer_part_no"] in text


def test_ground_truth_orders_are_consistent(gen):
    w = gen["w"]
    for key, po in gen["pos"].items():
        assert po["customer_code"] == w.customers[po["customer_id"]].code
        assert abs(sum(ln["amount"] for ln in po["lines"]) - po["computed_total"]) < 0.01
        exp = po["expected_order"]
        assert [ln["material"] for ln in exp["lines"]] == [ln["expected_material"] for ln in po["lines"]]
        for ln in exp["lines"]:
            assert ln["material"] in w.products and ln["qty"] > 0 and ln["unit_price"] > 0
        rules = {f["rule"] for f in po["flags"]}
        unknown_lines = {f["line_no"] for f in po["flags"] if f["rule"] == "unknown_part"}
        assert not any(f["line_no"] in unknown_lines and f["rule"] != "unknown_part" for f in po["flags"])   # 料号没对上的行不做依赖物料的检查
        if not unknown_lines:
            assert set(po["injected"]) - {"pack_multiple"} <= rules         # 故意埋的问题都在标准答案里（包装倍数为 1 的型号埋不了）
        assert ("duplicate_po" in rules) == bool(po["duplicate_of"])
        if po["change"]:                                                     # 改了又取消的行只算取消（留出集上发现过）
            assert not {c["line_no"] for c in po["change"]["changes"]} & set(po["change"]["cancelled_lines"]), key
        for ln in po["lines"]:
            known = (po["customer_id"], ln["expected_material"]) in w.part_map and w.part_map[(po["customer_id"], ln["expected_material"])] == ln["customer_part_no"]
            assert known != any(f["rule"] == "unknown_part" and f["line_no"] == ln["line_no"] for f in po["flags"])


def test_po_changes_point_to_an_earlier_version(gen):
    by_email = {e["id"]: e for e in gen["emails"]}
    seen = {}
    for e in gen["emails"]:
        for st in e["subtasks"]:
            if st["type"] in ("po_entry", "po_change"):
                po = gen["pos"][st["refs"]["attachment"]]
                if st["type"] == "po_change":
                    base = seen.get((po["customer_id"], po["po_no"], st["refs"]["base_version"]))
                    seeded = store.one("SELECT 1 FROM wf_po_version WHERE customer_id = %s AND po_no = %s AND version = 1 AND source = 'mock'",
                                       (po["customer_id"], po["po_no"]))
                    assert base or (st["refs"]["base_version"] == 1 and seeded), (e["id"], po["po_no"])
                    if base:
                        assert by_email[base]["date"] < e["date"]
                    assert po["version"] == st["refs"]["base_version"] + 1 and po["change"]
                seen[(po["customer_id"], po["po_no"], po["version"])] = e["id"]


def test_order_references_exist_in_erp(gen):
    refs = {r["order_no"]: r for r in store.query("SELECT order_no, customer_id, customer_po FROM wf_order_po_ref WHERE source = 'mock'")}
    for e in gen["emails"]:
        for st in e["subtasks"]:
            if st["type"] in ("delivery_query", "expedite"):
                r = refs[st["refs"]["order_no"]]
                assert r["customer_po"] == st["refs"]["customer_po"] and r["customer_id"] == e["customer_id"]
                o = erp.order_status(r["order_no"])
                assert o and o["customer_id"] == e["customer_id"]
                if st["type"] == "expedite":
                    assert date.fromisoformat(st["refs"]["requested_date"]) < date.fromisoformat(o["required_date"])
            if st["type"] == "spec_inquiry":
                m = st["refs"]["expected_material"]
                assert m is None or m in gen["w"].products
    assert sum(1 for e in gen["emails"] for st in e["subtasks"] if st["type"] == "spec_inquiry" and st["refs"]["expected_material"] is None) == 2


def test_seed_tables_cover_ground_truth_and_are_idempotent(gen):
    w = gen["w"]
    before = world.seed_db(w)
    assert world.seed_db(w) == before
    price = {(r["customer_id"], r["product_code"]) for r in store.query("SELECT customer_id, product_code FROM wf_price_list WHERE source = 'mock'")}
    ext = {r["product_code"] for r in store.query("SELECT product_code FROM wf_material_ext")}
    for po in gen["pos"].values():
        for ln in po["lines"]:
            assert ln["expected_material"] in ext
            assert (po["customer_id"], ln["expected_material"]) in price or po["change"] or po.get("duplicate_of")


def test_customer_emails_never_mention_our_internal_model_codes(gen):
    """客户只知道自己的料号：交期询问、加急的正文里不能出现我方型号（规格询价里是推荐对象，不算）"""
    codes = set(gen["w"].products)
    for e in gen["emails"]:
        if {"delivery_query", "expedite"} & set(e["intents"]) and "spec_inquiry" not in e["intents"]:
            body = mailbox.parse((gen["out"] / e["file"]).read_bytes())["body"]
            assert not any(c in body for c in codes), e["id"]


def test_one_email_belongs_to_one_customer(gen):
    """一封邮件里的所有子任务（PO、订单引用）都属于发件的那个客户"""
    for e in gen["emails"]:
        for st in e["subtasks"]:
            if "attachment" in st["refs"]:
                assert gen["pos"][st["refs"]["attachment"]]["customer_id"] == e["customer_id"], e["id"]
