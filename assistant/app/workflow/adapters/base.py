"""
四个外部系统的接口（Protocol）。业务代码、Agent 的工具只依赖这些接口；用哪个实现由配置决定（registry.py）。

  EmailAdapter      收信、发信            Mock：本地 eml 目录      Real：Microsoft Graph / IMAP
  ERPAdapter        查客户、物料、价格、库存、订单；保存销售订单草稿
                                          Mock：MiniERP 自己的库   Real：金蝶云·星空 WebAPI
  TaskAdapter       派任务                Mock：跟单工作台待办中心  Real：飞书任务
  KnowledgeAdapter  产品知识检索          Mock：产品主数据         Real：项目一的企业知识库

方法名后面标 [W] 的是写操作：只能在人工审批通过之后调用（阶段 3 由图上的审批节点保证，并有测试证明审批前不会发生写操作）。
"""
from typing import Protocol, runtime_checkable

from app.workflow.adapters.models import (Customer, EmailDraft, ERPSaveResult, KBAnswer, Material, OrderStatus, PriceReference, RawEmail,
                                          SalesOrderDraft, SentReceipt, TaskRequest)


@runtime_checkable
class EmailAdapter(Protocol):
    name: str

    def fetch_new(self) -> list[RawEmail]:
        """取订单邮箱里的邮件。可以重复返回已经处理过的邮件，去重由 Message-ID 幂等保证"""
        ...

    def send(self, draft: EmailDraft) -> SentReceipt:
        """[W] 发出一封已经人工确认的邮件"""
        ...


@runtime_checkable
class ERPAdapter(Protocol):
    name: str

    def find_customer_by_email(self, addr: str) -> Customer | None: ...

    def get_customer(self, customer_id: int) -> Customer | None: ...

    def get_material(self, code: str) -> Material | None: ...

    def list_materials(self) -> list[Material]: ...

    def get_price_reference(self, customer_id: int, material: str) -> PriceReference: ...

    def get_inventory(self, material: str) -> float: ...

    def get_order_status(self, order_no: str) -> OrderStatus | None: ...

    def save_sales_order(self, draft: SalesOrderDraft) -> ERPSaveResult:
        """[W] 保存销售订单草稿（只保存，不提交、不审核）"""
        ...


@runtime_checkable
class TaskAdapter(Protocol):
    name: str

    def create_task(self, req: TaskRequest) -> str:
        """[W] 派一个任务，返回任务 ID"""
        ...


@runtime_checkable
class KnowledgeAdapter(Protocol):
    name: str

    def search_product_kb(self, query: str, role: str = "SALES") -> KBAnswer: ...
