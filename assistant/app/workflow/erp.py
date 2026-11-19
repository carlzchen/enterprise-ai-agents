"""
从 ERP 读数据。全部是写死的参数化查询：结果会直接进报价和给客户的回复，不能有「模型把 SQL 写错了」的可能，所以不用问数的 Text2SQL。
用的是只读账号。真实环境里这一层对应金蝶 EAS 的只读视图。
"""
from datetime import date

from app.infra.erp.erp_repository import erp_repository
from app.workflow import store

_q = erp_repository._query


def customers() -> list[dict]:
    return _q("SELECT id, code, name, industry, region, is_domestic, currency, credit_limit, payment_terms_days FROM md_customer WHERE status = 'ACTIVE' OR status IS NULL")


def customer(customer_id: int) -> dict | None:
    rows = _q("SELECT id, code, name, industry, region, is_domestic, currency, credit_limit, payment_terms_days FROM md_customer WHERE id = %s", (customer_id,))
    return rows[0] if rows else None


def contact_by_email(email: str) -> dict | None:
    rows = _q("SELECT c.customer_id, c.name, c.title, c.email, m.name AS customer_name FROM crm_contact c JOIN md_customer m ON m.id = c.customer_id WHERE LOWER(c.email) = %s", (email.lower(),))
    return rows[0] if rows else None


def primary_contact(customer_id: int) -> dict | None:
    rows = _q("SELECT name, email FROM crm_contact WHERE customer_id = %s ORDER BY is_primary DESC, id LIMIT 1", (customer_id,))
    return rows[0] if rows else None


def suppliers() -> list[dict]:
    return _q("SELECT id, code, name FROM md_supplier")


def products() -> list[dict]:
    return _q("SELECT id, code, name, series, voltage, power_w, rated_speed_rpm, conversion_cost, list_price, revision, status FROM md_product")


def product(code: str) -> dict | None:
    rows = _q("SELECT id, code, name, series, voltage, power_w, rated_speed_rpm, conversion_cost, list_price, revision, status FROM md_product WHERE code = %s", (code,))
    return rows[0] if rows else None


def cost_inputs(code: str) -> dict | None:
    """算成本要用的原始数据：产品的 BOM（展开到有采购价的那一层）、每个物料最新的单价、加工费。计算本身在规则层做"""
    p = product(code)
    if not p:
        return None
    month = _q("SELECT MAX(price_month) m FROM md_material_price")[0]["m"]
    prices = {r["material_id"]: float(r["price"]) for r in _q("SELECT material_id, price FROM md_material_price WHERE price_month = %s", (month,))}
    names = {r["id"]: r for r in _q("SELECT id, code, name FROM md_material")}
    children: dict[int, list] = {}
    for r in _q("SELECT parent_material_id p, child_material_id c, qty_per FROM md_material_bom"):
        children.setdefault(r["p"], []).append((r["c"], float(r["qty_per"])))
    lines, missing = [], []

    def expand(mid: int, qty: float, depth: int = 0) -> None:
        if mid in children and depth < 6:                     # 半成品：往下展开
            for c, q in children[mid]:
                expand(c, qty * q, depth + 1)
        elif mid in prices:
            lines.append({"material": names[mid]["code"], "name": names[mid]["name"], "qty": round(qty, 6), "price": prices[mid]})
        else:
            missing.append(names[mid]["code"])
    for r in _q("SELECT material_id, qty_per FROM md_bom WHERE product_id = %s", (p["id"],)):
        expand(r["material_id"], float(r["qty_per"]))
    return {"model": code, "revision": p["revision"], "conversion_cost": float(p["conversion_cost"] or 0), "price_month": str(month), "lines": lines, "missing": missing}


def last_deal(customer_id: int, code: str) -> dict | None:
    rows = _q("SELECT o.order_no, o.order_date, l.unit_price * o.fx_rate AS unit_price, l.qty FROM sal_order_line l JOIN sal_order o ON o.id = l.order_id JOIN md_product p ON p.id = l.product_id "
              "WHERE o.customer_id = %s AND p.code = %s AND o.status <> 'CANCELLED' ORDER BY o.order_date DESC, o.id DESC LIMIT 1", (customer_id, code))
    return {**rows[0], "unit_price": round(float(rows[0]["unit_price"]), 4), "qty": float(rows[0]["qty"]), "order_date": str(rows[0]["order_date"])} if rows else None


