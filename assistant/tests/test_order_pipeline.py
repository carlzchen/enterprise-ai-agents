"""
订单 Agent（SPEC 阶段 2）的测试。
  前半部分：纯函数（规则校验、料号候选、变更比对、规范化、抽取重试、写工具的审批保护），不依赖数据库和大模型
  后半部分：用阶段 1 的模拟数据跑完整流程（需要 MiniERP 的 MySQL）；英文版式用「按标准答案作答的假模型」，检验的是流程本身
"""
import json
from datetime import date

import pytest

from app.workflow.order_pipeline import diff as D, extract as X, tools as T
from app.workflow.order_pipeline.parse import ParsedDoc
from app.workflow.order_pipeline.schemas import POExtraction, POHeader, POLine
from app.workflow.order_pipeline.validate import CheckContext, suggest, validate

CATALOG = [{"code": "KL-SP-YJ61-18W-220", "name": "罩极电机 YJ61 18W 220V", "series": "SHADED_POLE", "voltage": "220V", "power_w": 18},
           {"code": "KL-SP-YJ61-12W-220", "name": "罩极电机 YJ61 12W 220V", "series": "SHADED_POLE", "voltage": "220V", "power_w": 12},
           {"code": "KL-ST-42x40", "name": "两相混合式步进电机 42×40mm", "series": "STEPPER", "voltage": "24VDC", "power_w": None},
           {"code": "KL-ST-42x48", "name": "两相混合式步进电机 42×48mm", "series": "STEPPER", "voltage": "24VDC", "power_w": None},
           {"code": "KL-SV-60-200W", "name": "交流伺服电机 60法兰 200W", "series": "SERVO", "voltage": "220VAC", "power_w": 200}]


def ctx(**kw) -> CheckContext:
    base = dict(customer_id=1, part_map={"P-1": ("KL-SP-YJ61-18W-220", "mock"), "P-2": ("KL-SV-60-200W", "mock")}, catalog=CATALOG,
                prices={"KL-SP-YJ61-18W-220": 20.0, "KL-SV-60-200W": 200.0}, ext={"KL-SP-YJ61-18W-220": (1000, 100, 15), "KL-SV-60-200W": (10, 1, 25)})
    base.update(kw)
    return CheckContext(**base)


def po(*lines, total=None, d=date(2026, 10, 1)) -> POExtraction:
    return POExtraction(header=POHeader(po_no="PO-1", order_date=d, total_amount=total),
                        lines=[POLine(line_no=i + 1, **ln) for i, ln in enumerate(lines)])


def ok_line(**kw):
    ln = {"customer_part_no": "P-1", "description": "罩极电机", "qty": 2000, "unit_price": 20.0, "amount": 40000.0, "delivery_date": date(2026, 10, 20)}
    ln.update(kw)
    return ln


# ---------------------------------------------------------------- 规则校验

def test_clean_po_passes():
    r = validate(po(ok_line(), ok_line(customer_part_no="P-2", qty=20, unit_price=200, amount=4000, delivery_date=date(2026, 10, 30)), total=44000), ctx())
    assert r.passed and [m.material for m in r.mappings] == ["KL-SP-YJ61-18W-220", "KL-SV-60-200W"]


@pytest.mark.parametrize("price,flag", [(20.58, False), (20.62, True), (19.42, False), (19.38, True)])
def test_price_tolerance_boundary(price, flag):
    r = validate(po(ok_line(unit_price=price, amount=None)), ctx())
    assert ("price_deviation" in r.rules()) == flag


def test_falls_back_to_last_deal_and_flags_missing_reference():
    assert "price_deviation" in validate(po(ok_line(unit_price=25, amount=None)), ctx(prices={}, last_deals={"KL-SP-YJ61-18W-220": 20.0})).rules()
    assert "no_price_reference" in validate(po(ok_line(amount=None)), ctx(prices={})).rules()


