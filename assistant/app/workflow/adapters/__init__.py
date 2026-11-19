"""
外部系统适配器。业务代码和 Agent 的工具只用这里的四个函数拿到实现，不直接 import 具体类：

    from app.workflow import adapters
    adapters.email().fetch_new()
    adapters.erp().get_price_reference(customer_id, model)

用哪个实现由配置决定（WORKFLOW_EMAIL_ADAPTER / WORKFLOW_ERP_ADAPTER / WORKFLOW_TASK_ADAPTER / WORKFLOW_KNOWLEDGE_ADAPTER）。
测试里可以用 adapters.override(email=...) 换成自己的假实现。
"""
from contextlib import contextmanager

from app.infra.config.settings import settings
from app.workflow.adapters import mock, real
from app.workflow.adapters.base import EmailAdapter, ERPAdapter, KnowledgeAdapter, TaskAdapter

_EMAIL = {"mock": mock.FolderEmail, "imap": real.ImapEmail, "graph": real.GraphEmail}
_ERP = {"mock": mock.MiniErpERP, "kingdee": real.KingdeeERP}
_TASK = {"mock": mock.WorkbenchTask, "feishu": real.FeishuTask}
_KB = {"mock": mock.MasterDataKB, "rag": real.RagKnowledge}
_override: dict = {}


def _pick(kind: str, table: dict, choice: str):
    if kind in _override:
        return _override[kind]
    if choice not in table:
        raise ValueError(f"{kind} 适配器只能是 {'/'.join(table)}，配置的是 {choice!r}")
    return table[choice]()


def email() -> EmailAdapter:
    return _pick("email", _EMAIL, settings.workflow_email_adapter)


def erp() -> ERPAdapter:
    return _pick("erp", _ERP, settings.workflow_erp_adapter)


def task() -> TaskAdapter:
    return _pick("task", _TASK, settings.workflow_task_adapter)


def knowledge() -> KnowledgeAdapter:
    return _pick("knowledge", _KB, settings.workflow_knowledge_adapter)


@contextmanager
def override(**impls):
    """测试用：临时换掉某个适配器，例如 with adapters.override(email=FakeEmail()): ..."""
    old = dict(_override)
    _override.update(impls)
    try:
        yield
    finally:
        _override.clear()
        _override.update(old)