def deal_range(code: str, months: int = 12) -> dict | None:
    rows = _q("SELECT MIN(l.unit_price * o.fx_rate) lo, MAX(l.unit_price * o.fx_rate) hi, AVG(l.unit_price * o.fx_rate) avg_price, COUNT(*) n FROM sal_order_line l JOIN sal_order o ON o.id = l.order_id "
              "JOIN md_product p ON p.id = l.product_id WHERE p.code = %s AND o.status <> 'CANCELLED' AND o.order_date >= DATE_SUB(CURDATE(), INTERVAL %s MONTH)", (code, months))
    r = rows[0]
    return {"low": round(float(r["lo"]), 4), "high": round(float(r["hi"]), 4), "avg": round(float(r["avg_price"]), 4), "orders": r["n"]} if r["n"] else None


def credit(customer_id: int) -> dict:
    """信用数据：额度、未回款、逾期、已下单未发货的金额。可用额度的计算在规则层"""
    c = customer(customer_id)
    ar = _q("SELECT COALESCE(SUM(outstanding_cny), 0) o, COALESCE(SUM(CASE WHEN overdue_days > 0 THEN outstanding_cny ELSE 0 END), 0) od, COALESCE(MAX(overdue_days), 0) d "
            "FROM v_sales_invoice WHERE customer_id = %s AND outstanding_cny > 0", (customer_id,))[0]
    open_orders = _q("SELECT COALESCE(SUM((l.qty - l.shipped_qty) * l.unit_price * o.fx_rate), 0) a FROM sal_order o JOIN sal_order_line l ON l.order_id = o.id "
                     "WHERE o.customer_id = %s AND o.status = 'CONFIRMED'", (customer_id,))[0]["a"]
    return {"credit_limit": float(c["credit_limit"] or 0), "outstanding": float(ar["o"]), "overdue": float(ar["od"]), "max_overdue_days": int(ar["d"]),
            "open_orders": float(open_orders), "payment_terms_days": c["payment_terms_days"]}


def stock(code: str) -> float:
    rows = _q("SELECT COALESCE(SUM(qty), 0) q FROM v_stock WHERE item_type = 'PRODUCT' AND item_code = %s", (code,))
    return float(rows[0]["q"])


def order_status(order_no: str) -> dict | None:
    rows = _q("SELECT o.id, o.order_no, o.order_date, o.required_date, o.status, c.name customer_name, o.customer_id FROM sal_order o JOIN md_customer c ON c.id = o.customer_id WHERE o.order_no = %s", (order_no,))
    if not rows:
        return None
    o = rows[0]
    lines = _q("SELECT p.code model, l.qty, l.shipped_qty FROM sal_order_line l JOIN md_product p ON p.id = l.product_id WHERE l.order_id = %s ORDER BY l.line_no", (o["id"],))
    ships = _q("SELECT delivery_no, delivery_date, status FROM sal_delivery WHERE order_id = %s ORDER BY delivery_date", (o["id"],))
    return {"order_no": o["order_no"], "customer_id": o["customer_id"], "customer_name": o["customer_name"], "status": o["status"], "order_date": str(o["order_date"]),
            "required_date": str(o["required_date"]), "lines": [{"model": x["model"], "qty": float(x["qty"]), "shipped_qty": float(x["shipped_qty"])} for x in lines],
            "deliveries": [{"delivery_no": s["delivery_no"], "date": str(s["delivery_date"]), "status": s["status"]} for s in ships]}


def recent_orders(customer_id: int, limit: int = 5) -> list[dict]:
    return _q("SELECT order_no, order_date, required_date, status FROM sal_order WHERE customer_id = %s ORDER BY order_date DESC LIMIT %s", (customer_id, limit))


def product_params(code: str) -> dict[str, float]:
    """知识库里这个型号的结构化参数（数值型的），选型比对用"""
    rows = store.query("SELECT p.param_key, MAX(p.value_num) v FROM kb_param p JOIN kb_document d ON d.doc_key = p.doc_key AND d.active_version = p.version "
                       "WHERE p.model = %s AND p.value_num IS NOT NULL AND p.value_num > 0 GROUP BY p.param_key", (code,))
    return {r["param_key"]: float(r["v"]) for r in rows}


def today() -> date:
    return date.today()