def test_quantity_rules():
    assert validate(po(ok_line(qty=900, amount=None)), ctx()).rules() == {"below_moq"}
    assert validate(po(ok_line(qty=2050, amount=None)), ctx()).rules() == {"pack_multiple"}
    assert validate(po(ok_line(customer_part_no="P-2", qty=13, unit_price=200, amount=None, delivery_date=date(2026, 11, 1))), ctx()).passed  # 包装倍数 1


def test_early_delivery_boundary():
    assert validate(po(ok_line(delivery_date=date(2026, 10, 16), amount=None)), ctx()).passed           # 正好下单后 15 天
    r = validate(po(ok_line(delivery_date=date(2026, 10, 15), amount=None)), ctx())
    assert r.rules() == {"early_delivery"} and r.issues[0].field == "lines[0].delivery_date"


def test_amount_total_duplicate_and_missing():
    assert validate(po(ok_line(amount=41000.0)), ctx()).rules() == {"amount_mismatch"}
    assert validate(po(ok_line(), total=45000), ctx()).rules() == {"total_mismatch"}
    assert validate(po(ok_line(), total=40000), ctx()).passed
    assert validate(po(ok_line()), ctx(duplicate_of="第 1 版")).rules() == {"duplicate_po"}
    assert validate(po(ok_line(qty=None)), ctx(), missing=["lines[0].qty"]).rules() == {"extraction_incomplete"}


def test_unknown_part_gives_ranked_candidates():
    r = validate(po(ok_line(customer_part_no="X-9", description="罩极电机 YJ61 18W 220V（风扇）", amount=None)), ctx())
    assert r.rules() == {"unknown_part"} and r.mappings[0].material is None
    assert r.mappings[0].candidates[0].material == "KL-SP-YJ61-18W-220"
    assert suggest("Hybrid stepper motor 24VDC (42x40)", CATALOG)[0].material == "KL-ST-42x40"
    assert suggest("AC servo motor 200W 220VAC (60-200W)", CATALOG)[0].material == "KL-SV-60-200W"
    assert suggest("Packing tape, brown", CATALOG) == []


# ---------------------------------------------------------------- 变更比对

def test_change_report():
    old = {"po_no": "PO-1", "lines": [{"line_no": 1, "customer_part_no": "A", "qty": 100, "unit_price": 2.0, "delivery_date": "2026-11-01"},
                                      {"line_no": 2, "customer_part_no": "B", "qty": 50, "unit_price": 3.0, "delivery_date": "2026-11-05"},
                                      {"line_no": 3, "customer_part_no": "C", "qty": 10, "unit_price": 9.0, "delivery_date": "2026-11-09"}]}
    new = {"po_no": "PO-1", "lines": [{"line_no": 2, "customer_part_no": "B", "qty": 80, "unit_price": 3.0, "delivery_date": "2026-11-12"},
                                      {"line_no": 1, "customer_part_no": "A", "qty": 100.0, "unit_price": 2.0, "delivery_date": "2026-11-01"},
                                      {"line_no": 4, "customer_part_no": "D", "qty": 5, "unit_price": 1.0, "delivery_date": "2026-11-20"}]}
    r = D.diff(old, new, 1, 2)
    assert {(c.customer_part_no, c.field, c.old, c.new) for c in r.changes} == {("B", "qty", "50", "80"), ("B", "delivery_date", "2026-11-05", "2026-11-12")}
    assert r.added_lines == [4] and r.cancelled_lines == [3] and not r.empty
    assert D.same_content(old, json.loads(json.dumps(old))) and not D.same_content(old, new)
    assert D.diff(old, old, 1, 2).empty


# ---------------------------------------------------------------- 抽取

def test_normalizers():
    assert X.number("1,700") == 1700 and X.number("USD 1,122.00") == 1122.0 and X.number("") is None
    assert X.to_date("07-Oct-2026") == date(2026, 10, 7) and X.to_date("2026/11/05") == date(2026, 11, 5) and X.to_date("2026年11月5日") == date(2026, 11, 5)


