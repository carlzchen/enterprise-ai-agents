"""
模拟客户邮件 + 标准答案。

场景（括号里是封数）：新 PO·PDF（14）、新 PO·Excel（10）、PO 变更（8）、重复发送同一张 PO（2）、交期 / 状态询问（8）、
加急（6）、规格询价（8）、复合邮件（8）、干扰邮件（6）。海外客户写英文。

标准答案：
  emails.json  每封邮件：意图（SPEC 的子任务类型）、期望拆出的子任务和引用（PO 号、订单号、型号、要求日期……）
  pos.json     每个 PO 附件：正确的抬头和明细（含映射后的内部物料号）、故意埋的校验问题、变更内容、期望生成的销售订单
埋的校验问题：price_deviation（偏离价目表 5%~12%）、below_moq、pack_multiple、early_delivery（早于标准交期）、unknown_part（料号没登记）、
total_mismatch（PO 总额和明细之和对不上）、duplicate_po（同一张 PO 再发一次）。
"""
import hashlib
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr
from pathlib import Path

from app.workflow.mockdata import docs
from app.workflow.mockdata.world import BASE_DATE, World, describe, po_no

ORDERS_BOX, MARKET_BOX = "orders@kelang.example.com", "market@kelang.example.com"
PRICE_TOLERANCE = 0.03                          # 和 SPEC 默认一致：单价偏离价目表超过 3% 算问题
TZ = timezone(timedelta(hours=8))
MIME = {"pdf": ("application", "pdf"), "xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
SERIES_CN = {"SERVO": "交流伺服电机", "STEPPER": "步进电机", "BLDC": "无刷直流电机", "SHADED_POLE": "罩极电机", "CROSSFLOW": "贯流风机", "GEAR": "齿轮减速电机"}
# 新 PO 埋的问题：24 张里 10 张干净，其余每张 1~2 个问题
NEW_PO_FLAGS = [set(), {"price_deviation"}, set(), {"early_delivery"}, {"unknown_part"}, set(), {"below_moq"}, {"pack_multiple"}, set(),
                {"price_deviation", "early_delivery"}, {"total_mismatch"}, set(), {"unknown_part"}, {"price_deviation"}, set(), {"early_delivery"},
                {"pack_multiple", "price_deviation"}, set(), {"below_moq"}, {"unknown_part", "early_delivery"}, set(), {"price_deviation"},
                {"early_delivery"}, set()]
COMPOUND = [("expedite", "spec_inquiry"), ("po_entry", "delivery_query"), ("po_change", "expedite"), ("delivery_query", "spec_inquiry")] * 2
SPAM = [("zh", "展会邀请", "2026 深圳国际电机展诚邀参展", "尊敬的企业负责人：2026 深圳国际电机与驱动技术展将于 11 月举办，现诚邀贵司参展，展位有限，回复本邮件可获取报价单。", "expo-sz.example.net"),
        ("zh", "推广", "官网排名提升，三个月见效", "您好，我们专业做外贸网站 SEO 和谷歌推广，三个月保证首页排名，现在签约送半年托管服务，详情请回电。", "seo-boost.example.net"),
        ("zh", "物流", "中美海运特价舱位", "老板您好，本周盐田-洛杉矶海运特价舱位开放，整柜运费下调 15%，需要的话请把货量和品名发给我。", "fastship.example.net"),
        ("en", "newsletter", "Motion Control Weekly: Top 5 trends", "This week: humanoid robots drive actuator demand, new EU ecodesign rules for motors, and more. Unsubscribe anytime.", "motionweekly.example.org"),
        ("en", "proposal", "Partnership opportunity", "Dear Sir/Madam, we are a sourcing agent with clients in Europe looking for reliable motor suppliers. Can we schedule a call?", "global-sourcing.example.org"),
        ("en", "phishing", "Your mailbox storage is almost full", "Your mailbox has reached 98% of its quota. Click the link below to upgrade storage within 24 hours.", "mail-admin.example.org")]


def _iso(d) -> str | None:
    return d.isoformat() if d else None


class Generator:
    def __init__(self, world: World, seed: int, out: Path):
        self.w, self.rng, self.out = world, random.Random(seed * 7919 + 17), out
        self.emails: list[dict] = []
        self.pos: dict[str, dict] = {}
        self.po_seq: dict[int, int] = {}
        self.unknown_seq = 0
        self.latest: dict[tuple[int, str], dict] = {}
        self.attached: dict[tuple[int, str, int], str] = {}      # (客户, PO 号, 版本) → 第一次出现时的附件键，用来标重复发送           # 每张 PO 的最新版本：再次变更基于最新版，重复发送也发最新版

    def current(self, po: dict) -> dict:
        return self.latest.get((po["customer_id"], po["po_no"]), po)

    def remember(self, po: dict) -> dict:
        self.latest[(po["customer_id"], po["po_no"])] = po
        return po

    # ---------------------------------------------------------------- PO 内容
    def _qty(self, code: str, kind: str = "ok") -> float:
        moq, pack, _ = self.w.ext[code]
        if kind == "below_moq":
            return float(max(1, moq // 2 // pack) * pack) if pack > 1 else float(max(1, moq // 2))
        q = pack * self.rng.randint(max(1, moq // pack), max(2, moq // pack) * 6)
        return float(q + pack // 2) if kind == "pack_multiple" else float(q)

    def _line(self, cust, code: str, n: int, order_date: date, kinds: set) -> tuple[dict, list[str]]:
        _, pack, lead = self.w.ext[code]
        flags = []
        qkind = "below_moq" if "below_moq" in kinds else ("pack_multiple" if "pack_multiple" in kinds and pack > 1 else "ok")
        if qkind != "ok":
            flags.append(qkind)
        list_price = self.w.prices[(cust.id, code)]
        price = list_price
        if "price_deviation" in kinds:
            price = round(list_price * (1 + self.rng.choice([-1, 1]) * self.rng.uniform(0.05, 0.12)), 2)
            flags.append("price_deviation")
        if "early_delivery" in kinds:
            ddate = order_date + timedelta(days=max(2, lead - self.rng.randint(5, 10)))
            flags.append("early_delivery")
        else:
            ddate = order_date + timedelta(days=lead + self.rng.randint(3, 25))
        part = self.w.part_map[(cust.id, code)]
        if "unknown_part" in kinds:
            self.unknown_seq += 1
            part = f"{part[:-3]}{900 + self.unknown_seq}" if part[-3:].isdigit() else f"{part}-N{self.unknown_seq}"
            flags.append("unknown_part")
        return ({"line_no": n, "customer_part_no": part, "desc": describe(self.w.products[code], cust.lang), "expected_material": code,
                 "qty": self._qty(code, qkind), "unit_price": price, "price_list_price": list_price, "delivery_date": ddate}, flags)

    def flags(self, po: dict) -> list[dict]:
        """按规则重新算一张 PO 的问题标记（标准答案）。规则和 SPEC 一致：单价偏离价目表超过 PRICE_TOLERANCE、低于最小起订量、
        不是包装倍数、交期早于下单日 + 标准交期、客户料号没登记、PO 上的总额和明细之和对不上。变更后的版本也用它重算。
        料号没登记的行只标 unknown_part：系统不知道是哪个产品，依赖物料的检查要等映射确认后再做"""
        out, known = [], {pn for (cid, _), pn in self.w.part_map.items() if cid == po["customer_id"]}
        for ln in po["lines"]:
            if ln["customer_part_no"] not in known:              # 料号没对上就不知道是哪个产品：价格、起订量、交期要等人确认映射后再查
                out.append({"line_no": ln["line_no"], "rule": "unknown_part"})
                continue
            moq, pack, lead = self.w.ext[ln["expected_material"]]
            ref = ln["price_list_price"]
            for rule, hit in (("price_deviation", bool(ref) and abs(ln["unit_price"] - ref) / ref > PRICE_TOLERANCE),
                              ("below_moq", ln["qty"] < moq), ("pack_multiple", ln["qty"] % pack != 0),
                              ("early_delivery", ln["delivery_date"] < po["order_date"] + timedelta(days=lead))):
                if hit:
                    out.append({"line_no": ln["line_no"], "rule": rule})
        if abs(po["printed_total"] - po["computed_total"]) > 0.01:
            out.append({"line_no": None, "rule": "total_mismatch"})
        return out

    def new_po(self, cust, order_date: date, problems: set) -> dict:
        self.po_seq[cust.id] = self.po_seq.get(cust.id, 0) + 1
        codes = list(cust.products)
        self.rng.shuffle(codes)
        n = min(len(codes), self.rng.randint(1, 4))
        line_kinds = [set() for _ in range(n)]
        for p in sorted(problems - {"total_mismatch"}):          # 每个问题落在一行上
            candidates = [i for i in range(n) if not (p == "pack_multiple" and self.w.ext[codes[i]][1] == 1)]
            line_kinds[self.rng.choice(candidates or [0])].add(p)
        lines = [self._line(cust, codes[i], i + 1, order_date, line_kinds[i])[0] for i in range(n)]
        total = round(sum(round(ln["qty"] * ln["unit_price"], 2) for ln in lines), 2)
        printed = total
        if "total_mismatch" in problems:
            printed = round(total + round(lines[0]["qty"] * lines[0]["unit_price"] * 0.1, 2), 2)
        po = {"po_no": po_no(cust, order_date, self.po_seq[cust.id]), "version": 1, "customer_id": cust.id, "order_date": order_date,
              "currency": cust.currency, "payment_days": cust.payment_days, "lines": lines, "computed_total": total, "printed_total": printed,
              "injected": sorted(problems)}
        po["flags"] = self.flags(po)
        return self.remember(po)

    def change_po(self, base: dict, order_date: date) -> dict:
        """在第一版基础上改 1~3 处：数量、交期、加一行、取消一行"""
        cust = self.w.customers[base["customer_id"]]
        lines = [dict(ln) for ln in base["lines"]]
        changes, kinds = [], self.rng.sample(["qty", "date", "add", "cancel"], k=self.rng.randint(1, 3))
        if "cancel" in kinds and len(lines) < 2:
            kinds.remove("cancel")
        if "qty" in kinds:
            ln = self.rng.choice(lines)
            new = self._qty(ln["expected_material"])
            if new == ln["qty"]:
                new += self.w.ext[ln["expected_material"]][1]
            changes.append({"line_no": ln["line_no"], "field": "qty", "from": ln["qty"], "to": new})
            ln["qty"] = new
        if "date" in kinds:
            ln = self.rng.choice(lines)
            new = ln["delivery_date"] + timedelta(days=self.rng.choice([7, 10, 14, -5]))
            changes.append({"line_no": ln["line_no"], "field": "delivery_date", "from": _iso(ln["delivery_date"]), "to": _iso(new)})
            ln["delivery_date"] = new
        cancelled = []
        if "cancel" in kinds:
            ln = lines.pop(self.rng.randrange(len(lines)))
            cancelled.append(ln["line_no"])
            changes = [c for c in changes if c["line_no"] != ln["line_no"]]     # 改了又取消的行：标准答案里只算取消（不动随机序列，开发集不变）
        added = []
        if "add" in kinds:
            spare = [c for c in cust.products if c not in {x["expected_material"] for x in base["lines"]}]   # 新增行用这张 PO 从没出现过的料号
            if spare:
                ln, _ = self._line(cust, self.rng.choice(spare), max(x["line_no"] for x in base["lines"]) + 1, order_date, set())
                lines.append(ln)
                added.append(ln["line_no"])
        total = round(sum(round(x["qty"] * x["unit_price"], 2) for x in lines), 2)
        po = {**base, "version": base["version"] + 1, "order_date": base["order_date"], "lines": lines, "computed_total": total, "printed_total": total,
              "injected": [], "change": {"base_version": base["version"], "changes": changes, "added_lines": added, "cancelled_lines": cancelled}}
        po["flags"] = self.flags(po)
        return self.remember(po)

    def seeded_base(self, s: dict) -> dict:
        """预置的第一版（上个月已录入 ERP）"""
        c = s["content"]
        lines = [{**ln, "expected_material": ln["material"], "delivery_date": date.fromisoformat(ln["delivery_date"]),
                  "price_list_price": self.w.prices.get((s["customer_id"], ln["material"]), ln["unit_price"])} for ln in c["lines"]]
        total = round(sum(round(x["qty"] * x["unit_price"], 2) for x in lines), 2)
        po = {"po_no": s["po_no"], "version": 1, "customer_id": s["customer_id"], "order_date": date.fromisoformat(c["order_date"]),
              "currency": c["currency"], "payment_days": self.w.customers[s["customer_id"]].payment_days, "lines": lines,
              "computed_total": total, "printed_total": total, "injected": [], "erp_order_no": s["order_no"]}
        po["flags"] = self.flags(po)
        return self.latest.get((po["customer_id"], po["po_no"])) or self.remember(po)

    # ---------------------------------------------------------------- 文件
    def attach_po(self, po: dict, email_id: str) -> dict:
        cust = self.w.customers[po["customer_id"]]
        doc = {**po, "buyer": cust.name, "contact": cust.contacts[0][0],
               "ship_to": f"{cust.name}成品仓收货部（{cust.region}）" if cust.lang == "zh" else f"{cust.name}, Receiving Dock"}
        data, ext = docs.render(doc, cust.layout)
        safe = po["po_no"].replace("/", "_")
        name = f"{safe}_v{po['version']}.{ext}" if cust.lang == "en" else (f"采购订单_{safe}_第{po['version']}版.{ext}" if po["version"] > 1 else f"采购订单_{safe}.{ext}")
        key = f"{email_id}/{name}"                               # 不同客户的 PO 号可能相同，附件按邮件分目录存
        (self.out / "attachments" / email_id).mkdir(parents=True, exist_ok=True)
        (self.out / "attachments" / email_id / name).write_bytes(data)
        first = self.attached.setdefault((cust.id, po["po_no"], po["version"]), key)
        flags = list(po["flags"]) + ([{"line_no": None, "rule": "duplicate_po"}] if first != key else [])
        if key not in self.pos:
            self.pos[key] = {"email_id": email_id, "duplicate_of": first if first != key else None, "customer_id": cust.id, "customer_code": cust.code, "layout": cust.layout, "po_no": po["po_no"],
                              "version": po["version"], "order_date": _iso(po["order_date"]), "currency": po["currency"], "payment_days": po["payment_days"],
                              "printed_total": po["printed_total"], "computed_total": po["computed_total"], "flags": flags, "injected": po["injected"],
                              "lines": [{**ln, "delivery_date": _iso(ln["delivery_date"]), "amount": round(ln["qty"] * ln["unit_price"], 2)} for ln in po["lines"]],
                              "change": po.get("change"),
                              "expected_order": {"customer_code": cust.code, "customer_po": po["po_no"], "order_date": _iso(po["order_date"]),
                                                 "currency": po["currency"],
                                                 "lines": [{"material": ln["expected_material"], "qty": ln["qty"], "unit_price": ln["unit_price"],
                                                            "delivery_date": _iso(ln["delivery_date"])} for ln in po["lines"]]}}
        return {"name": name, "key": key, "data": data, "ext": ext}

    def write_eml(self, eid: str, when: datetime, sender: tuple[str, str], to: str, subject: str, body: str, files: list[dict], domain: str) -> str:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = formataddr(sender), to, subject
        msg["Date"], msg["Message-ID"] = format_datetime(when), f"<{eid.lower()}.{hashlib.md5(subject.encode()).hexdigest()[:8]}@{domain}>"
        msg.set_content(body)
        for f in files:
            mt, st = MIME[f["ext"]]
            msg.add_attachment(f["data"], maintype=mt, subtype=st, filename=f["name"])
        if files:
            msg.set_boundary(f"==mock-boundary-{eid}==")
        (self.out / "inbox").mkdir(parents=True, exist_ok=True)
        (self.out / "inbox" / f"{eid}.eml").write_bytes(bytes(msg))
        return msg["Message-ID"]


# ---------------------------------------------------------------- 正文模板

def body_new_po(cust, po, name):
    n = len(po["lines"])
    if cust.lang == "en":
        return f"Hi,\n\nPlease find attached our purchase order {po['po_no']} ({n} line item{'s' if n > 1 else ''}). Kindly confirm receipt and the delivery dates.\n\nBest regards,\n{name}\nPurchasing, {cust.name}"
    return f"您好，\n\n附件是我司采购订单 {po['po_no']}，共 {n} 项，请确认交期并回传订单确认。\n\n谢谢！\n{name}\n{cust.name} 采购部"


def body_change(cust, po, name, rng):
    ch = po["change"]
    detail = rng.random() < 0.5                                 # 一半邮件正文写了改了什么，一半只说「见附件」，逼着去做逐行比对
    if cust.lang == "en":
        txt = f"Hi,\n\nPlease note PO {po['po_no']} has been revised (rev. {po['version']}), see the updated PO attached."
        if detail and ch["changes"]:
            c = ch["changes"][0]
            txt += f" Main change: line {c['line_no']} {c['field'].replace('_', ' ')} changed to {c['to']}."
        return txt + f"\n\nPlease update your order accordingly and reconfirm.\n\nThanks,\n{name}"
    txt = f"您好，\n\n我司订单 {po['po_no']} 有变更，附件为第 {po['version']} 版，请以新版为准。"
    if detail and ch["changes"]:
        c = ch["changes"][0]
        txt += f"主要变化：第 {c['line_no']} 行{'数量' if c['field'] == 'qty' else '交期'}改为 {c['to']}。"
    return txt + f"\n\n请重新回传订单确认，谢谢。\n{name}"


def seg_delivery(cust, ref, by_po: bool, rng, part_map) -> tuple[str, dict]:
    ln = rng.choice(ref["lines"])
    part = part_map[(cust.id, ln["material"])]                  # 客户只知道自己的料号，不知道我方型号
    key = ref["customer_po"] if by_po else ref["order_no"]
    if cust.lang == "en":
        s = (f"Could you let us know the current status of our PO {key}? When will it ship?" if by_po
             else f"What is the shipping status of your order {key} (our P/N {part})?")
    else:
        s = (f"请问我司订单 {key} 现在什么状态？大概什么时候能发货？" if by_po else f"麻烦查一下贵司订单 {key}（我司料号 {part}）的发货进度。")
    return s, {"order_no": ref["order_no"], "customer_po": ref["customer_po"], "asked_by": "customer_po" if by_po else "order_no"}


def seg_expedite(cust, ref, rng, part_map) -> tuple[str, dict]:
    ln = rng.choice(ref["lines"])
    part = part_map[(cust.id, ln["material"])]
    want = max(BASE_DATE + timedelta(days=3), ref["required_date"] - timedelta(days=rng.randint(5, 10)))
    if cust.lang == "en":
        s = f"We urgently need P/N {part} on PO {ref['customer_po']}. Is it possible to pull the delivery in to {want:%Y-%m-%d}? Our line is waiting."
    else:
        s = f"订单 {ref['customer_po']} 里的料号 {part} 我们产线急用，能否提前到 {want:%Y-%m-%d} 交货？请尽快评估回复。"
    return s, {"order_no": ref["order_no"], "customer_po": ref["customer_po"], "material": ln["material"], "customer_part_no": part, "requested_date": _iso(want)}


def seg_spec(cust, w: World, rng, no_match: bool) -> tuple[str, dict]:
    if no_match:                                                 # 我方没有的规格：期望答案是「没有现成型号」
        p = {"series": "SERVO", "voltage": "380VAC", "power_w": 3000}
        vol = rng.choice([500, 800, 1200])
        if cust.lang == "en":
            s = f"We are looking for a {p['voltage']} AC servo motor around 3 kW for a new machine, about {vol} pcs per year. Do you have a suitable model?"
        else:
            s = f"我们新设备需要一款 380V、3kW 左右的交流伺服电机，年用量约 {vol} 台，贵司有合适的型号吗？请推荐并报价。"
        return s, {"expected_material": None, "params": p, "annual_volume": vol}
    code = rng.choice(sorted(w.products))
    prod = w.products[code]
    vol = rng.choice([2000, 5000, 10000, 20000])
    power = f"{float(prod['power_w']):g}W" if prod.get("power_w") else ""
    size = code.split("-")[-1] if prod["series"] == "STEPPER" else ""
    if cust.lang == "en":
        s = (f"We need a {SERIES_EN_LOWER[prod['series']]} for a new project: {prod['voltage']}{', ' + power if power else ''}{', frame ' + size if size else ''}. "
             f"Estimated volume {vol:,} pcs/year. Please recommend a model and send a datasheet.")
    else:
        s = (f"我们在开发一款新产品，需要{SERIES_CN[prod['series']]}，{prod['voltage']}{'、' + power if power else ''}{'、机座 ' + size if size else ''}，"
             f"年用量约 {vol} 台，请推荐型号并提供规格书。")
    return s, {"expected_material": code, "params": {"series": prod["series"], "voltage": prod["voltage"], "power_w": float(prod["power_w"]) if prod.get("power_w") else None},
               "annual_volume": vol}


SERIES_EN_LOWER = {"SERVO": "AC servo motor", "STEPPER": "hybrid stepper motor", "BLDC": "BLDC motor", "SHADED_POLE": "shaded pole motor",
                   "CROSSFLOW": "cross-flow fan", "GEAR": "gear motor"}


def wrap(cust, name, parts: list[str]) -> str:
    if cust.lang == "en":
        return "Hi,\n\n" + "\n\n".join(parts) + f"\n\nBest regards,\n{name}\n{cust.name}"
    return "您好，\n\n" + "\n\n".join(parts) + f"\n\n谢谢！\n{name}\n{cust.name}"


# ---------------------------------------------------------------- 编排

def generate(w: World, seed: int, out: Path) -> dict:
    g = Generator(w, seed, out)
    rng = g.rng
    custs = sorted(w.customers.values(), key=lambda c: c.id)
    pdf_c = [c for c in custs if c.layout in "AB" and len(c.products) >= 2]
    xls_c = [c for c in custs if c.layout in "CD" and len(c.products) >= 2]
    refs_by_c = {}
    for r in w.order_refs:
        refs_by_c.setdefault(r["customer_id"], []).append(r)
    confirmed = [r for r in w.order_refs if r["status"] == "CONFIRMED" and r["required_date"] > BASE_DATE + timedelta(days=8)]
    plan: list[dict] = []                                        # 先列计划，再排时间（变更、重复必须排在原件之后）

    for i, problems in enumerate(NEW_PO_FLAGS):
        cust = (pdf_c if i < 14 else xls_c)[(i * 5) % len(pdf_c if i < 14 else xls_c)]
        plan.append({"kind": "new_po", "cust": cust, "problems": problems, "fmt": "pdf" if i < 14 else "excel"})
    for i in range(8):
        plan.append({"kind": "po_change", "base": ("inbox", i * 3) if i < 5 else ("seeded", i - 5)})
    for i in range(2):
        plan.append({"kind": "duplicate", "base": ("inbox", 2 + i * 9)})
    for i in range(8):
        ref = rng.choice(w.order_refs)
        plan.append({"kind": "delivery_query", "ref": ref, "by_po": i % 3 != 2})
    for _ in range(6):
        plan.append({"kind": "expedite", "ref": rng.choice(confirmed)})
    for i in range(8):
        plan.append({"kind": "spec_inquiry", "cust": rng.choice(custs), "no_match": i in (3, 6)})
    for i, combo in enumerate(COMPOUND):
        plan.append({"kind": "compound", "combo": combo, "i": i})
    for s in SPAM:
        plan.append({"kind": "spam", "spam": s})

    order = list(plan)
    rng.shuffle(order)
    inbox_pos: list[dict] = []
    start = datetime.combine(BASE_DATE - timedelta(days=9), time(8, 30), tzinfo=TZ)

    def place_after_base(seq):                                  # 变更、重复移到原 PO 之后（打乱后再修正依赖）
        out, pending = [], list(seq)
        made = 0
        while pending:
            for p in list(pending):
                need = p.get("base")
                if p["kind"] in ("po_change", "duplicate") and need and need[0] == "inbox" and need[1] >= made:
                    continue
                out.append(p)
                pending.remove(p)
                if p["kind"] == "new_po":
                    made += 1
                break
            else:
                out.extend(pending)
                break
        return out

    for k, p in enumerate(place_after_base(order), start=1):
        eid = f"E{k:03d}"
        when = start + timedelta(minutes=k * 197)
        when_d = when.date()
        gt = {"id": eid, "file": f"inbox/{eid}.eml", "date": when.isoformat(), "scenario": p["kind"], "subtasks": [], "attachments": []}
        files: list[dict] = []
        if p["kind"] == "new_po":
            cust = p["cust"]
            po = g.new_po(cust, when_d, p["problems"])
            inbox_pos.append(po)
            name, email = cust.contacts[k % len(cust.contacts)]
            files.append(g.attach_po(po, eid))
            subject = f"PO {po['po_no']}" if cust.lang == "en" else f"采购订单 {po['po_no']}"
            body = body_new_po(cust, po, name)
            gt["subtasks"].append({"type": "po_entry", "refs": {"po_no": po["po_no"], "attachment": files[0]["key"]}})
            to = ORDERS_BOX
        elif p["kind"] in ("po_change", "duplicate"):
            src, idx = p["base"]
            base = g.current(inbox_pos[min(idx, len(inbox_pos) - 1)] if src == "inbox" else g.seeded_base(w.seeded_pos[idx % len(w.seeded_pos)]))
            cust = w.customers[base["customer_id"]]
            name, email = cust.contacts[0]
            if p["kind"] == "duplicate":
                po = base
                files.append(g.attach_po(po, eid))
                subject = (f"FW: PO {po['po_no']}" if cust.lang == "en" else f"转发：采购订单 {po['po_no']}")
                body = (f"Hi,\n\nResending PO {po['po_no']} in case you did not receive it.\n\n{name}" if cust.lang == "en"
                        else f"您好，\n\n采购订单 {po['po_no']} 再发一次，请查收，避免遗漏。\n\n{name}")
                gt["subtasks"].append({"type": "po_entry", "refs": {"po_no": po["po_no"], "attachment": files[0]["key"], "duplicate_of_version": po["version"]}})
            else:
                po = g.change_po(base, when_d)
                files.append(g.attach_po(po, eid))
                subject = (f"Revised PO {po['po_no']} rev.{po['version']}" if cust.lang == "en" else f"订单变更 {po['po_no']} 第{po['version']}版")
                body = body_change(cust, po, name, rng)
                gt["subtasks"].append({"type": "po_change", "refs": {"po_no": po["po_no"], "attachment": files[0]["key"], "base_version": base["version"],
                                                                      "erp_order_no": base.get("erp_order_no")}})
            to = ORDERS_BOX
        elif p["kind"] in ("delivery_query", "expedite"):
            ref = p["ref"]
            cust = w.customers[ref["customer_id"]]
            name, email = cust.contacts[-1]
            seg, refs = seg_delivery(cust, ref, p["by_po"], rng, w.part_map) if p["kind"] == "delivery_query" else seg_expedite(cust, ref, rng, w.part_map)
            subject = {("zh", "delivery_query"): f"询问订单 {refs['customer_po'] if p.get('by_po') else refs['order_no']} 交期", ("en", "delivery_query"): "Order status inquiry",
                       ("zh", "expedite"): f"【加急】订单 {refs['customer_po']} 申请提前交货", ("en", "expedite"): f"URGENT: pull-in request PO {refs['customer_po']}"}[(cust.lang, p["kind"])]
            body = wrap(cust, name, [seg])
            gt["subtasks"].append({"type": p["kind"], "refs": refs})
            to = ORDERS_BOX
        elif p["kind"] == "spec_inquiry":
            cust = p["cust"]
            name, email = cust.contacts[0]
            seg, refs = seg_spec(cust, w, rng, p["no_match"])
            subject = "Inquiry: motor for new project" if cust.lang == "en" else "新项目电机选型询价"
            body = wrap(cust, name, [seg])
            gt["subtasks"].append({"type": "spec_inquiry", "refs": refs})
            to = MARKET_BOX
        elif p["kind"] == "compound":
            combo = list(p["combo"])
            eligible = lambda c: [r for r in refs_by_c.get(c.id, []) if r["status"] == "CONFIRMED" and r["required_date"] > BASE_DATE + timedelta(days=8)]
            cands = [c for c in custs if len(c.products) >= 2 and refs_by_c.get(c.id)]
            if "po_change" in combo:                             # 先定客户：一封邮件里的所有子任务都属于同一个客户，生成过程中不换人
                cands = [c for c in cands if any(sp["customer_id"] == c.id for sp in w.seeded_pos)]
            if "expedite" in combo:
                ok = [c for c in cands if eligible(c)]
                if not ok:                                       # 没有能加急的在途订单：这封改成问交期
                    combo[combo.index("expedite")] = "delivery_query"
                cands = ok or cands
            cust = cands[(p["i"] * 7 + 3) % len(cands)]
            name, email = cust.contacts[0]
            segs, subj = [], []
            for t in combo:
                if t == "po_entry":
                    po = g.new_po(cust, when_d, set())
                    inbox_pos.append(po)
                    files.append(g.attach_po(po, eid))
                    segs.append(f"Attached is our new PO {po['po_no']}, please confirm." if cust.lang == "en" else f"附件是新的采购订单 {po['po_no']}，请确认。")
                    gt["subtasks"].append({"type": "po_entry", "refs": {"po_no": po["po_no"], "attachment": files[-1]["key"]}})
                    subj.append(f"PO {po['po_no']}")
                elif t == "po_change":
                    base = g.current(g.seeded_base(next(sp for sp in w.seeded_pos if sp["customer_id"] == cust.id)))
                    po = g.change_po(base, when_d)
                    files.append(g.attach_po(po, eid))
                    segs.append(f"PO {po['po_no']} has been revised, see rev.{po['version']} attached." if cust.lang == "en" else f"订单 {po['po_no']} 有变更，附件为第 {po['version']} 版。")
                    gt["subtasks"].append({"type": "po_change", "refs": {"po_no": po["po_no"], "attachment": files[-1]["key"], "base_version": base["version"],
                                                                          "erp_order_no": base.get("erp_order_no")}})
                    subj.append(f"PO {po['po_no']} rev.{po['version']}")
                elif t in ("delivery_query", "expedite"):
                    ref = rng.choice(eligible(cust) if t == "expedite" else refs_by_c[cust.id])
                    seg, refs = seg_delivery(cust, ref, True, rng, w.part_map) if t == "delivery_query" else seg_expedite(cust, ref, rng, w.part_map)
                    segs.append(seg)
                    gt["subtasks"].append({"type": t, "refs": refs})
                    subj.append("交期" if cust.lang == "zh" else "status")
                else:
                    seg, refs = seg_spec(cust, w, rng, False)
                    segs.append(("Also, " if cust.lang == "en" else "另外，") + seg)
                    gt["subtasks"].append({"type": "spec_inquiry", "refs": refs})
                    subj.append("询价" if cust.lang == "zh" else "inquiry")
            subject = " / ".join(subj)
            body = wrap(cust, name, segs)
            to = ORDERS_BOX
        else:
            lang, _, subject, body, domain = p["spam"]
            cust, name, email = None, ("Marketing" if lang == "en" else "市场部"), f"news@{domain}"
            gt["subtasks"].append({"type": "other", "refs": {}})
            to = ORDERS_BOX
        domain = cust.domain if cust else email.split("@")[1]
        if cust and k % 4 == 0:                                  # 四分之一的邮件从公司域名的公共地址发出，测按域名认客户
            email = f"purchasing@{cust.domain}"
        gt["message_id"] = g.write_eml(eid, when, (name, email), to, subject, body, files, domain)
        gt.update({"lang": cust.lang if cust else p["spam"][0], "customer_id": cust.id if cust else None, "from": email, "to": to, "subject": subject,
                   "intents": sorted({s["type"] for s in gt["subtasks"]}), "attachments": [f["key"] for f in files]})
        g.emails.append(gt)

    gt_dir = out / "ground_truth"
    gt_dir.mkdir(parents=True, exist_ok=True)
    (gt_dir / "emails.json").write_text(json.dumps(g.emails, ensure_ascii=False, indent=1), encoding="utf-8")
    (gt_dir / "pos.json").write_text(json.dumps(g.pos, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    counts: dict[str, int] = {}
    for e in g.emails:
        counts[e["scenario"]] = counts.get(e["scenario"], 0) + 1
    flags: dict[str, int] = {}
    for po in g.pos.values():
        for f in po["flags"]:
            flags[f["rule"]] = flags.get(f["rule"], 0) + 1
    files = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file() and p.name != "manifest.json")
    manifest = {"seed": seed, "base_date": BASE_DATE.isoformat(), "emails": len(g.emails), "scenarios": counts, "po_documents": len(g.pos), "flags": flags,
                "layouts": {k: sum(1 for p in g.pos.values() if p["layout"] == k) for k in "ABCD"},
                "files": {f: hashlib.sha256((out / f).read_bytes()).hexdigest() for f in files}}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest
