"""复用 MiniERP 签发的 JWT（同一密钥；jjwt 按密钥长度选择 HS256/384/512），实现单点登录与角色控制"""
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Query, Request

from app.infra.config.settings import settings

IMPORT_ROLES = {"ADMIN", "ENGINEER", "QUALITY"}


@dataclass
class CurrentUser:
    username: str
    role: str


def _decode(token: str) -> CurrentUser:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256", "HS384", "HS512"])
        return CurrentUser(username=claims["sub"], role=claims.get("role", ""))
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录 MiniERP")


def current_user(request: Request, token: str | None = Query(default=None)) -> CurrentUser:
    """优先读 Authorization 头；SSE 的 EventSource 不能带请求头，允许 ?token= 传递"""
    header = request.headers.get("Authorization", "")
    raw = header[7:] if header.startswith("Bearer ") else token
    if not raw:
        raise HTTPException(status_code=401, detail="未登录")
    return _decode(raw)


def import_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.role not in IMPORT_ROLES:
        raise HTTPException(status_code=403, detail="仅研发、质量或管理员可以维护知识库")
    return user
