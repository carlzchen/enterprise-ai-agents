"""
知识库服务的身份依赖。两种登录方式最后都变成同一个 Scope：

  公司 SSO     /auth/sso/login → SSO → /auth/sso/callback（验 ID Token）→ 本服务签发 8 小时的登录凭证（src = sso，sub = 员工号）
               之后每次请求按员工号取 Redis 里的身份缓存得到 Scope；凭证签发时间早于吊销时间（离职、禁用、SSO 注销）的一律拒绝
               这个凭证用知识库自己的密钥签、受众是 kbase：只在知识库有效，拿去调 ERP 后端会被拒绝（不能借知识库登录进 ERP）
  MiniERP 账号  MiniERP 签发的 JWT（sub = 用户名，role = 角色）→ 按角色给可见范围（authz.Scope.for_role），演示和运维用
"""
import hashlib
import time
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request

from app.api.auth import CurrentUser
from app.infra.config.settings import settings
from app.kbase import authz
from app.kbase.auth import principal

AUDIENCE = "kbase"


@dataclass
class Who:
    user: CurrentUser
    scope: authz.Scope


def _kb_key() -> bytes:
    """知识库 SSO 凭证的签名密钥：由主密钥派生，和 MiniERP 的 JWT 密钥不同"""
    return hashlib.sha256((settings.jwt_secret + "|kbase-sso-session").encode()).digest()


def issue(scope: authz.Scope, name: str | None) -> str:
    """SSO 用户的知识库登录凭证。角色写第一个（维护权限判断用），可见范围每次按员工号现查，不写死在凭证里"""
    now = time.time()
    claims = {"sub": scope.user, "name": name or scope.user, "role": scope.role or "STAFF", "src": "sso", "aud": AUDIENCE,
              "iat": round(now, 3), "exp": int(now + settings.sso_principal_ttl)}               # iat 到毫秒，和吊销时间比
    return jwt.encode(claims, _kb_key(), algorithm="HS256")


def _raw(request: Request) -> str:
    """优先读 Authorization 头；SSE 和图片标签带不了请求头，允许 ?token= 传"""
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else (request.query_params.get("token") or "")


def current_who(request: Request) -> Who:
    raw = _raw(request)
    if not raw:
        raise HTTPException(status_code=401, detail="未登录")
    try:                                                         # 先按知识库 SSO 凭证解
        c = jwt.decode(raw, _kb_key(), algorithms=["HS256"], audience=AUDIENCE)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新通过 SSO 登录")
    except jwt.PyJWTError:
        c = None
    if c is not None:
        if c.get("iat", 0) < principal.revoked_after(c["sub"]):
            raise HTTPException(status_code=401, detail="登录已被注销（离职、禁用或在 SSO 退出登录），请重新登录")
        try:
            scope = principal.resolve(c["sub"])
        except principal.AuthDenied as e:
            raise HTTPException(status_code=403, detail=str(e))
        return Who(CurrentUser(username=c["sub"], role=c.get("role", "")), scope)
    try:                                                         # 再按 MiniERP 的 JWT 解
        c = jwt.decode(raw, settings.jwt_secret, algorithms=["HS256", "HS384", "HS512"], options={"verify_aud": False})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    if c.get("aud") == AUDIENCE:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    user = CurrentUser(username=c["sub"], role=c.get("role", ""))
    return Who(user, authz.Scope.for_role(user.role, user.username))


def kb_user(request: Request) -> CurrentUser:
    """只需要「是谁、什么角色」的接口用这个（两种登录方式都认）"""
    return current_who(request).user
