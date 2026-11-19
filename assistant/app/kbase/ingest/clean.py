"""
清洗：
  文件级  重复（文件 SHA-256）、加密、损坏、过大
  文本级  页眉页脚和页码、断行、全半角、型号写法统一
  脱敏    手机号、身份证号、银行卡号、邮箱、敏感词表里的内容，入库前打码（向量库和模型都看不到原文）
  术语    同一个概念的不同写法统一成一种（扭矩→转矩、Nm→N·m、°C→℃、φ→Φ），检索时不会因为写法不同而漏召回
  归一化  切片哈希用的文本：去掉页码标记、页眉页脚、空白和文档标题，只留真正的内容，排版变化不算内容变化
"""
import hashlib
from functools import lru_cache
import re
import unicodedata
import zipfile
from pathlib import Path

MAX_BYTES = 80 * 1024 * 1024
SUPPORTED = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".md": "md", ".markdown": "md", ".txt": "md",
             ".xlsx": "xlsx", ".dxf": "dxf", ".dwg": "dwg", ".png": "image", ".jpg": "image", ".jpeg": "image"}
ROUTE = {"pdf": "doc", "docx": "doc", "pptx": "doc", "md": "doc", "image": "doc", "xlsx": "table", "dxf": "cad", "dwg": "cad"}


