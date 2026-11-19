"""
权限：决定一个人能看哪些视图（表级）、哪些行（行级）、哪些列（列级和打码）。
所有限制都落在 SQL 上，模型拿不到无权限的数据，而不是靠提示词"请不要回答"。
"""
import threading
import time
from dataclasses import dataclass

from sqlglot import exp, parse_one

from app.copilot import db
from app.copilot.semantic.loader import ViewDef, semantic

ALL, DEPT, SELF = "all", "dept", "self"
ROLE_CN = {"ADMIN": "管理员", "MANAGEMENT": "管理层", "FINANCE": "财务", "HR": "人力资源", "SALES": "销售", "PURCHASE": "采购",
           "WAREHOUSE": "仓储", "PRODUCTION": "生产", "QUALITY": "质量", "ENGINEER": "研发"}


@dataclass(frozen=True)
class UserContext:
    username: str
    role: str
    employee_id: int | None = None
    name: str = ""
    dept_id: int | None = None
    dept_name: str = ""
    dept_ids: tuple[int, ...] = ()        # 本部门及所有下级部门
    dept_names: tuple[str, ...] = ()
    line_codes: tuple[str, ...] = ()      # 这些部门里的员工所在的产线

    @property
    def scope_key(self) -> str:
        """缓存键里用：同一角色、同一行级范围的人可以共用查询结果"""
        return f"{self.role}:{','.join(map(str, self.dept_ids))}:{self.employee_id}"


_cache: dict[str, tuple[float, UserContext]] = {}
_lock = threading.Lock()
_TTL = 300


def load_user(username: str, role: str) -> UserContext:
    """从 ERP 读出这个账号对应的员工、部门子树和产线（缓存 5 分钟）"""
    with _lock:
        hit = _cache.get(username)
        if hit and time.time() - hit[0] < _TTL and hit[1].role == role:
            return hit[1]
    rows = db.meta_query(
        "SELECT e.id, e.name, e.dept_id, d.name AS dept_name FROM sys_user u JOIN hr_employee e ON e.id = u.employee_id "
        "JOIN org_dept d ON d.id = e.dept_id WHERE u.username = %s", (username,))
    if not rows:
        ctx = UserContext(username=username, role=role)
    else:
        me = rows[0]
        depts = db.meta_query("SELECT id, name, parent_id FROM org_dept")
        ids, changed = {me["dept_id"]}, True
        while changed:                                     # 部门树很小，循环展开子树
            changed = False
            for d in depts:
                if d["parent_id"] in ids and d["id"] not in ids:
                    ids.add(d["id"])
                    changed = True
        lines = db.meta_query("SELECT DISTINCT line_code FROM hr_employee WHERE line_code IS NOT NULL AND dept_id IN %s", (list(ids),))
        ctx = UserContext(username=username, role=role, employee_id=me["id"], name=me["name"], dept_id=me["dept_id"],
                          dept_name=me["dept_name"], dept_ids=tuple(sorted(ids)),
                          dept_names=tuple(d["name"] for d in depts if d["id"] in ids),
                          line_codes=tuple(sorted(r["line_code"] for r in lines)))
    with _lock:
        _cache[username] = (time.time(), ctx)
    return ctx


def access_level(view: ViewDef, role: str) -> str | None:
    return view.access.get(role) or view.access.get("*")


def allowed_views(role: str) -> list[ViewDef]:
    return [v for v in semantic.views.values() if access_level(v, role)]


def hidden_columns(view: ViewDef, role: str) -> dict[str, set[str]]:
    """这个角色看不到的敏感列 → 需要的角色"""
    return {c: roles for c, roles in view.sensitive.items() if role not in roles}


def masked_columns(view: ViewDef, role: str) -> set[str]:
    return {c for c, roles in view.mask.items() if role not in roles}


def visible_columns(view: ViewDef, role: str) -> list[str]:
    hidden = hidden_columns(view, role)
    return [c for c in view.columns if c not in hidden]


def roles_text(roles: set[str]) -> str:
    return "、".join(ROLE_CN.get(r, r) for r in sorted(roles, key=list(ROLE_CN).index))


def row_filter(view: ViewDef, ctx: UserContext) -> tuple[exp.Expression | None, str | None]:
    """返回（行级过滤条件, 给用户看的范围说明）；没有限制时都是 None"""
    level = access_level(view, ctx.role)
    if level == ALL:
        return None, None
    scope = view.row_scope
    if level == DEPT:
        note = f"{view.title}：仅 {ctx.dept_name} 及下级部门"
        if "dept_id" in scope and ctx.dept_ids:
            return exp.column(scope["dept_id"]).isin(*[exp.Literal.number(i) for i in ctx.dept_ids]), note
        if "dept_name" in scope and ctx.dept_names:
            return exp.column(scope["dept_name"]).isin(*[exp.Literal.string(n) for n in ctx.dept_names]), note
        if "line_code" in scope and ctx.line_codes:
            return (exp.column(scope["line_code"]).isin(*[exp.Literal.string(c) for c in ctx.line_codes]),
                    f"{view.title}：仅 {ctx.dept_name} 下属产线（{'、'.join(ctx.line_codes)}）")
        return exp.false(), f"{view.title}：你的账号没有关联到部门，查不到数据"
    if level == SELF:
        if "employee_id" in scope and ctx.employee_id:
            return exp.column(scope["employee_id"]).eq(exp.Literal.number(ctx.employee_id)), f"{view.title}：仅本人"
        return exp.false(), f"{view.title}：你的账号没有关联到员工，查不到数据"
    return exp.false(), None


_MASK_SQL = "CONCAT(LEFT(`{c}`, 4), REPEAT('*', GREATEST(CHAR_LENGTH(`{c}`) - 8, 4)), RIGHT(`{c}`, 4))"


def secured_source(view: ViewDef, ctx: UserContext) -> tuple[exp.Select | None, str | None, list[str]]:
    """
    把一张视图换成"这个人能看到的版本"：只保留可见列、打码列换成表达式、加上行级条件。
    没有任何限制时返回 None（直接查原视图）。返回（子查询, 范围说明, 被打码的列）。
    """
    cond, note = row_filter(view, ctx)
    hidden, masked = hidden_columns(view, ctx.role), masked_columns(view, ctx.role)
    if cond is None and not hidden and not masked:
        return None, None, []
    cols = []
    for c in visible_columns(view, ctx.role):
        if c in masked:
            cols.append(parse_one(_MASK_SQL.format(c=c), dialect="mysql").as_(c, quoted=True))
        else:
            cols.append(exp.column(c, quoted=True))
    select = exp.select(*cols).from_(exp.to_table(view.name))
    if cond is not None:
        select = select.where(cond)
    return select, note, sorted(masked)
