"""
模拟的公司 SSO（仅本地开发用，挂在外部系统模拟网关 8096 上）：标准 OIDC 身份平台 + SCIM 2.0 目录。组织和人员都是虚构的。

  GET  /idp/.well-known/openid-configuration    发现文档
  GET  /idp/authorize                            授权页：带 login_hint 时当作「已经登录了公司 SSO」直接回跳；不带时给一个选人的页面
  POST /idp/token                                授权码换令牌（校验 client_secret、redirect_uri、PKCE）
  GET  /idp/jwks                                 签名公钥（RS256，密钥存在 output/mock_idp_key.pem，重启不变）
  GET  /scim/v2/Users、/scim/v2/Users/{id}       目录：部门路径、职位、职级、工号、是否在职

联调用：
  POST /mock/sso/users/{id}    改人员信息；禁用或调岗时像真实 SSO 一样向知识库推后端通道注销（logout_token）
  POST /mock/sso/outage        目录接口在接下来 N 秒里返回 503
"""
import base64
import hashlib
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Form, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.infra.config.settings import settings
from app.shared.runtime.logger import PROJECT_ROOT

router = APIRouter()
BASE = f"http://127.0.0.1:{settings.kingdee_mock_port}"
ISSUER = f"{BASE}/idp"
KID = "mock-idp-1"

DEPTS = {"D000": "科朗电机", "D100": "研发中心", "D110": "伺服研发部", "D120": "步进研发部", "D200": "质量部", "D300": "制造中心",
         "D310": "绕线车间", "D400": "营销中心", "D410": "华东销售部", "D500": "采购部", "D600": "财务部"}
PARENT = {"D100": "D000", "D110": "D100", "D120": "D100", "D200": "D000", "D300": "D000", "D310": "D300", "D400": "D000", "D410": "D400",
          "D500": "D000", "D600": "D000"}
USERS = {
    "E1000": {"name": "陈研", "dept": "D100", "title": "研发总监", "level": "M3", "employee_no": "KL1000"},
    "E1001": {"name": "张工", "dept": "D110", "title": "高级研发工程师", "level": "P7", "employee_no": "KL1001"},
    "E2001": {"name": "李质", "dept": "D200", "title": "质量工程师", "level": "P5", "employee_no": "KL2001"},
    "E4001": {"name": "赵班", "dept": "D310", "title": "绕线班组长", "level": "P4", "employee_no": "KL4001"},
    "E3001": {"name": "王销", "dept": "D410", "title": "销售经理", "level": "M1", "employee_no": "KL3001"},
    "E5001": {"name": "新同事", "dept": "D600", "title": "", "level": "", "employee_no": "KL5001"},
    "E1099": {"name": "已离职", "dept": "D110", "title": "研发工程师", "level": "P5", "employee_no": "KL1099", "active": False},
}
_codes: dict[str, dict] = {}
_down = {"until": 0.0}


def _key():
    f = PROJECT_ROOT / "output" / "mock_idp_key.pem"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        f.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return serialization.load_pem_private_key(Path(f).read_bytes(), password=None)


_private = None


def private():
    global _private
    if _private is None:
        _private = _key()
    return _private


def _path(dept: str) -> list[str]:
    out, cur = [], dept
    while cur:
        out.append(cur)
        cur = PARENT.get(cur)
    return list(reversed(out))


def claims_of(uid: str) -> dict:
    u = USERS[uid]
    path = _path(u["dept"])
    return {"sub": uid, "name": u["name"], "employee_no": u["employee_no"], "dept_id": u["dept"], "dept_path": path,
            "dept_names": [DEPTS[d] for d in path], "title": u["title"], "level": u["level"], "active": u.get("active", True)}


def _sign(payload: dict) -> str:
    return jwt.encode(payload, private(), algorithm="RS256", headers={"kid": KID})


@router.get("/idp/.well-known/openid-configuration")
def discovery():
    return {"issuer": ISSUER, "authorization_endpoint": f"{BASE}/idp/authorize", "token_endpoint": f"{BASE}/idp/token", "jwks_uri": f"{BASE}/idp/jwks",
            "response_types_supported": ["code"], "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"],
            "backchannel_logout_supported": True, "subject_types_supported": ["public"]}


