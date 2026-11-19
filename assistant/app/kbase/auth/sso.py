"""
公司 SSO（OIDC）客户端：授权码 + PKCE。线上指向公司的身份平台（AD FS / Keycloak / 自建 IAM 都支持标准 OIDC），本地联调指向模拟网关。

  登录    /auth/sso/login 生成 state、nonce、PKCE 的 code_verifier（存 Redis 10 分钟）→ 跳到 SSO 的授权页
          → SSO 登录（或已有会话直接通过）后带 code 回到 /auth/sso/callback → 用 code + code_verifier 换 ID Token
  验签    ID Token 用 SSO 公布的公钥（JWKS）验 RS256 签名，再核对 iss、aud、exp、nonce；不信任何没验过签的声明
  声明    sub（员工唯一号）、name、employee_no（工号）、dept_id、dept_path（从根到本部门的部门编号）、dept_names、title、level（职级）
  注销    SSO 的后端通道注销（Back-Channel Logout）：员工离职、被禁用或在 SSO 退出登录时，SSO 把 logout_token 推过来，
          这边验签后吊销这个人在知识库的全部登录（签发时间早于吊销时间的凭证一律作废）
"""
import base64
import hashlib
import secrets
import time

import jwt
import requests

from app.infra.config.settings import settings

TIMEOUT = 5
BACKCHANNEL_EVENT = "http://schemas.openid.net/event/backchannel-logout"


class SSOUnavailable(Exception):
    """SSO 暂时连不上"""


class SSOError(Exception):
    """授权码无效、签名不对、state 对不上等：拒绝登录"""


_session = requests.Session()
_session.trust_env = False
_meta = {"doc": None, "at": 0.0}
_jwks: dict = {}


def discovery() -> dict:
    if _meta["doc"] and time.time() - _meta["at"] < 3600:
        return _meta["doc"]
    try:
        r = _session.get(settings.sso_issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SSOUnavailable(f"取 SSO 配置失败：{type(e).__name__}") from e
    _meta.update(doc=r.json(), at=time.time())
    return _meta["doc"]


def _key_for(token: str):
    uri = discovery()["jwks_uri"]
    if uri not in _jwks:
        _jwks[uri] = jwt.PyJWKClient(uri, cache_keys=True, lifespan=3600)
    try:
        return _jwks[uri].get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientConnectionError as e:
        raise SSOUnavailable("取 SSO 公钥失败") from e


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(state: str, nonce: str, challenge: str, login_hint: str | None = None) -> str:
    from urllib.parse import urlencode
    q = {"response_type": "code", "client_id": settings.sso_client_id, "redirect_uri": settings.sso_redirect_uri, "scope": "openid profile kb",
         "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"}
    if login_hint:
        q["login_hint"] = login_hint
    return discovery()["authorization_endpoint"] + "?" + urlencode(q)


def exchange(code: str, verifier: str, nonce: str) -> dict:
    """授权码换 ID Token 并验签，返回声明"""
    try:
        r = _session.post(discovery()["token_endpoint"], timeout=TIMEOUT, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": settings.sso_redirect_uri, "client_id": settings.sso_client_id,
            "client_secret": settings.sso_client_secret, "code_verifier": verifier})
    except requests.RequestException as e:
        raise SSOUnavailable(f"换取令牌失败：{type(e).__name__}") from e
    if r.status_code >= 500:
        raise SSOUnavailable(f"SSO 返回 {r.status_code}")
    body = r.json()
    if "id_token" not in body:
        raise SSOError(f"换取令牌失败：{body.get('error_description') or body.get('error')}")
    claims = verify(body["id_token"])
    if claims.get("nonce") != nonce:
        raise SSOError("nonce 对不上，可能是重放")
    return claims


def verify(token: str, audience: str | None = None) -> dict:
    try:
        return jwt.decode(token, _key_for(token), algorithms=["RS256"], audience=audience or settings.sso_client_id,
                          issuer=discovery()["issuer"], leeway=30)
    except jwt.PyJWTError as e:
        raise SSOError(f"令牌校验失败：{e}") from e


def verify_logout_token(token: str) -> dict:
    """后端通道注销令牌：验签，必须带注销事件、不能带 nonce"""
    claims = verify(token)
    if BACKCHANNEL_EVENT not in (claims.get("events") or {}) or "nonce" in claims or not (claims.get("sub") or claims.get("sid")):
        raise SSOError("不是合法的注销令牌")
    return claims


# ---------------------------------------------------------------- 登录状态（state → nonce + code_verifier）

def save_state(state: str, data: dict, ttl: int = 600) -> bool:
    import json
    from app.platform import resilience as R
    r = R.redis_client()
    if r is None:
        return False
    try:
        r.set(f"kb:sso:state:{state}", json.dumps(data), ex=ttl)
        return True
    except Exception:
        return False


def pop_state(state: str) -> dict | None:
    """取出并删除（原子操作），同一个 state 只能用一次，防重放"""
    import json
    from app.platform import resilience as R
    r = R.redis_client()
    if r is None:
        return None
    try:
        raw = r.getdel(f"kb:sso:state:{state}")
    except Exception:
        return None
    return json.loads(raw) if raw else None
