"""
公司 SSO 身份 → 知识库可见范围（Scope）。

  来源   登录时用 ID Token 里验过签的声明（部门路径、职位、职级、工号）；缓存过期后按员工号查内部目录（SCIM）刷新
  规则   resources/sso_rules.json：部门决定基本角色，职位补充角色，职级决定密级上限，部分角色的密级有封顶
  部门   dept_path 是从根到本部门的部门编号，全部变成 D:<编号> 标记，文档授权给上级部门时下级部门自动能看（沿组织树继承）
  缓存   Redis kb:principal:<sub>，和知识库登录凭证同寿命（8 小时）；另存一份「最后一次成功解析」的结果，最多沿用 7 天
  吊销   SSO 后端通道注销 → 记下吊销时间，签发早于这个时间的凭证全部作废，同时清掉身份缓存；每天和目录全量对账一次兜底

三种异常分开处理：
  1. 目录服务暂时不可用：有旧身份就沿用（标注「按 xx 时的身份判断」并记审计），没有就要求重新走 SSO 登录（登录时声明来自 ID Token，不依赖目录）
  2. 离职、禁用或目录里查无此人：拒绝，清掉缓存，记审计
  3. 没匹配到任何角色规则（新岗位、职级没填）：只给全员可见 + 本部门可见的公开资料，记审计并告警给管理员补规则

远程访问：知识库只在公司内网提供服务，在外面要用公司电脑连 VPN 后访问（网络层限制，见 deploy/nginx 和 server 的内网中间件），
这里不做任何公网登录方式。
"""
import json
import re
import time
from functools import lru_cache
from pathlib import Path

from app.infra.config.settings import settings
from app.kbase import authz, store
from app.kbase.auth import directory
from app.platform import resilience as R
from app.shared.runtime.logger import logger

RULES = Path(__file__).resolve().parents[1] / "resources" / "sso_rules.json"
KEY, STALE, REVOKED = "kb:principal:{}", "kb:principal:stale:{}", "kb:revoked:{}"
NO_RULE_NOTE = "你的岗位还没有配置知识库权限，暂时只能看公开资料"


class AuthDenied(Exception):
    """不能给这个人任何权限（离职、禁用、查无此人、目录不可用且没有旧身份）"""


@lru_cache(maxsize=1)
def rules() -> dict:
    return json.loads(RULES.read_text(encoding="utf-8"))


def derive(claims: dict) -> dict:
    """按规则算角色和密级"""
    r = rules()
    path = claims.get("dept_path") or []
    title, level_code = claims.get("title") or "", claims.get("level") or ""
    roles = []
    for x in r["dept_roles"]:
        if x["dept"] in path and x["role"] not in roles:
            roles.append(x["role"])
    for x in r["title_roles"]:
        if title and re.search(x["pattern"], title) and x["role"] not in roles:
            roles.append(x["role"])
    level = max((x["level"] for x in r["level_clearance"] if level_code and re.search(x["pattern"], level_code)), default=0)
    caps = [r["role_clearance_cap"].get(x) for x in roles]
    if roles and all(c is not None for c in caps):              # 所有角色都有上限时，密级不超过其中最大的上限
        level = min(level, max(caps))
    matched = bool(roles) and bool(level_code)
    return {"roles": roles, "clearance": level if matched else 0, "matched": matched}


def build(claims: dict) -> dict:
    d = derive(claims)
    path = claims.get("dept_path") or []
    names = claims.get("dept_names") or [""] * len(path)
    return {"sub": claims["sub"], "name": claims.get("name"), "employee_no": claims.get("employee_no"), "title": claims.get("title"),
            "level": claims.get("level"), "dept_path": [{"id": i, "name": n} for i, n in zip(path, names)], "roles": d["roles"],
            "clearance": d["clearance"], "matched": d["matched"], "resolved_at": int(time.time())}


def to_scope(p: dict, notes: tuple[str, ...] = ()) -> authz.Scope:
    tokens = [*p["roles"], *[f"D:{d['id']}" for d in p["dept_path"]]]
    if not p["matched"] and NO_RULE_NOTE not in notes:
        notes = (*notes, NO_RULE_NOTE)
    return authz.Scope.build(p["sub"], tokens, p["clearance"], source="sso", employee_no=p.get("employee_no"), notes=notes)