class Rejected(Exception):
    """文件本身有问题，重试也没用：加密、损坏、类型不支持、缺密级"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_file(path: Path) -> str:
    """返回文件类型；有问题抛 Rejected"""
    if not path.exists():
        raise Rejected(f"文件不存在：{path.name}")
    kind = SUPPORTED.get(path.suffix.lower())
    if kind is None:
        raise Rejected(f"不支持的文件类型 {path.suffix or '(无扩展名)'}；支持 PDF、Word、PPT、Markdown、Excel、DXF、DWG、PNG、JPG")
    size = path.stat().st_size
    if size == 0:
        raise Rejected("文件是空的（0 字节）")
    if size > MAX_BYTES:
        raise Rejected(f"文件超过 {MAX_BYTES // 1024 // 1024}MB 上限")
    head = path.read_bytes()[:8]
    if kind == "pdf":
        if not head.startswith(b"%PDF"):
            raise Rejected("文件损坏：不是有效的 PDF")
        from pypdf import PdfReader
        try:
            if PdfReader(str(path)).is_encrypted:
                raise Rejected("PDF 已加密，请提供未加密的版本")
        except Rejected:
            raise
        except Exception as e:
            raise Rejected(f"PDF 无法读取：{str(e)[:80]}")
    elif kind in ("docx", "pptx", "xlsx"):
        if head.startswith(b"\xd0\xcf\x11\xe0"):
            raise Rejected("文件已加密或是旧版 Office 格式，请另存为未加密的新格式")
        try:
            with zipfile.ZipFile(path) as z:
                bad = z.testzip()
            if bad:
                raise Rejected(f"文件损坏：{bad}")
        except zipfile.BadZipFile:
            raise Rejected("文件损坏：不是有效的 Office 文件")
    elif kind == "dxf":
        text = path.read_bytes()[:4000]
        if b"SECTION" not in text and not head.startswith(b"AutoCAD Binary DXF"):
            raise Rejected("文件损坏：不是有效的 DXF")
    return kind


_MODEL = re.compile(r"(?<![A-Za-z0-9])KL[\s_\-]*([A-Za-z]{2})((?:[\s_\-]+[0-9A-Za-z]+){1,4})(?![A-Za-z0-9])", re.I)


_WIDE = str.maketrans("－—–＿　", "---_ ")          # 全角连字符、下划线、空格先换成半角


def normalize_model(text: str) -> str:
    """KL SV 80 750W、KL_SV_80_750W、kl-sv-80-750w 都统一成 KL-SV-80-750W。型号的每一段都带数字，遇到不带数字的词就停"""
    def fix(m: re.Match) -> str:
        parts, rest = [], re.split(r"[\s_\-]+", m.group(2).strip(" _-"))
        for i, p in enumerate(rest):
            if not any(ch.isdigit() for ch in p):
                tail = " " + " ".join(rest[i:])
                break
            parts.append(p.upper().replace("X", "x") if re.fullmatch(r"\d+[xX]\d+", p) else p.upper())
        else:
            tail = ""
        return ("-".join(["KL", m.group(1).upper(), *parts]) + tail) if parts else m.group(0)
    return _MODEL.sub(fix, text.translate(_WIDE))


def clean_text(md: str) -> str:
    """全角字母数字转半角、去页码行和重复的页眉页脚、合并多余空行"""
    md = "".join(unicodedata.normalize("NFKC", ch) if "！" <= ch <= "～" and ch not in "，。；：？！（）" else ch for ch in md)
    lines = md.replace("\r\n", "\n").split("\n")
    counts: dict[str, int] = {}
    in_code = False
    for line in lines:
        s = line.strip()
        if re.match(r"^(```|~~~)", s):
            in_code = not in_code
            continue
        if in_code:
            continue
        if 4 <= len(s) <= 40 and not s.startswith(("#", "|", "-", "<!--")):
            counts[s] = counts.get(s, 0) + 1
    repeated = {s for s, n in counts.items() if n >= 5}              # 同一行出现很多次：页眉或页脚
    out, blank, in_code = [], 0, False
    for line in lines:
        s = line.strip()
        if re.match(r"^(```|~~~)", s):
            in_code = not in_code
            out.append(line.rstrip())
            blank = 0
            continue
        if in_code:
            out.append(line.rstrip())
            continue
        if s in repeated or re.fullmatch(r"(第\s*)?\d+\s*(页|/\s*\d+)?", s) or re.fullmatch(r"[-–—]\s*\d+\s*[-–—]", s):
            continue
        blank = blank + 1 if not s else 0
        if blank <= 1:
            out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------- 脱敏、术语统一、归一化

_RES = Path(__file__).resolve().parents[1] / "resources"
_MASK = [(re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)"), r"\1****\2", "手机号"),
         (re.compile(r"(?<![\dA-Za-z])(\d{6})(?:19|20)\d{2}(?:0[1-9]|1[0-2])\d{2}\d{3}[\dXx](?![\dA-Za-z])"), r"\1********", "身份证号"),
         (re.compile(r"(?<!\d)(62\d{4})\d{6,9}(\d{4})(?!\d)"), r"\1******\2", "银行卡号"),
         (re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"), r"\1***@\2", "邮箱")]


def mask_sensitive(md: str) -> tuple[str, dict]:
    """返回（打码后的文本, 各类命中次数）。图片链接里的路径不动"""
    stats: dict[str, int] = {}
    keep = {}

    def hide(m: re.Match) -> str:                          # 图片和链接的地址先藏起来，打码完再放回去
        keep[f"\x00{len(keep)}\x00"] = m.group(0)
        return f"\x00{len(keep) - 1}\x00"
    text = re.sub(r"\]\([^)]*\)", hide, md)
    for pattern, repl, kind in _MASK:
        text, n = pattern.subn(repl, text)
        if n:
            stats[kind] = n
    from app.kbase.query.guard import _words              # 和问答输出共用一张敏感词表
    for w in _words()["脱敏"]:
        text, n = re.subn(re.escape(w), "＊" * len(w), text, flags=re.I)
        if n:
            stats["敏感词"] = stats.get("敏感词", 0) + n
    for k, v in keep.items():
        text = text.replace(k, v)
    return text, stats


@lru_cache(maxsize=1)
def _terms() -> list[tuple[re.Pattern, str]]:
    rules, section = [], None
    for line in (_RES / "terms.txt").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
            continue
        src, dst = (x.strip() for x in s.split("=>"))
        if section == "单位":                               # 单位只在数字后面替换：「3 Nm」换，「Nmax」不换
            rules.append((re.compile(r"(?<=[\d\s])" + re.escape(src) + r"(?![A-Za-z·])"), dst))
        else:
            rules.append((re.compile(re.escape(src)), dst))
    rules.sort(key=lambda r: -len(r[0].pattern))            # 长的先换：「额定力矩」先于「力矩」
    return rules


def unify_terms(md: str) -> tuple[str, int]:
    n = 0
    md = re.sub(r"[φø⌀∅]", lambda m: "Φ", md)
    for pattern, dst in _terms():
        md, k = pattern.subn(dst, md)
        n += k
    return md, n


_PAGE_MARK = re.compile(r"<!--\s*page:\d+\s*-->")
_HEAD = re.compile(r"^【[^】]*】")


def normalize_for_hash(chunk_text: str) -> str:
    """切片哈希用的文本：去掉开头的「【文档标题】」、页码标记、单独成行的页码、所有空白。章节标题保留（章节换了算内容变化）"""
    t = _HEAD.sub("", chunk_text.strip())
    t = _PAGE_MARK.sub("", t)
    t = "\n".join(line for line in t.split("\n") if not re.fullmatch(r"\s*(第\s*\d+\s*页|\d+\s*/\s*\d+|-\s*\d+\s*-)\s*", line))
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", t))


def norm_hash(chunk_text: str) -> str:
    return hashlib.sha256(normalize_for_hash(chunk_text).encode("utf-8")).hexdigest()
