"""
邮件接入：把邮件读进来、去重、归并线程、去掉引用的历史正文、认出发件人是谁。这一层不调用大模型。
  从哪个邮箱取信由邮箱适配器决定（app/workflow/adapters：mock 读本地 eml 目录，graph / imap 读真实邮箱）
唯一键是邮件的 Message-ID：同一封邮件重复拉取只会处理一次。
"""
import email
import email.policy
import hashlib
import re
from datetime import datetime
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from app.shared.runtime.logger import logger
from app.workflow import erp, store

FREE_DOMAINS = {"qq.com", "163.com", "126.com", "gmail.com", "outlook.com", "hotmail.com", "foxmail.com", "sina.com", "yahoo.com"}
_QUOTE_MARKS = [re.compile(p, re.I | re.M) for p in (
    r"^在\s*.{0,60}写道[：:]\s*$", r"^On .{0,120}wrote:\s*$", r"^-{2,}\s*Original Message\s*-{2,}", r"^-{2,}\s*原始邮件\s*-{2,}", r"^_{10,}\s*$",
    r"^发件人[：:].*\n(?:.*\n){0,3}?(?:发送时间|日期)[：:]", r"^From:.*\n(?:.*\n){0,3}?Sent:", r"^-{2,}\s*回复的原邮件\s*-{2,}")]
_FWD = re.compile(r"-{2,}\s*(?:Forwarded message|转发的邮件|转发邮件信息|原始邮件)\s*-{2,}", re.I)
_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|回复|答复|转发)\s*[:：]\s*)+", re.I)


# 【讲解】去引用：客户回信时下面会带着整段历史邮件。如果不切掉，模型会把我们上一封报价里的数字（比如旧单价）当成客户新说的话。
# 做法纯规则：找到「在…写道」「On … wrote:」「-----Original Message-----」这类分隔行，从那里截断，再去掉以 > 开头的引用行。
def strip_quotes(body: str) -> str:
    """只留这封邮件新写的内容：切掉「某人写道」之后的引用，以及每行以 > 开头的引用"""
    text = body.replace("\r\n", "\n")
    cut = len(text)
    for p in _QUOTE_MARKS:
        m = p.search(text)
        if m:
            cut = min(cut, m.start())
    lines = [l for l in text[:cut].split("\n") if not l.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def forwarded_sender(body: str) -> str | None:
    """转发邮件：真实的发件人写在正文里"""
    m = _FWD.search(body)
    if not m:
        return None
    f = re.search(r"^(?:From|发件人)\s*[:：]\s*(.+)$", body[m.end():m.end() + 600], re.I | re.M)
    addr = parseaddr(f.group(1))[1] if f else ""
    return addr.lower() if "@" in addr else None


def forwarded_body(body: str) -> str:
    m = _FWD.search(body)
    if not m:
        return body
    rest = body[m.end():]
    parts = re.split(r"\n\s*\n", rest, maxsplit=1)             # 跳过转发头（发件人、主题、日期那几行）
    return (body[:m.start()].strip() + "\n\n" + (parts[1] if len(parts) > 1 else rest)).strip()


def subject_key(subject: str) -> str:
    return re.sub(r"\s+", " ", _PREFIX.sub("", subject or "")).strip().lower()


def language(text: str) -> str:
    """回信用什么语言：正文里中文字占到字母总数的两成以上就算中文"""
    zh, en = len(re.findall(r"[\u4e00-\u9fff]", text)), len(re.findall(r"[A-Za-z]", text))
    return "zh" if zh and zh / (zh + en) >= 0.2 else "en"


def _attachment_text(name: str, data: bytes) -> str:
    """附件转成文字。PO 和询价表大多是 Excel 或 PDF"""
    low = name.lower()
    try:
        if low.endswith((".xlsx", ".xlsm")):
            import io

            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            out = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c).strip() for c in row if c not in (None, "")]
                    if cells:
                        out.append(" | ".join(cells))
            return "\n".join(out)[:8000]
        if low.endswith(".pdf"):
            import io

            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)[:8000]
        if low.endswith((".txt", ".csv", ".md")):
            return data.decode("utf-8", "replace")[:8000]
    except Exception as e:
        logger.warning(f"[workflow] 附件 {name} 解析失败：{str(e)[:100]}")
    return ""