# ---------------------------------------------------------------- Redis

def _redis():
    return R.redis_client()


def _get(key: str) -> dict | None:
    try:
        r = _redis()
        v = r.get(key) if r else None
        return json.loads(v) if v else None
    except Exception:
        return None


def _set(key: str, value: dict, ttl: int) -> None:
    try:
        r = _redis()
        if r:
            r.setex(key, ttl, json.dumps(value, ensure_ascii=False))
    except Exception:
        pass


def _cache(p: dict) -> None:
    _set(KEY.format(p["sub"]), p, settings.sso_principal_ttl)
    _set(STALE.format(p["sub"]), p, settings.sso_stale_ttl)


def audit(sub: str, action: str, detail: dict, result: str, employee_no: str | None = None) -> None:
    try:
        store.execute("INSERT INTO kb_audit_log (user_id, employee_no, action, target, detail, result) VALUES (%s,%s,%s,%s,%s,%s)",
                      (sub, employee_no, action, "principal", store.dumps(detail), result))
    except Exception:
        logger.exception("[auth] 写审计失败")


def _no_rule_alert(p: dict) -> None:
    audit(p["sub"], "PRINCIPAL_NO_RULE", {"title": p["title"], "level": p["level"], "dept": [d["name"] for d in p["dept_path"]]}, "MINIMAL", p["employee_no"])
    try:
        from app.platform import alerts
        alerts.fire("kbase", "auth_no_rule", "P3", f"员工 {p['name']}（{p['employee_no']}）没有匹配到知识库权限规则",
                    f"职位：{p['title'] or '（空）'}；职级：{p['level'] or '（空）'}；部门：{'/'.join(d['name'] for d in p['dept_path'])}。请在 sso_rules.json 里补规则。",
                    dedup_key=f"kbase:auth_no_rule:{p['sub']}")
    except Exception:
        logger.warning("[auth] 没匹配到规则的告警没发出去")


# ---------------------------------------------------------------- 登录、解析、吊销

def login(claims: dict) -> authz.Scope:
    """SSO 回调里调用：ID Token 已验签。离职或禁用的人 SSO 本身就不会放行，这里再看一眼 active 声明"""
    if claims.get("active") is False:
        audit(claims["sub"], "PRINCIPAL_DENIED", {"reason": "账号已禁用"}, "DENIED", claims.get("employee_no"))
        raise AuthDenied("你的账号已被禁用，不能使用知识库")
    p = build(claims)
    if not p["matched"]:
        _no_rule_alert(p)
    _cache(p)
    audit(p["sub"], "SSO_LOGIN", {"roles": p["roles"], "clearance": p["clearance"], "dept": [d["id"] for d in p["dept_path"]]}, "OK", p["employee_no"])
    return to_scope(p)


def revoked_after(sub: str) -> float:
    """这个人最近一次被吊销的时间（没有返回 0）。签发时间早于它的知识库凭证都作废"""
    try:
        r = _redis()
        v = r.get(REVOKED.format(sub)) if r else None
        return float(v) if v else 0.0
    except Exception:
        return 0.0


def revoke(sub: str, reason: str) -> None:
    try:
        r = _redis()
        if r:
            r.setex(REVOKED.format(sub), settings.sso_stale_ttl, f"{time.time():.3f}")       # 毫秒精度：吊销之后重新登录拿到的凭证签发时间更晚
            r.delete(KEY.format(sub), STALE.format(sub))
    except Exception:
        logger.exception("[auth] 吊销失败")
    audit(sub, "SSO_REVOKED", {"reason": reason}, "REVOKED")


