"""
模拟的金蝶 EAS WebService 网关（仅本地开发用）：python -m app.kbase.kingdee.mock_gateway

对外是两个 SOAP 服务，请求和返回的格式照着 EAS 的 WebService 做：
  /ormrpc/services/EASLogin               login 方法，返回 sessionId
  /ormrpc/services/WSKbMasterDataFacade   query 方法（约定由 EAS 二次开发发布的只读查询服务），返回 JSON
数据来自 MiniERP：产品和物料对应 EAS 的物料档案，产品 BOM 和半成品 BOM 对应 EAS 的物料清单。
知识库这边只认 EAS 的协议，换成真实环境时只改地址和登录信息，不改代码。

这不是金蝶的产品，也没有包含金蝶的任何代码或数据，只是按公开的集成方式写的一个替身。
"""
import json
import re
import uuid
from xml.sax.saxutils import escape

import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.infra.config.settings import settings

app = FastAPI(title="外部系统模拟网关（本地开发）：金蝶 EAS WebService + 公司 SSO（OIDC + SCIM 目录）+ 飞书告警机器人接收端")
from app.kbase.auth.sso_mock import router as _sso_router  # noqa: E402

app.include_router(_sso_router)
_sessions: set[str] = set()


def _rows(sql: str) -> list[dict]:
    with pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user, password=settings.mysql_password,
                         database=settings.erp_database, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor) as c, c.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def _materials() -> list[dict]:
    out = []
    for p in _rows("SELECT code, name, series, voltage, power_w, rated_speed_rpm, status, created_at FROM md_product"):
        spec = " ".join(x for x in (p["voltage"], f"{p['power_w']:g}W" if p["power_w"] else "", f"{p['rated_speed_rpm']}rpm" if p["rated_speed_rpm"] else "") if x)
        out.append({"FNumber": p["code"], "FName_L2": p["name"], "FModel": spec, "FGroupName": f"成品-{p['series']}",
                    "FMaterialAttr": "自制", "FBaseUnitName": "台", "FStatus": 1 if p["status"] == "ACTIVE" else 0,
                    "FLastUpdateTime": p["created_at"].strftime("%Y-%m-%dT%H:%M:%S")})
    for m in _rows("SELECT code, name, category, uom, supply_type, created_at FROM md_material"):
        spec = re.search(r"([ΦφM]?\d[\w.×xX\-/]*)\s*$", m["name"])
        out.append({"FNumber": m["code"], "FName_L2": m["name"], "FModel": spec.group(1) if spec else "",
                    "FGroupName": f"物料-{m['category']}", "FMaterialAttr": "自制" if m["supply_type"] == "MAKE" else "外购",
                    "FBaseUnitName": m["uom"], "FStatus": 1, "FLastUpdateTime": m["created_at"].strftime("%Y-%m-%dT%H:%M:%S")})
    return out


def _boms() -> list[dict]:
    out = []
    for b in _rows("SELECT p.code parent, p.revision rev, m.code child, m.name child_name, b.qty_per, m.uom FROM md_bom b "
                   "JOIN md_product p ON p.id = b.product_id JOIN md_material m ON m.id = b.material_id ORDER BY p.code, b.id"):
        out.append({"FBomNumber": f"{b['parent']}_V{b['rev']}", "FParentNumber": b["parent"], "FChildNumber": b["child"],
                    "FChildName": b["child_name"], "FQty": float(b["qty_per"]), "FUnitName": b["uom"]})
    for b in _rows("SELECT pm.code parent, cm.code child, cm.name child_name, mb.qty_per, cm.uom FROM md_material_bom mb "
                   "JOIN md_material pm ON pm.id = mb.parent_material_id JOIN md_material cm ON cm.id = mb.child_material_id ORDER BY pm.code, mb.id"):
        out.append({"FBomNumber": f"{b['parent']}_V1", "FParentNumber": b["parent"], "FChildNumber": b["child"],
                    "FChildName": b["child_name"], "FQty": float(b["qty_per"]), "FUnitName": b["uom"]})
    return out


_DATASETS = {"material": _materials, "bom": _boms}
_ENVELOPE = ('<?xml version="1.0" encoding="UTF-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
             '<soapenv:Body>{body}</soapenv:Body></soapenv:Envelope>')


def _xml(body: str, status: int = 200) -> Response:
    return Response(_ENVELOPE.format(body=body), status_code=status, media_type="text/xml; charset=utf-8")


def _fault(message: str) -> Response:
    return _xml(f"<soapenv:Fault><faultcode>soapenv:Server</faultcode><faultstring>{escape(message)}</faultstring></soapenv:Fault>", 500)


def _arg(xml: str, name: str) -> str:
    m = re.search(rf"<(?:\w+:)?{name}[^>]*>(.*?)</(?:\w+:)?{name}>", xml, re.S)
    return m.group(1).strip() if m else ""


@app.post("/ormrpc/services/EASLogin")
async def login(request: Request):
    xml = (await request.body()).decode("utf-8")
    ok = (_arg(xml, "userName") == settings.kingdee_username and _arg(xml, "password") == settings.kingdee_password
          and _arg(xml, "dcName") == settings.kingdee_dc_name)
    if not ok:
        return _fault("用户名、密码或数据中心不正确")
    sid = uuid.uuid4().hex
    _sessions.add(sid)
    return _xml(f"<loginResponse><loginReturn><sessionId>{sid}</sessionId><userName>{escape(_arg(xml, 'userName'))}</userName>"
                f"<dcName>{escape(_arg(xml, 'dcName'))}</dcName><slnName>{escape(_arg(xml, 'slnName'))}</slnName></loginReturn></loginResponse>")


@app.post("/ormrpc/services/{facade}")
async def query(facade: str, request: Request):
    xml = (await request.body()).decode("utf-8")
    if facade != settings.kingdee_facade:
        return _fault(f"服务 {facade} 不存在或没有发布")
    if _arg(xml, "SessionId") not in _sessions:
        return _fault("会话已失效，请重新登录")
    loader = _DATASETS.get(_arg(xml, "dataset"))
    if loader is None:
        return _fault(f"数据集 {_arg(xml, 'dataset')} 不存在")
    start, limit = int(_arg(xml, "start") or 0), int(_arg(xml, "limit") or 2000)
    rows = loader()[start:start + limit]
    return _xml(f"<queryResponse><queryReturn>{escape(json.dumps(rows, ensure_ascii=False))}</queryReturn></queryResponse>")


# ---------------------------------------------------------------- 飞书自定义机器人（本地联调用的接收端）

_feishu: list[dict] = []


@app.post("/open-apis/bot/v2/hook/{token}")
async def feishu_hook(token: str, request: Request):
    """按飞书自定义机器人的约定校验签名并收下消息。真实地址是 https://open.feishu.cn/open-apis/bot/v2/hook/<token>"""
    import base64, hashlib, hmac, time
    body = await request.json()
    if settings.feishu_secret:
        ts = str(body.get("timestamp", ""))
        want = base64.b64encode(hmac.new(f"{ts}\n{settings.feishu_secret}".encode(), b"", hashlib.sha256).digest()).decode()
        if not ts or abs(time.time() - int(ts)) > 3600 or body.get("sign") != want:
            return {"code": 19021, "msg": "sign match fail or timestamp is not within one hour from current time"}
    card = body.get("card") or {}
    _feishu.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "token": token, "title": (card.get("header") or {}).get("title", {}).get("content"),
                    "color": (card.get("header") or {}).get("template"), "text": ((card.get("elements") or [{}])[0].get("text") or {}).get("content") or (body.get("content") or {}).get("text")})
    del _feishu[:-200]
    return {"code": 0, "msg": "success", "data": {}}


@app.get("/feishu/messages")
def feishu_messages():
    return list(reversed(_feishu))


@app.get("/health")
def health():
    return {"status": "ok", "mock": True, "product": "EAS"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=settings.kingdee_mock_port, log_level="warning")
