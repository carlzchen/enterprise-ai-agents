"""
公司内部目录（SCIM 2.0 接口，AD / HR 系统 / IAM 一般都能提供）：SSO 登录之外，按员工号查最新的部门、职位、职级、是否在职。
用在两处：身份缓存过期后刷新（不用让人重新登录）、每日全量对账（离职、调岗没有触发注销时兜底）。

  GET /scim/v2/Users/{id}                        单个人
  GET /scim/v2/Users?startIndex=1&count=100      全量分页
部门路径、职级放在公司自定义的扩展里（urn:company:params:kb）。
"""
import requests

from app.infra.config.settings import settings

TIMEOUT = 5
EXT = "urn:company:params:kb"
ENT = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"


class DirectoryUnavailable(Exception):
    pass


_session = requests.Session()
_session.trust_env = False


def _get(path: str, **params) -> requests.Response:
    try:
        r = _session.get(settings.sso_scim_url.rstrip("/") + path, params=params, timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {settings.sso_scim_token}"})
    except requests.RequestException as e:
        raise DirectoryUnavailable(f"连不上目录服务：{type(e).__name__}") from e
    if r.status_code >= 500 or r.status_code == 429:
        raise DirectoryUnavailable(f"目录服务返回 {r.status_code}")
    return r


def to_claims(u: dict) -> dict:
    """SCIM 用户 → 和 ID Token 一样的声明结构"""
    ext, ent = u.get(EXT) or {}, u.get(ENT) or {}
    return {"sub": u["id"], "name": (u.get("name") or {}).get("formatted") or u.get("displayName"), "employee_no": ent.get("employeeNumber"),
            "dept_id": ext.get("dept_id"), "dept_path": ext.get("dept_path") or [], "dept_names": ext.get("dept_names") or [],
            "title": u.get("title") or "", "level": ext.get("level") or "", "active": bool(u.get("active", True))}


def user(sub: str) -> dict | None:
    r = _get(f"/Users/{sub}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return to_claims(r.json())


def all_users() -> list[dict]:
    out, start = [], 1
    while True:
        r = _get("/Users", startIndex=start, count=100)
        r.raise_for_status()
        body = r.json()
        out += [to_claims(u) for u in body.get("Resources", [])]
        start += body.get("itemsPerPage") or len(body.get("Resources", [])) or 100
        if start > body.get("totalResults", 0) or not body.get("Resources"):
            return out