@router.get("/idp/jwks")
def jwks():
    nums = private().public_key().public_numbers()

    def b64(n: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()
    return {"keys": [{"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256", "n": b64(nums.n), "e": b64(nums.e)}]}


@router.get("/idp/authorize")
def authorize(request: Request, client_id: str, redirect_uri: str, state: str, nonce: str, code_challenge: str, code_challenge_method: str = "S256",
              login_hint: str | None = None, response_type: str = "code", scope: str = "openid"):
    if client_id != settings.sso_client_id or redirect_uri != settings.sso_redirect_uri:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if not login_hint:                                         # 模拟 SSO 的登录页：选一个虚构员工
        q = dict(request.query_params)
        links = "".join(f'<li><a href="?{urlencode({**q, "login_hint": k})}">{v["name"]}（{DEPTS[v["dept"]]} · {v["title"] or "无职位"} · {v["level"] or "无职级"}）</a></li>'
                        for k, v in USERS.items())
        return HTMLResponse(f"<meta charset='utf-8'><h3>公司统一身份认证（模拟）</h3><p>选择登录的员工：</p><ul>{links}</ul>")
    if login_hint not in USERS or not USERS[login_hint].get("active", True):
        return RedirectResponse(f"{redirect_uri}?{urlencode({'error': 'access_denied', 'error_description': '账号不存在或已禁用', 'state': state})}")
    code = uuid.uuid4().hex
    _codes[code] = {"sub": login_hint, "nonce": nonce, "challenge": code_challenge, "redirect_uri": redirect_uri, "exp": time.time() + 60}
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}")


@router.post("/idp/token")
def token(grant_type: str = Form(...), code: str = Form(...), redirect_uri: str = Form(...), client_id: str = Form(...),
          client_secret: str = Form(...), code_verifier: str = Form(...)):
    c = _codes.pop(code, None)
    if client_id != settings.sso_client_id or client_secret != settings.sso_client_secret:
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if grant_type != "authorization_code" or not c or c["exp"] < time.time() or c["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_grant", "error_description": "授权码无效或已过期"}, status_code=400)
    want = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
    if want != c["challenge"]:
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE 校验失败"}, status_code=400)
    now = int(time.time())
    idt = _sign({"iss": ISSUER, "aud": client_id, "iat": now, "exp": now + 600, "nonce": c["nonce"], "sid": uuid.uuid4().hex, **claims_of(c["sub"])})
    return {"access_token": uuid.uuid4().hex, "token_type": "Bearer", "expires_in": 600, "id_token": idt}


# ---------------------------------------------------------------- SCIM 目录

def _scim(uid: str) -> dict:
    c = claims_of(uid)
    return {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "id": uid, "userName": c["employee_no"], "displayName": c["name"],
            "name": {"formatted": c["name"]}, "title": c["title"], "active": c["active"],
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"employeeNumber": c["employee_no"], "department": DEPTS[USERS[uid]["dept"]]},
            "urn:company:params:kb": {"dept_id": c["dept_id"], "dept_path": c["dept_path"], "dept_names": c["dept_names"], "level": c["level"]}}


def _scim_auth(authorization: str | None):
    if time.time() < _down["until"]:
        return JSONResponse({"detail": "service unavailable"}, status_code=503)
    if authorization != f"Bearer {settings.sso_scim_token}":
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return None


@router.get("/scim/v2/Users/{uid}")
def scim_user(uid: str, authorization: str | None = Header(default=None)):
    err = _scim_auth(authorization)
    if err:
        return err
    if uid not in USERS:
        return JSONResponse({"detail": "not found", "status": "404"}, status_code=404)
    return _scim(uid)


@router.get("/scim/v2/Users")
def scim_users(startIndex: int = 1, count: int = 100, authorization: str | None = Header(default=None)):
    err = _scim_auth(authorization)
    if err:
        return err
    ids = list(USERS)
    page = ids[startIndex - 1:startIndex - 1 + count]
    return {"schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"], "totalResults": len(ids), "startIndex": startIndex,
            "itemsPerPage": len(page), "Resources": [_scim(u) for u in page]}


# ---------------------------------------------------------------- 联调用

@router.post("/mock/sso/users/{uid}")
async def change_user(uid: str, request: Request):
    """改人员信息。禁用、调岗、改职级时，向知识库推后端通道注销（真实 SSO 在禁用账号或强制下线时会这么做）"""
    body = await request.json()
    u = USERS[uid]
    for k in ("dept", "title", "level", "name"):
        if k in body:
            u[k] = body[k]
    if "active" in body:
        u["active"] = bool(body["active"])
    delivered = None
    if body.get("logout", True):
        now = int(time.time())
        lt = _sign({"iss": ISSUER, "aud": settings.sso_client_id, "iat": now, "exp": now + 120, "jti": uuid.uuid4().hex, "sub": uid,
                    "events": {"http://schemas.openid.net/event/backchannel-logout": {}}})
        url = body.get("callback") or f"http://127.0.0.1:{settings.kbase_app_port}/auth/sso/backchannel-logout"
        try:
            delivered = requests.post(url, data={"logout_token": lt}, timeout=5).status_code
        except Exception as e:
            delivered = f"失败：{e}"
    return {"user": claims_of(uid), "backchannel_logout": delivered}


@router.post("/mock/sso/outage")
async def outage(request: Request):
    body = await request.json()
    _down["until"] = time.time() + float(body.get("seconds", 60))
    return {"down_until": _down["until"]}
