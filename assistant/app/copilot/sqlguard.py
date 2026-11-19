"""
SQL 校验与改写。不管 SQL 来自模板还是模型，执行前都走同一条链：
  ① 语法解析  ② 只能是一条查询  ③ 表白名单  ④ 敏感列  ⑤ 危险函数
  ⑥ 改写：把每张视图换成"这个人能看到的版本"（行级过滤、列裁剪、打码），再强制加行数上限
  ⑦ 试运行：用执行账号跑 LIMIT 0 的版本，让数据库确认表、字段、权限都没问题；再看执行计划，拦住扫描量过大的查询
任何一步不过都不会执行。最后一道保险在数据库：执行账号只有白名单视图的 SELECT 权限。
"""
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.copilot import db
from app.copilot.permission import UserContext, access_level, hidden_columns, roles_text, secured_source
from app.copilot.semantic.loader import semantic
from app.infra.config.settings import settings

MAX_ROWS = 1000
MAX_SCAN_ROWS = 20_000_000
_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Merge, exp.Set,
              exp.Use, exp.TruncateTable, exp.Into, exp.Lock, exp.Transaction, exp.Commit, exp.Rollback, exp.Grant)
_BANNED_FUNCS = {"SLEEP", "BENCHMARK", "LOAD_FILE", "GET_LOCK", "RELEASE_LOCK", "IS_FREE_LOCK", "IS_USED_LOCK", "USER",
                 "CURRENT_USER", "SESSION_USER", "SYSTEM_USER", "DATABASE", "SCHEMA", "VERSION", "CONNECTION_ID",
                 "UUID", "RAND", "FOUND_ROWS", "ROW_COUNT", "LAST_INSERT_ID", "MASTER_POS_WAIT", "EXTRACTVALUE", "UPDATEXML"}
_SYSTEM_SCHEMAS = {"information_schema", "mysql", "performance_schema", "sys"}


class SqlRejected(Exception):
    """repairable=True 表示是写法问题，可以把错误信息还给模型让它改；False 表示是权限或安全问题，直接拒绝"""

    def __init__(self, message: str, repairable: bool = False, code: str = "REJECTED"):
        super().__init__(message)
        self.message, self.repairable, self.code = message, repairable, code


@dataclass
class Guarded:
    sql: str                                   # 改写后真正执行的 SQL
    views: list[str] = field(default_factory=list)
    scope_notes: list[str] = field(default_factory=list)
    masked: list[str] = field(default_factory=list)
    est_rows: int = 0

    @property
    def self_only(self) -> list[str]:
        """哪些数据被限定成了「仅本人」"""
        return [n.split("：")[0] for n in self.scope_notes if n.endswith("仅本人")]


def _func_name(f: exp.Func) -> str:
    return (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).upper()


def parse(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql.strip().rstrip(";"), read="mysql") if s is not None]
    except SqlglotError as e:
        raise SqlRejected("SQL 语法错误：" + re.sub(r"\x1b\[[0-9;]*m", "", str(e))[:200], repairable=True, code="SYNTAX")
    if len(statements) != 1:
        raise SqlRejected("只允许一条 SQL 语句", code="MULTI")
    return statements[0]


def check(tree: exp.Expression, ctx: UserContext) -> list[str]:
    """②–⑤ 静态检查，返回用到的白名单视图"""
    if not isinstance(tree, exp.Query) or any(True for _ in tree.find_all(*_FORBIDDEN)):
        raise SqlRejected("只允许查询语句，不能新增、修改或删除数据", code="NOT_SELECT")
    if any(True for _ in tree.find_all(exp.Parameter, exp.SessionParameter, exp.Placeholder)):
        raise SqlRejected("不允许使用变量或占位符", code="VARIABLE")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    used: list[str] = []
    for t in tree.find_all(exp.Table):
        if t.db:
            if t.db.lower() in _SYSTEM_SCHEMAS:
                raise SqlRejected("不允许访问系统库", code="SYSTEM_SCHEMA")
            if t.db != settings.erp_database:
                raise SqlRejected(f"不允许访问数据库 {t.db}", code="TABLE")
        elif t.name in ctes:
            continue
        view = semantic.views.get(t.name)
        if view is None:
            raise SqlRejected(f"{t.name} 不在可查询的数据范围内", code="TABLE")
        if not access_level(view, ctx.role):
            need = "、".join(sorted(r for r in view.access if r != "*"))
            raise SqlRejected(f"你的角色无权查询「{view.title}」（{t.name}），需要角色：{need}", code="TABLE_DENIED")
        if t.name not in used:
            used.append(t.name)
    hidden: dict[str, set[str]] = {}
    for name in used:
        hidden.update(hidden_columns(semantic.views[name], ctx.role))
    for c in tree.find_all(exp.Column):
        if c.name in hidden:
            view = next(v for v in (semantic.views[n] for n in used) if c.name in v.sensitive)
            label = view.columns.get(c.name) or c.name
            raise SqlRejected(f"你的角色无权查看「{label.split('，')[0].split('（')[0]}」，需要 {roles_text(hidden[c.name])} 权限", code="COLUMN_DENIED")
    for f in tree.find_all(exp.Func):
        if _func_name(f) in _BANNED_FUNCS:
            raise SqlRejected(f"不允许使用函数 {_func_name(f)}", code="FUNCTION")
    return used


def rewrite(tree: exp.Expression, ctx: UserContext) -> tuple[exp.Expression, list[str], list[str]]:
    """⑥ 改写：视图 → 受限子查询；强制行数上限"""
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    notes, masked = [], []
    for t in list(tree.find_all(exp.Table)):
        if (not t.db and t.name in ctes) or t.name not in semantic.views:
            continue
        source, note, m = secured_source(semantic.views[t.name], ctx)
        if note and note not in notes:
            notes.append(note)
        masked.extend(x for x in m if x not in masked)
        if source is not None:
            t.replace(source.subquery(t.alias or t.name))
    limit = tree.args.get("limit")
    current = None
    if limit is not None and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        current = int(limit.expression.this)
    if current is None or current > MAX_ROWS:
        tree = tree.limit(MAX_ROWS)
    return tree, notes, masked


def guard(sql: str, ctx: UserContext, dry_run: bool = True) -> Guarded:
    tree = parse(sql)
    views = check(tree, ctx)
    tree, notes, masked = rewrite(tree, ctx)
    final = tree.sql(dialect="mysql")
    est = 0
    if dry_run:
        try:
            db.ro_probe(tree.copy().limit(0).sql(dialect="mysql"))
        except Exception as e:                                       # 字段不存在、函数用错等：可以让模型改
            raise SqlRejected(f"数据库无法执行这条 SQL：{str(e)[:300]}", repairable=True, code="PROBE")
        try:
            est = db.explain_rows(final)
        except Exception:                                            # 扫描量估算只是保护措施，估不出来不拦
            est = 0
        if est > MAX_SCAN_ROWS:
            raise SqlRejected("查询范围过大，请缩小时间范围或增加过滤条件", code="TOO_LARGE")
    return Guarded(sql=final, views=views, scope_notes=notes, masked=masked, est_rows=est)