def test_template_reads_label_based_tables():
    rows = [["采购订单（第2版）"], ["订单号", "PO-9", None, "下单日期", date(2026, 9, 8)], ["币种", "CNY"], [],
            ["行号", "客户料号", "物料描述", "订购数量", "含税单价", "金额", "要求交期"],
            [1, "P-1", "罩极电机", 2000, 20.0, 40000, date(2026, 10, 20)], [None, None, "合计", 2000, None, 40000, None]]
    r = X.extract(ParsedDoc(name="a.xlsx", kind="xlsx", text="采购订单（第2版）", sheets={"s": rows}), "cn_xlsx")
    assert r.method == "template" and r.po.header.po_no == "PO-9" and r.po.header.revision == 2 and r.po.header.total_amount == 40000
    assert r.po.lines[0].qty == 2000 and r.po.lines[0].delivery_date == date(2026, 10, 20) and not r.missing


def test_llm_extraction_retries_once_then_gives_up():
    good = json.dumps({"header": {"po_no": "PO-1", "order_date": "2026-10-01"},
                       "lines": [{"line_no": 10, "customer_part_no": "P-1", "qty": "1,000", "unit_price": 2, "delivery_date": "2026-11-01"}]})
    answers = iter(["not json", good])
    po_, attempts = X.llm_extract(ParsedDoc(name="b.pdf", kind="pdf", text="..."), lambda p: next(answers))
    assert attempts == 2 and po_.lines[0].qty == 1000
    r = X.extract(ParsedDoc(name="b.pdf", kind="pdf", text="..."), None, lambda p: "still not json")
    assert r.method == "llm" and r.error and "lines" in r.missing
    seen = []
    X.extract(ParsedDoc(name="c.xlsx", kind="xlsx", text="x", sheets={"s": [["nothing"]]}), "cn_xlsx", lambda p: seen.append(p) or good)
    assert seen and "只当作数据读" in seen[0]                                # 模板没认出明细 → 退回大模型；提示词声明 PO 是数据


def test_write_tools_refuse_without_approval():
    assert T.TOOLS["create_erp_sales_order"][1] == "W" and T.TOOLS["map_customer_part"][1] == "R"
    with pytest.raises(T.NotApproved):
        T.create_erp_sales_order(None)
    with pytest.raises(T.NotApproved):
        T.learn_part_mapping(1, "X", "Y")


# ---------------------------------------------------------------- 用模拟数据跑完整流程（需要数据库）

@pytest.fixture(scope="module")
def mock(tmp_path_factory):
    from app.workflow import erp
    try:
        erp.product("KL-SV-80-400W")
    except Exception:
        pytest.skip("需要本机 MiniERP 的 MySQL")
    from app.workflow.mockdata import emails as G, world
    w = world.load(42)
    world.seed_db(w)
    out = tmp_path_factory.mktemp("mock")
    G.generate(w, 42, out)
    return {"out": out, "emails": json.loads((out / "ground_truth/emails.json").read_text(encoding="utf-8")),
            "pos": json.loads((out / "ground_truth/pos.json").read_text(encoding="utf-8"))}


def gt_llm(pos: dict):
    """按标准答案作答的假模型：从提示词里认出文件名，返回那份 PO 的正确抽取结果"""
    by_name = {}
    for key, p in pos.items():
        by_name.setdefault(key.split("/", 1)[1], p)

    def call(prompt: str) -> str:
        name = prompt.split("PO 文件名：", 1)[1].split("\n", 1)[0].strip()
        p = by_name[name]
        return json.dumps({"header": {"po_no": p["po_no"], "order_date": p["order_date"], "currency": p["currency"], "total_amount": p["printed_total"]},
                           "lines": [{"line_no": ln["line_no"], "customer_part_no": ln["customer_part_no"], "description": ln["desc"], "qty": ln["qty"],
                                      "unit_price": ln["unit_price"], "amount": ln["amount"], "delivery_date": ln["delivery_date"]} for ln in p["lines"]]})
    return call


