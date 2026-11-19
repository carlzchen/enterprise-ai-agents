"""
模拟数据的「世界」：从 MiniERP 读客户、联系人、产品、成交历史、在途订单，补上星空里有、MiniERP 里没有的数据，写进 wf_ 表。

  wf_material_ext       每个产品的最小起订量、包装倍数、标准交期（按产品线给）
  wf_price_list         客户价目表 = 近 6 个月该客户该型号的平均成交价（没有成交的用标准价换算）
  wf_customer_part_map  每个客户自己的一套料号（各家编号风格不同）
  wf_order_po_ref       近期在途 / 刚发货的销售订单对应的客户 PO 号
  wf_po_version         几张「上个月已录入」的 PO 第一版，给 PO 变更邮件引用

只改 source='mock' 的行：重复运行结果一样，人工确认 / 流程写入的数据不受影响。不碰 MiniERP 的业务表。
"""
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.workflow import erp, store

BASE_DATE = date(2026, 10, 12)                  # 模拟「今天」：邮件日期、PO 日期都围绕它，固定才能复现
SERIES_EXT = {   # 产品线 → (最小起订量, 包装倍数, 标准交期天数)
    "SHADED_POLE": (1000, 100, 15), "CROSSFLOW": (500, 20, 18), "GEAR": (200, 50, 20),
    "STEPPER": (100, 20, 20), "BLDC": (200, 50, 22), "SERVO": (10, 1, 25),
}
SERIES_EN = {"SERVO": "AC servo motor", "STEPPER": "Hybrid stepper motor", "BLDC": "BLDC motor", "SHADED_POLE": "Shaded pole motor",
             "CROSSFLOW": "Cross-flow fan", "GEAR": "Gear motor"}
FX = {"CNY": 1.0, "USD": 7.1, "EUR": 7.8}
EN_NAMES = ["Anna Lindqvist", "Mark Johnson", "Kenji Sato", "Ji-ho Park", "Lukas Becker", "Emre Yilmaz", "Sophie Martin", "David Miller",
            "Yuki Tanaka", "Marco Rossi", "Laura Schmidt", "Hans Weber"]


@dataclass
class Customer:
    id: int
    code: str
    name: str
    lang: str                                   # zh / en
    currency: str
    domain: str
    contacts: list[tuple[str, str]]             # (姓名, 邮箱)
    part_style: int
    po_style: int
    layout: str                                 # A 中文 PDF / B 英文 PDF / C 中文 Excel / D 英文 Excel
    payment_days: int = 60
    region: str = ""
    products: list[str] = field(default_factory=list)


@dataclass
class World:
    customers: dict[int, Customer]
    products: dict[str, dict]
    ext: dict[str, tuple[int, int, int]]
    prices: dict[tuple[int, str], float]
    part_map: dict[tuple[int, str], str]        # (客户, 内部物料号) → 客户料号
    order_refs: list[dict]                      # 在途订单：order_no, customer_id, customer_po, lines, status, required_date
    seeded_pos: list[dict]                      # 预置的 PO 第一版


def abbr(c: dict) -> str:
    code_letters = "".join(ch for ch in c["name"] if ch.isascii() and ch.isalpha() and ch.isupper())
    return (code_letters[:3] or c["code"].replace("C0", "K")).upper()


def part_no(cust: Customer, product: dict, k: int) -> str:
    series = product["series"][:2]
    if cust.part_style == 0:
        return f"{cust.code}-{series}-{1000 + k * 7:04d}"
    if cust.part_style == 1:
        return f"{abbr({'name': cust.name, 'code': cust.code})}{300000 + k * 131}"
    if cust.part_style == 2:
        return f"M{cust.id:02d}.{series}.{k + 1:03d}"
    return f"{100200 + cust.id * 1000 + k * 17}"


def po_no(cust: Customer, d: date, n: int) -> str:
    return [f"PO{d:%Y%m%d}{n:03d}", f"CG-{d:%Y}-{cust.id:02d}{n:03d}", f"45{cust.id:02d}{d:%m}{n:04d}", f"PO-{cust.code}-{d:%y%m}{n:02d}"][cust.po_style]


def describe(product: dict, lang: str) -> str:
    if lang == "zh":
        return product["name"]
    bits = [SERIES_EN[product["series"]]]
    if product.get("power_w"):
        bits.append(f"{float(product['power_w']):g}W")
    if product.get("voltage"):
        bits.append(product["voltage"])
    bits.append(f"({product['code'].split('-', 2)[-1]})")
    return " ".join(bits)


