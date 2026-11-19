"""
真实系统的实现。拿不到权限之前只有 IMAP 能用（沿用原来的只读收信）；其余写清楚要对接什么，调用时报错而不是悄悄降级。

  GraphEmail      Microsoft 365（推荐）：Entra ID 应用 + 证书认证，Mail.Read / Mail.Send 应用权限，
                  用 RBAC for Applications 限定到订单共享邮箱；收信用订阅通知 + 增量查询（delta），按 internetMessageId 幂等；
                  发信可以改成在业务员草稿箱生成草稿，由人在 Outlook 里点发送。GRAPH_CLOUD=china 时接口地址是 microsoftgraph.chinacloudapi.cn。
  ImapEmail       自建邮件服务器时用；只读打开收件箱，不改已读状态。Exchange Online 已停用账号密码方式，不推荐。
  KingdeeERP      金蝶云·星空 WebAPI：用第三方系统登录授权（AppID / AppSecret）登录，查询走 ExecuteBillQuery，
                  保存销售订单走 Save（formid=SAL_SaleOrder），只保存不提交、不审核；集成用户只开销售订单保存权限。
  FeishuTask      飞书自建应用，任务 v2 接口在指定任务清单里建任务，负责人按工号映射飞书 open_id。
  RagKnowledge    项目一的企业知识库（/ask 接口）；阶段 6 做技术 Agent 时接上。
"""
import imaplib
from datetime import datetime

from app.infra.config.settings import settings
from app.workflow.adapters.models import EmailDraft, RawEmail, SentReceipt


class NotConnected(RuntimeError):
    """真实系统还没接：配置成 real 但实现还是占位时抛出"""


class GraphEmail:
    name = "graph"

    def fetch_new(self) -> list[RawEmail]:
        raise NotConnected("Microsoft Graph 收信还没接：需要 IT 注册 Entra ID 应用、授予 Mail.Read 并限定到订单共享邮箱")

    def send(self, draft: EmailDraft) -> SentReceipt:
        raise NotConnected("Microsoft Graph 发信还没接：需要 Mail.Send 权限（或改成在业务员草稿箱生成草稿）")


class ImapEmail:
    name = "imap"

    def fetch_new(self, limit: int = 50) -> list[RawEmail]:
        out = []
        with imaplib.IMAP4_SSL(settings.workflow_imap_host) as box:
            box.login(settings.workflow_imap_user, settings.workflow_imap_password)
            box.select("INBOX", readonly=True)
            _, data = box.search(None, "ALL")
            for num in data[0].split()[-limit:]:
                _, parts = box.fetch(num, "(BODY.PEEK[])")
                out.append(RawEmail(source=f"imap:{num.decode()}", mailbox=settings.workflow_imap_user, raw=parts[0][1]))
        return out

    def send(self, draft: EmailDraft) -> SentReceipt:
        raise NotConnected(f"SMTP 发信还没接（{datetime.now():%Y-%m-%d}）：需要订单邮箱的 SMTP 账号")


class KingdeeERP:
    name = "kingdee"
    _WHY = "金蝶云·星空 WebAPI 还没接：需要数据中心 ID、AppID / AppSecret 和只开销售订单保存权限的集成用户"

    def _todo(self, what: str):
        raise NotConnected(f"{self._WHY}（调用了 {what}）")

    def find_customer_by_email(self, addr):
        self._todo("find_customer_by_email：客户联系人 BD_Customer 的联系人子表按邮箱查")

    def get_customer(self, customer_id):
        self._todo("get_customer：ExecuteBillQuery 查 BD_Customer")

    def get_material(self, code):
        self._todo("get_material：ExecuteBillQuery 查 BD_MATERIAL")

    def list_materials(self):
        self._todo("list_materials")

    def get_price_reference(self, customer_id, material, qty=None):
        self._todo("get_price_reference：销售价目表 BD_SAL_PriceList，或最近一张销售订单")

    def get_inventory(self, material):
        self._todo("get_inventory：即时库存 STK_Inventory")

    def get_order_status(self, order_no):
        self._todo("get_order_status：销售订单 SAL_SaleOrder + 发货通知 / 出库")

    def save_sales_order(self, draft):
        self._todo("save_sales_order：Save（formid=SAL_SaleOrder），只保存不提交、不审核")


class FeishuTask:
    name = "feishu"

    def create_task(self, req):
        raise NotConnected("飞书任务还没接：需要飞书自建应用、任务权限和任务清单 GUID")


class RagKnowledge:
    name = "rag"

    def search_product_kb(self, query: str, role: str = "SALES"):
        raise NotConnected("项目一知识库的接口在阶段 6（技术 Agent）接入")
