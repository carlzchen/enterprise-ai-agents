"""
数据库访问。两个账号，职责分开：
  assistant   服务自己用：读用户归属和表结构（minierp 库只读）、读写 assistant 库里的审计表
  copilot_ro  只用来执行"模板或模型生成的 SQL"：数据库层面只授权了白名单视图，别的表读不到，也写不了
"""
import datetime as dt
from decimal import Decimal

import pymysql

from app.infra.config.settings import settings
from app.platform import resilience as R

QUERY_TIMEOUT_MS = 10_000


def _connect(user: str, password: str, database: str, read_timeout: int = 15):
    return pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=user, password=password, database=database,
                           charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                           connect_timeout=5, read_timeout=read_timeout)


def meta_query(sql: str, args=None) -> list[dict]:
    """服务内部的固定查询（参数化），读 minierp 库"""
    with _connect(settings.mysql_user, settings.mysql_password, settings.erp_database) as c, c.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def app_query(sql: str, args=None) -> list[dict]:
    with _connect(settings.mysql_user, settings.mysql_password, settings.mysql_database) as c, c.cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def app_exec(sql: str, args=None) -> int:
    with _connect(settings.mysql_user, settings.mysql_password, settings.mysql_database) as c, c.cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


DB = R.Breaker("copilot_db", "ERP 数据库（问数）", fail_max=5, reset_seconds=20)
DB_POOL = R.Bulkhead("copilot_db", "ERP 数据库（问数）", limit=8, queue=24, wait_seconds=10)       # 同时最多 8 条分析 SQL，保护业务库


def ro_query(sql: str) -> tuple[list[str], list[dict]]:
    """用只读白名单账号执行已校验的 SQL。经过并发闸门和熔断器：库忙时排队，排不上或连续出错就告诉上层「不可用」"""
    with DB_POOL.enter():
        return DB.call(_ro_query, sql)


def _ro_query(sql: str) -> tuple[list[str], list[dict]]:
    """不传参数，所以 SQL 里的 % 不会被当成占位符"""
    with _connect(settings.copilot_db_user, settings.copilot_db_password, settings.erp_database,
                  read_timeout=QUERY_TIMEOUT_MS // 1000 + 5) as c, c.cursor() as cur:
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {QUERY_TIMEOUT_MS}")
        cur.execute(sql)
        columns = [d[0] for d in cur.description or []]
        return columns, [{k: _plain(v) for k, v in row.items()} for row in cur.fetchall()]


def ro_probe(sql_limit_0: str) -> None:
    """试运行：用执行账号跑一遍 LIMIT 0 的版本。MySQL 会解析全部表名和字段、检查权限，但不取数。"""
    with _connect(settings.copilot_db_user, settings.copilot_db_password, settings.erp_database) as c, c.cursor() as cur:
        cur.execute(sql_limit_0)


def explain_rows(sql: str) -> int:
    """
    预计扫描行数。MySQL 不允许只有视图权限的账号对视图做 EXPLAIN（会暴露底层表结构），
    所以这一步用服务账号；它只拿执行计划，不取数据。
    """
    with _connect(settings.mysql_user, settings.mysql_password, settings.erp_database) as c, c.cursor() as cur:
        cur.execute("EXPLAIN " + sql)
        return sum(int(r.get("rows") or 0) for r in cur.fetchall())
