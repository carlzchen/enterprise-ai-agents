"""
MiniERP 业务数据只读访问（数据库账号只有 minierp 库的 SELECT 权限）。
全部为参数化查询，不接受模型生成的 SQL。
"""
import pymysql

from app.infra.config.settings import settings

_PRODUCT_SQL = """
SELECT p.code, p.name, p.series, p.business_unit, p.voltage, p.power_w, p.rated_speed_rpm, p.list_price, p.revision
FROM md_product p WHERE p.status = 'ACTIVE'
"""


class ErpRepository:
    def _conn(self):
        return pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user,
                               password=settings.mysql_password, database=settings.erp_database, charset="utf8mb4",
                               cursorclass=pymysql.cursors.DictCursor, autocommit=True, read_timeout=10)

    def _query(self, sql: str, args=None) -> list[dict]:
        with self._conn() as c, c.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def products(self) -> list[dict]:
        return self._query(_PRODUCT_SQL + " ORDER BY p.code")

    def product(self, code: str) -> dict | None:
        rows = self._query(_PRODUCT_SQL + " AND p.code = %s", (code,))
        return rows[0] if rows else None

    def products_by_series(self, series: str) -> list[dict]:
        return self._query(_PRODUCT_SQL + " AND p.series = %s ORDER BY p.code", (series,))

    def bom(self, code: str) -> list[dict]:
        return self._query("""
            SELECT m.code AS material_code, m.name AS material_name, b.qty_per, m.uom
            FROM md_bom b JOIN md_product p ON p.id = b.product_id JOIN md_material m ON m.id = b.material_id
            WHERE p.code = %s ORDER BY m.code""", (code,))

    def ecn_history(self, code: str | None = None, series: str | None = None, limit: int = 10) -> list[dict]:
        where, args = [], []
        if code:
            where.append("p.code = %s")
            args.append(code)
        if series:
            where.append("p.series = %s")
            args.append(series)
        cond = " AND ".join(where) or "1=1"
        return self._query(f"""
            SELECT DISTINCT e.ecn_no, e.title, e.reason, e.status, e.request_date, e.effective_date, e.description
            FROM plm_ecn e JOIN plm_ecn_line l ON l.ecn_id = e.id JOIN md_product p ON p.id = l.product_id
            WHERE {cond} ORDER BY e.request_date DESC LIMIT %s""", (*args, limit))

    def open_complaints(self, code: str) -> list[dict]:
        return self._query("""
            SELECT c.complaint_no, c.defect_type, c.status, c.received_date, c.root_cause
            FROM qms_complaint c JOIN md_product p ON p.id = c.product_id
            WHERE p.code = %s ORDER BY c.received_date DESC LIMIT 5""", (code,))


erp_repository = ErpRepository()