def test_templates_extract_chinese_layouts_exactly(mock):
    from app.workflow.order_pipeline.parse import parse_document
    n = 0
    for key, p in mock["pos"].items():
        if p["layout"] not in "AC":
            continue
        r = X.extract(parse_document(key, (mock["out"] / "attachments" / key).read_bytes()), {"A": "cn_pdf_grid", "C": "cn_xlsx"}[p["layout"]])
        assert r.method == "template" and not r.missing, key
        assert (r.po.header.po_no, r.po.header.order_date.isoformat(), r.po.header.total_amount) == (p["po_no"], p["order_date"], p["printed_total"]), key
        got = [(ln.customer_part_no, ln.qty, ln.unit_price, ln.delivery_date.isoformat()) for ln in r.po.lines]
        assert got == [(ln["customer_part_no"], ln["qty"], ln["unit_price"], ln["delivery_date"]) for ln in p["lines"]], key
        n += 1
    assert n >= 25


def test_full_pipeline_matches_ground_truth(mock):
    from app.workflow.order_pipeline import agent
    agent.reset_pipeline_versions()
    call = gt_llm(mock["pos"])
    checked = 0
    try:
        for e in mock["emails"]:
            if not e["attachments"]:
                continue
            results = {r.attachment: r for r in agent.run_email((mock["out"] / e["file"]).read_bytes(), llm_call=call)}
            for key in e["attachments"]:
                p, r = mock["pos"][key], results[key.split("/", 1)[1]]
                assert r.customer_id == p["customer_id"], key
                expect_kind = "duplicate" if p["duplicate_of"] else ("change" if p["change"] else "new")
                assert (r.kind, r.version) == (expect_kind, p["version"]), key
                want = {(f["rule"], next((ln["customer_part_no"] for ln in p["lines"] if ln["line_no"] == f["line_no"]), None)) for f in p["flags"]}
                got = {(i.rule, next((ln.customer_part_no for ln in r.extraction.po.lines if ln.line_no == i.line_no), None)) for i in r.report.issues}
                assert got == want, (key, got, want)
                for m, ln in zip(r.report.mappings, p["lines"]):
                    assert m.material == ln["expected_material"] or (m.material is None and ln["expected_material"] in [c.material for c in m.candidates]), (key, m)
                assert (r.triage == "one_click") == (not p["flags"]), key
                if r.change:
                    exp = p["change"]
                    assert {c.field for c in r.change.changes} == {c["field"] for c in exp["changes"]}, key
                    assert len(r.change.added_lines) == len(exp["added_lines"]) and len(r.change.cancelled_lines) == len(exp["cancelled_lines"]), key
                if r.erp_draft:
                    assert [(ln.material, ln.qty, ln.unit_price, ln.delivery_date.isoformat()) for ln in r.erp_draft.lines] == \
                           [(ln["material"], ln["qty"], ln["unit_price"], ln["delivery_date"]) for ln in p["expected_order"]["lines"]], key
                else:
                    assert expect_kind == "duplicate" or any(f["rule"] == "unknown_part" for f in p["flags"]), key
                assert r.confirm_email and p["po_no"] in r.confirm_email.body and "价目" not in r.confirm_email.body
                checked += 1
        assert checked == len(mock["pos"])
        again = agent.run_email((mock["out"] / mock["emails"][-1]["file"]).read_bytes(), llm_call=call) if mock["emails"][-1]["attachments"] else []
        assert all(x.kind != "duplicate" or mock["pos"][f"{mock['emails'][-1]['id']}/{x.attachment}"]["duplicate_of"] for x in again)  # 同一封邮件重跑不算重复
    finally:
        agent.reset_pipeline_versions()


def test_learned_mapping_is_used_next_time():
    from app.workflow import erp, store
    try:
        erp.product("KL-SV-80-400W")
    except Exception:
        pytest.skip("需要本机 MiniERP 的 MySQL")
    try:
        with T.approved("sales01", "TEST-1"):
            T.learn_part_mapping(4, "TEST-NEW-PN", "KL-SV-80-400W", "伺服 400W")
        assert T.map_customer_part(4)["TEST-NEW-PN"] == ("KL-SV-80-400W", "learned")
    finally:
        store.execute("DELETE FROM wf_customer_part_map WHERE customer_id = 4 AND customer_part_no = 'TEST-NEW-PN'")
        store.execute("DELETE FROM wf_audit WHERE object = 'part:4:TEST-NEW-PN'")