def load(seed: int) -> World:
    """从 MiniERP 读数据，按固定种子生成各种补充数据（不写库）"""
    rng = random.Random(seed)
    store.ensure()
    q = erp._q
    products = {p["code"]: p for p in sorted(erp.products(), key=lambda p: p["code"]) if (p.get("status") or "ACTIVE") == "ACTIVE"}
    ext = {code: SERIES_EXT[p["series"]] for code, p in products.items()}
    exts = {r["customer_id"]: r for r in store.query("SELECT customer_id, domains FROM wf_customer_ext")}
    contacts = defaultdict(list)
    for r in q("SELECT customer_id, name, email FROM crm_contact WHERE email IS NOT NULL ORDER BY customer_id, is_primary DESC, id"):
        contacts[r["customer_id"]].append((r["name"], r["email"]))
    history = q("SELECT o.customer_id, p.code, AVG(l.unit_price) price, COUNT(*) n FROM sal_order o JOIN sal_order_line l ON l.order_id = o.id "
                "JOIN md_product p ON p.id = l.product_id WHERE o.status <> 'CANCELLED' AND o.order_date >= %s GROUP BY o.customer_id, p.code",
                (BASE_DATE - timedelta(days=183),))
    bought = defaultdict(list)
    prices: dict[tuple[int, str], float] = {}
    for r in sorted(history, key=lambda r: (r["customer_id"], -r["n"], r["code"])):
        if r["code"] in products:
            bought[r["customer_id"]].append(r["code"])
            prices[(r["customer_id"], r["code"])] = round(float(r["price"]), 2)

    customers: dict[int, Customer] = {}
    for c in sorted(erp.customers(), key=lambda c: c["id"]):
        lang = "zh" if c["is_domestic"] else "en"
        domain = ((exts.get(c["id"]) or {}).get("domains") or f"{c['code'].lower()}-corp.example.com").split(",")[0].strip()
        cs = contacts.get(c["id"]) or [(f"{c['name'][:2]}采购", f"purchase@{domain}")]
        if lang == "en":
            cs = [(EN_NAMES[(c["id"] + i) % len(EN_NAMES)], email) for i, (_, email) in enumerate(cs)]
        cust = Customer(id=c["id"], code=c["code"], name=c["name"], lang=lang, currency=c["currency"] or "CNY", domain=domain, contacts=cs,
                        part_style=rng.randrange(4), po_style=rng.randrange(4), layout=rng.choice("AC") if lang == "zh" else rng.choice("BD"),
                        payment_days=int(c.get("payment_terms_days") or 60), region=c.get("region") or "")
        own = bought.get(c["id"], [])[:8]
        if len(own) < 3:                                       # 成交太少的客户补几个同产品线的型号，价格用标准价换算成客户币种
            extra = [code for code in products if code not in own]
            rng.shuffle(extra)
            for code in extra[: 3 - len(own)]:
                own.append(code)
                prices[(c["id"], code)] = round(float(products[code]["list_price"]) / FX.get(cust.currency, 1.0), 2)
        cust.products = own
        customers[c["id"]] = cust

    part_map = {}
    for cust in customers.values():
        for k, code in enumerate(cust.products):
            part_map[(cust.id, code)] = part_no(cust, products[code], k)

    recent = q("SELECT o.id, o.order_no, o.customer_id, o.order_date, o.required_date, o.status FROM sal_order o "
               "WHERE (o.status = 'CONFIRMED' AND o.order_date >= %s) OR (o.status = 'SHIPPED' AND o.order_date >= %s) ORDER BY o.order_date, o.order_no",
               (BASE_DATE - timedelta(days=75), BASE_DATE - timedelta(days=40)))
    lines = defaultdict(list)
    if recent:
        for r in q("SELECT l.order_id, l.line_no, p.code, l.qty, l.shipped_qty, l.unit_price FROM sal_order_line l JOIN md_product p ON p.id = l.product_id "
                   f"WHERE l.order_id IN ({','.join(str(o['id']) for o in recent)}) ORDER BY l.order_id, l.line_no"):
            if r["code"] not in products:
                continue
            lines[r["order_id"]].append({"line_no": r["line_no"], "material": r["code"], "qty": float(r["qty"]), "shipped_qty": float(r["shipped_qty"]),
                                         "unit_price": round(float(r["unit_price"]), 2)})
    order_refs, counters = [], defaultdict(int)
    for o in recent:
        cust = customers.get(o["customer_id"])
        if not cust or not lines[o["id"]]:
            continue
        counters[cust.id] += 1
        for ln in lines[o["id"]]:                               # 在途订单里出现的型号也要有客户料号
            part_map.setdefault((cust.id, ln["material"]), part_no(cust, products[ln["material"]], 50 + len(part_map)))
        order_refs.append({"order_no": o["order_no"], "customer_id": cust.id, "customer_po": po_no(cust, o["order_date"], 900 + counters[cust.id]),
                           "order_date": o["order_date"], "required_date": o["required_date"], "status": o["status"], "lines": lines[o["id"]]})

    seeded = []
    for ref in [r for r in order_refs if r["status"] == "CONFIRMED" and len(r["lines"]) >= 2][:3]:
        cust = customers[ref["customer_id"]]
        seeded.append({"customer_id": cust.id, "po_no": ref["customer_po"], "version": 1, "order_no": ref["order_no"],
                       "content": {"po_no": ref["customer_po"], "order_date": ref["order_date"].isoformat(), "currency": cust.currency,
                                   "lines": [{"line_no": ln["line_no"], "customer_part_no": part_map[(cust.id, ln["material"])], "material": ln["material"],
                                              "desc": describe(products[ln["material"]], cust.lang), "qty": ln["qty"], "unit_price": ln["unit_price"],
                                              "delivery_date": ref["required_date"].isoformat()} for ln in ref["lines"]]}})
    return World(customers=customers, products=products, ext=ext, prices=prices, part_map=part_map, order_refs=order_refs, seeded_pos=seeded)