def parse(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body, attachments = "", []
    for part in msg.walk():
        if part.is_multipart():
            continue
        name = part.get_filename()
        if name:
            data = part.get_payload(decode=True) or b""
            text = _attachment_text(name, data)
            attachments.append({"name": name, "size": len(data), "parsed": bool(text), "text": text})
        elif part.get_content_type() == "text/plain" and not body:
            body = part.get_content()
    name, addr = parseaddr(str(msg.get("From", "")))
    try:
        when = parsedate_to_datetime(str(msg.get("Date"))).replace(tzinfo=None)
    except Exception:
        when = datetime.now()
    mid = (msg.get("Message-ID") or "").strip() or "<sha1-" + hashlib.sha1(raw).hexdigest() + ">"
    refs = re.findall(r"<[^>]+>", str(msg.get("References", "")))
    return {"message_id": mid, "in_reply_to": (msg.get("In-Reply-To") or "").strip() or None, "references": refs, "from_addr": addr.lower(), "from_name": name,
            "to": [a.lower() for _, a in getaddresses([str(msg.get("To", ""))])], "subject": str(msg.get("Subject", "")), "received_at": when.replace(microsecond=0),
            "body": body, "attachments": attachments}


# 【讲解】认发件人：先按联系人邮箱精确匹配，再按公司域名匹配。qq、163、gmail 这类公共邮箱不按域名猜（谁都能注册），
# 认不出就标 UNKNOWN，后面会进人工待确认队列——宁可让人看一眼，也不能把邮件挂到错的客户名下。
def identify(addr: str) -> dict:
    """认出发件人是哪个客户或供应商。先按联系人邮箱精确匹配，再按域名；公共邮箱（qq、163、gmail 等）不按域名猜"""
    c = erp.contact_by_email(addr)
    if c:
        return {"type": "CUSTOMER", "id": c["customer_id"], "name": c["customer_name"], "contact": c["name"], "by": "联系人邮箱"}
    domain = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    if domain and domain not in FREE_DOMAINS:
        row = store.one("SELECT e.customer_id FROM wf_customer_ext e WHERE FIND_IN_SET(%s, e.domains)", (domain,))
        if row:
            cu = erp.customer(row["customer_id"])
            return {"type": "CUSTOMER", "id": cu["id"], "name": cu["name"], "contact": None, "by": "邮箱域名"}
        sid = store.rule("supplier_domains").get("domains", {}).get(domain) if store.one("SELECT 1 FROM wf_rule WHERE name = 'supplier_domains' LIMIT 1") else None
        if sid:
            s = next((x for x in erp.suppliers() if x["id"] == sid), None)
            if s:
                return {"type": "SUPPLIER", "id": s["id"], "name": s["name"], "contact": None, "by": "邮箱域名"}
    return {"type": "UNKNOWN", "id": None, "name": None, "contact": None, "by": "公共邮箱，没法按域名判断" if domain in FREE_DOMAINS else "陌生域名"}


def _thread_of(m: dict) -> str:
    """线程归并：先看回复关系（In-Reply-To、References），再看同一个往来方的同一个主题，都没有就新开一条"""
    for ref in [m["in_reply_to"], *reversed(m["references"])]:
        if ref:
            row = store.one("SELECT thread_id FROM wf_mail WHERE message_id = %s", (ref,))
            if row:
                return row["thread_id"]
    key = subject_key(m["subject"])
    if key and m.get("party_id"):
        row = store.one("SELECT thread_id FROM wf_mail WHERE party_id = %s AND party_type = %s AND LOWER(subject) LIKE %s ORDER BY received_at DESC LIMIT 1",
                        (m["party_id"], m["party_type"], "%" + key[:80].replace("%", "") + "%"))
        if row:
            return row["thread_id"]
    return "T" + hashlib.sha1(m["message_id"].encode()).hexdigest()[:16]


# 【讲解】幂等接入：以邮件的 Message-ID 为唯一键，同一封邮件不管被拉取多少次都只处理一次（返回 None）。
# 转发邮件先从正文里找出真实发件人，再识别客户；入库的是去掉引用之后的正文。
def ingest(raw: bytes, raw_path: str = "") -> str | None:
    """收一封邮件。返回 Message-ID；已经收过的返回 None"""
    store.ensure()
    m = parse(raw)
    if store.one("SELECT 1 FROM wf_mail WHERE message_id = %s", (m["message_id"],)):
        return None
    real = forwarded_sender(m["body"])
    body = forwarded_body(m["body"]) if real else m["body"]
    who = identify(real or m["from_addr"])
    m.update(party_type=who["type"], party_id=who["id"])
    new = strip_quotes(body)
    store.execute(
        "INSERT IGNORE INTO wf_mail (message_id, thread_id, in_reply_to, from_addr, from_name, real_from, subject, received_at, body_new, attachments, raw_path, "
        "party_type, party_id, party_name, lang) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (m["message_id"], _thread_of(m), m["in_reply_to"], m["from_addr"], m["from_name"] or who.get("contact"), real, m["subject"][:500], m["received_at"], new,
         store.dumps(m["attachments"]), raw_path, who["type"], who["id"], who["name"], language(new or m["subject"])))
    return m["message_id"]


def pull() -> list[str]:
    """拉一次邮箱，返回这次新收到的 Message-ID"""
    from app.workflow import adapters
    new = []
    for item in adapters.email().fetch_new():
        raw, path = item.raw, item.source
        try:
            mid = ingest(raw, path)
            if mid:
                new.append(mid)
        except Exception:
            logger.exception(f"[workflow] 邮件 {path} 接入失败")
    return new
