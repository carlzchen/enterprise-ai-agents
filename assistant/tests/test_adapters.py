"""
外部系统适配器的测试：接口一致性、按配置选实现、Mock 的行为、真实系统未接时明确报错。
文件类测试不依赖数据库；ERP 的 Mock 读 MiniERP 的库，没有数据库时跳过。
"""
import dataclasses
import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.infra.config.settings import settings
from app.workflow import adapters, mailbox
from app.workflow.adapters import mock, real
from app.workflow.adapters.base import EmailAdapter, ERPAdapter, KnowledgeAdapter, TaskAdapter
from app.workflow.adapters.models import EmailDraft, SalesOrderDraft, SalesOrderLine


def test_every_implementation_satisfies_its_protocol():
    for impl in (mock.FolderEmail(), real.GraphEmail(), real.ImapEmail()):
        assert isinstance(impl, EmailAdapter)
    for impl in (mock.MiniErpERP(), real.KingdeeERP()):
        assert isinstance(impl, ERPAdapter)
    for impl in (mock.WorkbenchTask(), real.FeishuTask()):
        assert isinstance(impl, TaskAdapter)
    for impl in (mock.MasterDataKB(), real.RagKnowledge()):
        assert isinstance(impl, KnowledgeAdapter)


def test_registry_follows_settings(monkeypatch):
    def use(**kw):                                              # 配置是冻结的 dataclass：换一份新的给注册表
        monkeypatch.setattr(adapters, "settings", dataclasses.replace(settings, **kw))
    use(workflow_email_adapter="mock", workflow_erp_adapter="mock", workflow_task_adapter="mock", workflow_knowledge_adapter="mock")
    assert isinstance(adapters.email(), mock.FolderEmail) and isinstance(adapters.erp(), mock.MiniErpERP)
    assert isinstance(adapters.task(), mock.WorkbenchTask) and isinstance(adapters.knowledge(), mock.MasterDataKB)
    use(workflow_erp_adapter="kingdee", workflow_email_adapter="graph")
    assert isinstance(adapters.erp(), real.KingdeeERP) and isinstance(adapters.email(), real.GraphEmail)
    use(workflow_erp_adapter="sap")
    with pytest.raises(ValueError, match="mock/kingdee"):
        adapters.erp()


def test_override_for_tests():
    fake = mock.FolderEmail(inbox="/nonexistent")
    with adapters.override(email=fake):
        assert adapters.email() is fake
    assert adapters.email() is not fake


def test_real_systems_fail_loudly_until_connected():
    with pytest.raises(real.NotConnected, match="Mail.Read"):
        real.GraphEmail().fetch_new()
    with pytest.raises(real.NotConnected, match="SAL_SaleOrder"):
        real.KingdeeERP().save_sales_order(None)
    with pytest.raises(real.NotConnected):
        real.FeishuTask().create_task(None)


def test_folder_email_round_trip(tmp_path):
    inbox, sent = tmp_path / "inbox", tmp_path / "sent"
    inbox.mkdir()
    box = mock.FolderEmail(inbox=inbox, sent_dir=sent, mailbox="orders@kelang.example.com")
    receipt = box.send(EmailDraft(to=["buyer@acme.example.com"], subject="Re: PO 4500012345", body="已收到，交期 2026-11-30。",
                                  in_reply_to="<po-1@acme.example.com>", ref="OB-001"))
    assert receipt.location.endswith("OB-001.eml")
    (inbox / "a.eml").write_bytes((sent / "OB-001.eml").read_bytes())      # 把发出的邮件当成收到的再读回来
    got = box.fetch_new()
    assert len(got) == 1 and got[0].mailbox == "orders@kelang.example.com"
    m = mailbox.parse(got[0].raw)
    assert m["in_reply_to"] == "<po-1@acme.example.com>" and m["subject"] == "Re: PO 4500012345" and "2026-11-30" in m["body"]


def test_sales_order_draft_validates_and_maps_to_kingdee():
    with pytest.raises(ValidationError):
        SalesOrderLine(material="KL-SV-80-400W", qty=0, unit_price=10, delivery_date=date(2026, 11, 30))
    d = SalesOrderDraft(customer_id=1, customer_code="C001", customer_po="PO-1", order_date=date(2026, 10, 19),
                        lines=[SalesOrderLine(material="KL-SV-80-400W", qty=100, unit_price=520.5, delivery_date=date(2026, 11, 30))])
    m = d.to_kingdee_model()
    assert m["FCustId"] == {"FNumber": "C001"} and m["FSaleOrderEntry"][0]["FQty"] == 100 and m["FSaleOrderEntry"][0]["FDeliveryDate"] == "2026-11-30"


# ---------------------------------------------------------------- 需要 MiniERP 数据库

def _erp_or_skip():
    try:
        e = mock.MiniErpERP()
        e.list_materials()
        return e
    except Exception:
        pytest.skip("需要本机 MiniERP 的 MySQL")


def test_mini_erp_queries():
    e = _erp_or_skip()
    m = e.get_material("KL-SV-80-400W")
    assert m and m.series == "SERVO"
    assert e.get_material("NO-SUCH-MODEL") is None
    c = e.get_customer(4)
    assert c and c.code and c.name
    assert e.get_inventory("KL-SV-80-400W") >= 0
    ref = e.get_price_reference(4, "KL-SV-80-400W", qty=1000)
    assert ref.source in ("quote", "last_deal", "none") and (ref.unit_price is None or ref.unit_price > 0)


def test_mini_erp_saves_draft_idempotently(tmp_path):
    e = _erp_or_skip()
    e.draft_dir = tmp_path
    d = SalesOrderDraft(customer_id=4, customer_code="C004", customer_po="PO/2026-001", order_date=date(2026, 10, 19), source_case="CASE-1",
                        lines=[SalesOrderLine(material="KL-SV-80-400W", qty=50, unit_price=500, delivery_date=date(2026, 11, 30))])
    r1, r2 = e.save_sales_order(d), e.save_sales_order(d)
    assert r1.ok and r1.bill_no == r2.bill_no and len(list(tmp_path.glob("*.json"))) == 1     # 同一个客户 + PO 号只有一份草稿
    body = json.loads((tmp_path / f"{r1.bill_no}.json").read_text(encoding="utf-8"))
    assert body["formid"] == "SAL_SaleOrder" and body["source_case"] == "CASE-1"


def test_outbound_confirmation_goes_through_email_adapter(tmp_path):
    """发件闸门：人确认后经邮箱适配器发出，库里记下发件位置；重复确认不会再发第二封"""
    _erp_or_skip()
    from app.workflow import flows, store
    box = mock.FolderEmail(inbox=tmp_path / "in", sent_dir=tmp_path / "sent")
    with adapters.override(email=box):
        aid = flows.create_outbound("DELIVERY_REPLY", None, "buyer@acme.example.com", "Re: 交期", "您好，订单预计 2026-11-30 发货。", "template",
                                    in_reply_to="<q-1@acme.example.com>")
        try:
            r1 = flows.confirm_outbound(aid, "", "sales01")
            r2 = flows.confirm_outbound(aid, "", "sales01")
            row = store.one("SELECT status, sent_path, adoption FROM wf_outbound WHERE action_id = %s", (aid,))
            assert r1["sent"] and not r2["sent"]
            assert row["status"] == "SENT" and row["adoption"] == "AS_IS" and row["sent_path"] == str(tmp_path / "sent" / f"{aid}.eml")
            assert len(list((tmp_path / "sent").glob("*.eml"))) == 1
            assert mailbox.parse((tmp_path / "sent" / f"{aid}.eml").read_bytes())["in_reply_to"] == "<q-1@acme.example.com>"
        finally:
            ob = store.one("SELECT task_id FROM wf_outbound WHERE action_id = %s", (aid,))
            store.execute("DELETE FROM wf_outbound WHERE action_id = %s", (aid,))
            if ob:
                store.execute("DELETE FROM wf_task WHERE task_id = %s", (ob["task_id"],))
            store.execute("DELETE FROM wf_audit WHERE object = %s", (f"outbound:{aid}",))