def resolve(sub: str, force: bool = False) -> authz.Scope:
    """每次请求调用：sub → Scope。缓存没有就查目录刷新；异常情况见模块说明"""
    if not force:
        hit = _get(KEY.format(sub))
        if hit:
            return to_scope(hit)
    try:
        claims = directory.user(sub)
    except directory.DirectoryUnavailable as e:                   # 异常一：目录不可用
        stale = _get(STALE.format(sub))
        if stale:
            when = time.strftime("%m-%d %H:%M", time.localtime(stale["resolved_at"]))
            audit(sub, "PRINCIPAL_STALE", {"reason": str(e), "resolved_at": when}, "DEGRADED", stale.get("employee_no"))
            return to_scope(stale, (f"公司目录暂时不可用，按 {when} 时的身份判断权限",))
        audit(sub, "PRINCIPAL_UNAVAILABLE", {"reason": str(e)}, "DENIED")
        raise AuthDenied("公司目录暂时不可用，也没有你之前的身份记录，请重新通过 SSO 登录") from e
    if claims is None or not claims.get("active", True):          # 异常二：离职、禁用或查无此人
        revoke(sub, "离职或禁用" if claims else "目录里查无此人")
        audit(sub, "PRINCIPAL_DENIED", {"reason": "离职或禁用" if claims else "目录里查无此人"}, "DENIED", (claims or {}).get("employee_no"))
        raise AuthDenied("你的账号已离职、被禁用或不在公司目录里，不能使用知识库")
    p = build(claims)
    if not p["matched"]:                                          # 异常三：没匹配到规则
        _no_rule_alert(p)
    _cache(p)
    return to_scope(p)


def describe(scope: authz.Scope) -> dict:
    p = _get(KEY.format(scope.user)) or _get(STALE.format(scope.user)) or {}
    return {"user_id": scope.user, "name": p.get("name"), "employee_no": scope.employee_no, "title": p.get("title"), "level": p.get("level"),
            "dept_path": " / ".join(d["name"] or d["id"] for d in p.get("dept_path") or []), "roles": p.get("roles"),
            "clearance": authz.LEVEL_CN.get(scope.clearance), "tokens": list(scope.tokens), "notes": list(scope.notes)}


def on_backchannel_logout(logout_token: str) -> dict:
    """SSO 后端通道注销：验签后吊销这个人的全部知识库登录"""
    from app.kbase.auth import sso
    claims = sso.verify_logout_token(logout_token)
    sub = claims.get("sub")
    if not sub:
        return {"ok": True, "ignored": "只有会话号，没有员工号"}
    try:
        r = _redis()
        jti = claims.get("jti")
        if r and jti and not r.set(f"kb:sso:logout:{jti}", "1", nx=True, ex=86400):
            return {"ok": True, "duplicate": True}
    except Exception:
        pass
    revoke(sub, "SSO 后端通道注销")
    return {"ok": True, "revoked": sub}


def reconcile_all() -> dict:
    """每日全量对账：从目录拉全部人员重新解析，和缓存里的比；变了的更新，离职或禁用的吊销。注销通知丢了也能在一天内追上"""
    users = directory.all_users()
    out = {"users": len(users), "changed": [], "removed": [], "no_rule": []}
    for u in users:
        sub = u["sub"]
        old = _get(STALE.format(sub))
        if not u.get("active", True):
            if old or not revoked_after(sub):
                revoke(sub, "每日对账发现已离职或禁用")
                out["removed"].append(sub)
            continue
        new = build(u)
        if not new["matched"]:
            out["no_rule"].append(sub)
        if old and (old["roles"], old["clearance"], [d["id"] for d in old["dept_path"]]) != (new["roles"], new["clearance"], [d["id"] for d in new["dept_path"]]):
            out["changed"].append({"sub": sub, "before": {"roles": old["roles"], "clearance": old["clearance"]},
                                   "after": {"roles": new["roles"], "clearance": new["clearance"]}})
        if old:                                                   # 只刷新用过知识库的人
            _cache(new)
    audit("-", "PRINCIPAL_RECONCILE", {k: (v if isinstance(v, int) else len(v)) for k, v in out.items()}, "OK")
    logger.info(f"[auth] 目录对账：{out['users']} 人，变化 {len(out['changed'])}，吊销 {len(out['removed'])}，没规则 {len(out['no_rule'])}")
    return out