def seed_db(w: World) -> dict:
    """把补充数据写进 wf_ 表（只替换 source='mock' 的行）"""
    store.ensure()
    for t in ("wf_material_ext", "wf_price_list", "wf_customer_part_map", "wf_order_po_ref", "wf_po_version"):
        store.execute(f"DELETE FROM {t} WHERE source = 'mock'")
    for code, (moq, pack, lead) in w.ext.items():
        store.execute("INSERT INTO wf_material_ext (product_code, moq, pack_qty, std_lead_days, source) VALUES (%s,%s,%s,%s,'mock') "
                      "ON DUPLICATE KEY UPDATE product_code = product_code", (code, moq, pack, lead))
    for (cid, code), price in sorted(w.prices.items()):
        store.execute("INSERT INTO wf_price_list (customer_id, product_code, currency, unit_price, effective_from, source) VALUES (%s,%s,%s,%s,%s,'mock')",
                      (cid, code, w.customers[cid].currency, price, date(2026, 7, 1)))
    for (cid, code), pn in sorted(w.part_map.items()):
        store.execute("INSERT INTO wf_customer_part_map (customer_id, customer_part_no, product_code, customer_desc, source) VALUES (%s,%s,%s,%s,'mock') "
                      "ON DUPLICATE KEY UPDATE customer_part_no = customer_part_no", (cid, pn, code, describe(w.products[code], w.customers[cid].lang)))
    for r in w.order_refs:
        store.execute("INSERT INTO wf_order_po_ref (order_no, customer_id, customer_po, source) VALUES (%s,%s,%s,'mock') "
                      "ON DUPLICATE KEY UPDATE order_no = order_no", (r["order_no"], r["customer_id"], r["customer_po"]))
    for s in w.seeded_pos:
        store.execute("INSERT INTO wf_po_version (customer_id, po_no, version, content, order_no, source) VALUES (%s,%s,1,%s,%s,'mock')",
                      (s["customer_id"], s["po_no"], store.dumps(s["content"]), s["order_no"]))
    templates = {str(c.id): {"A": "cn_pdf_grid", "C": "cn_xlsx"}[c.layout] for c in w.customers.values() if c.layout in "AC"}
    if (store.rule("po_templates").get("customers") or {}) != templates:          # 中文版式的客户配模板，英文客户走大模型；内容没变不新增版本
        store.set_rule("po_templates", {**store.rule("po_templates"), "customers": templates}, "mock")
    return {"po_templates": len(templates), "material_ext": len(w.ext), "price_list": len(w.prices), "part_map": len(w.part_map), "order_po_ref": len(w.order_refs),
            "seeded_po_versions": len(w.seeded_pos)}
