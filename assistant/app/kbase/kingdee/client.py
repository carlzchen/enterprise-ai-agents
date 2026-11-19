"""
金蝶 EAS 对接客户端。EAS 对外集成走的是 WebService（SOAP），不是 REST：
  登录    {地址}/ormrpc/services/EASLogin 的 login 方法（用户名、密码、解决方案名、数据中心、语言、数据库类型），返回会话号 sessionId
  取数    {地址}/ormrpc/services/{查询服务名} 的 query 方法，请求头里带 sessionId，分页返回 JSON

要注意的一点：EAS 标准产品里没有「随便查一张表」的通用接口（这和金蝶云星空不一样）。
读物料和 BOM 需要 EAS 那边做一个只读的查询服务并发布成 WebService（BOS 二次开发，通常由 EAS 顾问或公司 IT 完成）；
这里约定的服务名默认是 WSKbMasterDataFacade，方法是 query(数据集名, 起始行, 每页行数)，返回 JSON 数组。
另一种常见做法是让 IT 在 EAS 数据库上建两个只读视图、开一个只读账号直接查，字段含义见 forms.py。
"""
import json
import re
import threading
from xml.sax.saxutils import escape

import httpx

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

PAGE = 2000
_ENVELOPE = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
             '<soapenv:Header>{header}</soapenv:Header><soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>')


class KingdeeError(RuntimeError):
    pass


def _tag(xml: str, name: str) -> str | None:
    m = re.search(rf"<(?:\w+:)?{name}[^>]*>(.*?)</(?:\w+:)?{name}>", xml, re.S)
    return m.group(1) if m else None


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&amp;", "&")


class EasClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or settings.kingdee_base_url).rstrip("/")
        self._http = httpx.Client(timeout=60, trust_env=False)
        self._session: str | None = None
        self._lock = threading.Lock()

    def _call(self, service: str, body: str, header: str = "") -> str:
        r = self._http.post(f"{self.base}/ormrpc/services/{service}", content=_ENVELOPE.format(header=header, body=body).encode("utf-8"),
                            headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'})
        fault = _tag(r.text, "faultstring")
        if fault:
            raise KingdeeError(_unescape(fault))
        r.raise_for_status()
        return r.text

    def login(self) -> None:
        args = [("userName", settings.kingdee_username), ("password", settings.kingdee_password), ("slnName", settings.kingdee_sln_name),
                ("dcName", settings.kingdee_dc_name), ("language", settings.kingdee_language), ("dbType", settings.kingdee_db_type)]
        xml = self._call("EASLogin", "<login>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args) + "</login>")
        sid = _tag(xml, "sessionId")
        if not sid:
            raise KingdeeError("金蝶 EAS 登录失败：没有拿到 sessionId，请检查用户名、密码和数据中心")
        self._session = sid
        logger.info(f"金蝶 EAS 已登录：{self.base}，数据中心 {settings.kingdee_dc_name}")

    def _page(self, dataset: str, start: int) -> list[dict]:
        header = f'<SessionId xmlns="http://login.webservice.bos.kingdee.com">{self._session}</SessionId>'
        xml = self._call(settings.kingdee_facade, f"<query><dataset>{dataset}</dataset><start>{start}</start><limit>{PAGE}</limit></query>", header)
        return json.loads(_unescape(_tag(xml, "queryReturn") or "[]"))

    def query(self, dataset: str) -> list[dict]:
        """取一个数据集的全部行（自动翻页）。会话过期时重新登录一次"""
        rows, start = [], 0
        with self._lock:
            if not self._session:
                self.login()
            while True:
                try:
                    page = self._page(dataset, start)
                except KingdeeError as e:
                    if "会话" not in str(e) and "session" not in str(e).lower():
                        raise KingdeeError(f"金蝶 EAS 查询 {dataset} 失败：{e}") from e
                    self.login()
                    page = self._page(dataset, start)
                rows.extend(page)
                if len(page) < PAGE:
                    return rows
                start += PAGE


kingdee = EasClient()
