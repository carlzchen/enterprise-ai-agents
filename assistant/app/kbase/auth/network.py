"""
网络边界：知识库只在公司内网提供服务。远程办公要先用公司电脑连 VPN，拿到 VPN 网段的地址后再访问；没有公网入口。

  两层：Nginx 只监听内网网卡并按网段放行（deploy/nginx/kbase.conf）；应用里再用这个中间件校验一次来源地址，
        防止有人绕过 Nginx 直接打到服务端口
  来源地址：直连时看对端地址；只有对端是受信任的反向代理时，才采信它写的 X-Forwarded-For（取最后一个不受信任的地址），
        避免客户端自己伪造请求头
  /health 和 /metrics 也只给内网（监控在内网）
"""
import ipaddress

from fastapi import Request
from fastapi.responses import JSONResponse

from app.infra.config.settings import settings


def _nets(text: str) -> list:
    return [ipaddress.ip_network(x.strip(), strict=False) for x in text.split(",") if x.strip()]


ALLOWED = _nets(settings.kbase_allowed_networks)
PROXIES = _nets(settings.kbase_trusted_proxies)


def _in(addr: str, nets: list) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in n for n in nets)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    if not _in(peer, PROXIES):
        return peer
    chain = [x.strip() for x in request.headers.get("X-Forwarded-For", "").split(",") if x.strip()]
    for addr in reversed(chain):                                 # 从右往左，跳过受信任的代理
        if not _in(addr, PROXIES):
            return addr
    return chain[0] if chain else peer


def install(app) -> None:
    @app.middleware("http")
    async def intranet_only(request: Request, call_next):
        ip = client_ip(request)
        if not _in(ip, ALLOWED):
            return JSONResponse({"detail": "知识库只在公司内网提供服务，请用公司电脑连接 VPN 后访问"}, status_code=403)
        return await call_next(request)
