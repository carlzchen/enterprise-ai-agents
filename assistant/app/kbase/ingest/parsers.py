"""
三条解析路线，输出统一的结构：
  doc    PDF / Word / PPT / Markdown → Markdown（带页码标记 <!-- page:N -->）
  table  Excel 选型表 → 结构化参数 + 每个型号一句描述
  cad    DXF / DWG 图纸 → 标题栏、尺寸与公差、技术要求、明细表（读矢量实体，不做 OCR）+ 与 ERP 的交叉校验

解析工具的选择：
  扫描件、带表格或带图片的 PDF → MinerU（云端，版面、表格还原最好，图片导出成文件）
  纯文字 PDF → pypdf；Word → Docling（本地，图片按原位置导出），没装或失败时退到 python-docx；PPT → python-pptx
  扫描件 → 逐页渲染成图片交给视觉模型（Qwen-VL）转写；没有配置视觉模型时退到 MinerU
  图片文件（PNG / JPG）→ 视觉模型转写
  所有路线产生的本地图片统一交给 images.enrich：上传 MinIO 私有存储并让视觉模型写摘要
"""
import re
import subprocess
import tempfile
from pathlib import Path

from app.infra.config.settings import settings
from app.kbase import store
from app.kbase.ingest import params as P
from app.kbase.ingest.clean import Rejected, normalize_model
from app.shared.runtime.logger import logger

PAGE_MARK = "<!-- page:{} -->"
MODEL_RE = re.compile(r"KL-[A-Z]{2}-[0-9A-Zx]+(?:-[0-9A-Z]+)*")


# ---------------------------------------------------------------- 路线一：文档

def _docling(path: Path, img_dir: Path) -> str | None:
    """本地 Docling 转 Markdown；文档里的图片导出到 img_dir，md 里是相对 img_dir 父目录的路径"""
    try:
        from docling.document_converter import DocumentConverter
        from docling_core.types.doc import ImageRefMode
    except Exception:
        return None
    try:
        doc = DocumentConverter().convert(str(path)).document
        img_dir.mkdir(parents=True, exist_ok=True)
        md_file = img_dir.parent / f"{img_dir.name}.md"
        doc.save_as_markdown(md_file, artifacts_dir=img_dir, image_mode=ImageRefMode.REFERENCED)
        return md_file.read_text(encoding="utf-8").replace(str(img_dir.parent) + "/", "")
    except Exception as e:                                   # Docling 处理不了时退到轻量解析，不让整份文档失败
        logger.warning(f"Docling 解析 {path.name} 失败，改用轻量解析：{str(e)[:120]}")
        return None


def _save_image(blob: bytes, ext: str, img_dir: Path) -> str:
    """图片落盘，返回相对 img_dir 父目录的路径；文件名取内容哈希，同一张图只存一份"""
    import hashlib
    img_dir.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(blob).hexdigest()[:16] + "." + ext.lstrip(".").lower().replace("jpeg", "jpg")
    (img_dir / name).write_bytes(blob)
    return f"{img_dir.name}/{name}"


def _docx(path: Path, img_dir: Path) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(str(path))
    out = []
    for child in d.element.body.iterchildren():              # 按文档里的实际顺序走，段落和表格不乱序
        if child.tag.endswith("}p"):
            p = Paragraph(child, d)
            text = p.text.strip()
            for rid in child.xpath(".//a:blip/@r:embed"):         # 段落里的内嵌图片，保持原位置
                part = d.part.related_parts.get(rid)
                if part is not None and part.content_type.startswith("image/"):
                    out.append(f"![]({_save_image(part.blob, part.content_type.split('/')[-1], img_dir)})")
            if not text:
                continue
            level = re.search(r"(?:heading|标题)\s*(\d)", (p.style.name or "").lower())
            out.append(("#" * min(int(level.group(1)), 4) + " " if level else "") + text)
        elif child.tag.endswith("}tbl"):
            rows = [[c.text.strip().replace("\n", " ") for c in r.cells] for r in Table(child, d).rows]
            if rows:
                out.append("| " + " | ".join(rows[0]) + " |")
                out.append("|" + "---|" * len(rows[0]))
                out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n\n".join(out)


def _pptx(path: Path, img_dir: Path) -> str:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    out = []
    for i, slide in enumerate(Presentation(str(path)).slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None and slide.shapes.title.has_text_frame else f"第 {i} 页"
        out.append(PAGE_MARK.format(i))
        out.append(f"## {title}")
        for shape in slide.shapes:
            if shape == slide.shapes.title:
                continue
            if shape.has_text_frame and shape.text_frame.text.strip():
                out.append(shape.text_frame.text.strip())
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                out.append(f"![]({_save_image(shape.image.blob, shape.image.ext, img_dir)})")
            if getattr(shape, "has_table", False) and shape.has_table:
                rows = [[c.text.strip() for c in r.cells] for r in shape.table.rows]
                out.append("| " + " | ".join(rows[0]) + " |")
                out.append("|" + "---|" * len(rows[0]))
                out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n\n".join(out)


def _pdf_text(path: Path) -> tuple[str, float]:
    """直接抽文字。返回（带页码标记的文本, 平均每页字数）；每页字数很少说明是扫描件"""
    from pypdf import PdfReader
    pages = [(p.extract_text() or "").strip() for p in PdfReader(str(path)).pages]
    text = "\n\n".join(f"{PAGE_MARK.format(i)}\n{t}" for i, t in enumerate(pages, start=1))
    return text, (sum(len(t) for t in pages) / max(1, len(pages)))


def pdf_layout_pages(path: Path) -> list[str]:
    """Keep PDF column positions for native text and table interpretation."""
    import pdfplumber
    import textwrap
    with pdfplumber.open(path) as document:
        return [textwrap.dedent("\n".join(line.rstrip() for line in (page.extract_text(layout=True, x_density=3, y_density=6) or "").splitlines())).strip()
                for page in document.pages]


def _pdf_images(path: Path) -> int:
    """PDF 里有信息量的内嵌图片张数：按图片对象去重，太小的图标不算，页数 ≥ 4 时每页都出现的（页眉 Logo）不算。
    只看位图；用矢量线条画的图（例如 CAD 导出的接线图）看不出来，会被当成没有图片"""
    from collections import Counter
    from pypdf import PdfReader
    try:
        pages = PdfReader(str(path)).pages
        seen, total = Counter(), len(pages)
        for page in pages:
            xobjects = (page.get("/Resources") or {}).get("/XObject")
            ids = set()
            for ref in (xobjects.get_object().values() if xobjects else []):
                o = ref.get_object()
                w, h = o.get("/Width", 0), o.get("/Height", 0)
                if o.get("/Subtype") == "/Image" and min(w, h) >= 100 and w * h >= 30000:
                    ids.add(getattr(ref, "idnum", id(o)))
            seen.update(ids)
        return sum(1 for n in seen.values() if total < 4 or n <= total / 2)
    except Exception as e:
        logger.warning(f"统计 {path.name} 的内嵌图片失败，按没有图片处理：{str(e)[:100]}")
        return 0


def _pdf_needs_mineru(path: Path, text: str, per_page: float) -> str | None:
    """纯文字 PDF 返回 None；需要 MinerU 的返回原因"""
    if per_page < 80:
        return f"每页只有 {per_page:.0f} 字，像扫描件"
    lines = [x for x in text.split("\n") if x.strip() and not x.startswith("<!--")]
    table_like = sum(len(x.strip()) <= 12 for x in lines) / max(1, len(lines))      # 表格被抽成"一格一行"时短行会很多
    if table_like >= 0.35:
        return f"短行占 {table_like:.0%}，疑似带表格"
    n = _pdf_images(path)
    return f"含 {n} 张内嵌图片" if n else None


def parse_doc(path: Path, kind: str, work_dir: Path, doc_key: str = "", title: str = "") -> dict:
    """返回 {markdown, parsed_by, images}。images 是这份文档里每张图的登记信息（对象路径、哈希、摘要）"""
    from app.kbase.ingest import images
    img_dir, found = Path(work_dir) / "images", []
    if kind == "md":
        md = path.read_text(encoding="utf-8", errors="replace")
        return {"markdown": images.enrich(md, path.parent, doc_key, sink=found), "parsed_by": "markdown", "images": found}
    if kind == "image":                                                         # 图片文件：视觉模型转写
        md = images.image_file_to_markdown(path, title or path.stem)
        return {"markdown": images.enrich(md, path.parent, doc_key, sink=found), "parsed_by": "qwen-vl" if images.vl_available() else "image",
                "images": found}
    if kind == "docx":
        md = _docling(path, img_dir)
        return {"markdown": images.enrich(md or _docx(path, img_dir), Path(work_dir), doc_key, sink=found),
                "parsed_by": "docling" if md else "python-docx", "images": found}
    if kind == "pptx":                                                          # 要保留"第几页"，自己按页读
        return {"markdown": images.enrich(_pptx(path, img_dir), Path(work_dir), doc_key, sink=found), "parsed_by": "python-pptx", "images": found}
    text, per_page = _pdf_text(path)
    reason = _pdf_needs_mineru(path, text, per_page)
    if reason is None:                                                          # 纯文字 PDF：本地解析，保留页码
        pages = pdf_layout_pages(path)
        markdown = "\n\n".join(f"## PDF page {i}\n{PAGE_MARK.format(i)}\n```text\n{page}\n```"
                                for i, page in enumerate(pages, 1))
        return {"markdown": markdown, "parsed_by": "pdfplumber-layout", "images": []}
    if reason.startswith("每页只有") and images.vl_available():                # 扫描件：逐页渲染交给视觉模型转写
        logger.info(f"[kbase] {path.name} 逐页交给视觉模型：{reason}")
        return {"markdown": images.scanned_pdf_to_markdown(path, Path(work_dir)), "parsed_by": "qwen-vl", "images": []}
    logger.info(f"[kbase] {path.name} 交给 MinerU：{reason}")
    from app.kbase.ingest import mineru as pdf_parse_service                    # 带表格或带图片：MinerU 还原版面、表格并导出图片
    md_file = Path(pdf_parse_service.pdf_to_markdown(str(path), str(work_dir)))
    return {"markdown": images.enrich(md_file.read_text(encoding="utf-8"), md_file.parent, doc_key, sink=found), "parsed_by": "mineru", "images": found}


_HEADING = re.compile(r"^(?:第[一二三四五六七八九十\d]+[章节]|\d+(?:\.\d+){0,2})\s+\S.{0,28}$")
_CODE_HEADING = re.compile(r"^[A-Z]{1,3}\.?\d{2,4}\s+\S.{0,20}$")


def _headings(text: str) -> str:
    """直接抽出来的文字没有标题层级：把"1 型号与参数""3.2 接线"这类短行还原成标题，切片才能按章节走"""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        if _HEADING.match(s) and not s.endswith(("。", "；", "，")):
            out.append(("### " if "." in s.split()[0] else "## ") + s)
        elif _CODE_HEADING.match(s):
            out.append("### " + s)
        else:
            out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------- 路线二：表格

def parse_table(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    params, lines, rows_total = [], [], 0
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True) if any(c not in (None, "") for c in r)]
        head_at = next((i for i, r in enumerate(rows) if sum(c not in (None, "") for c in r) >= 3
                        and any(re.search(r"型号|model|产品编码|物料编码", str(c), re.I) for c in r if c)), None)
        if head_at is None:
            continue
        header = [str(c).strip() if c is not None else "" for c in rows[head_at]]
        model_col = next(i for i, h in enumerate(header) if re.search(r"型号|model|产品编码|物料编码", h, re.I))
        cols = [(i, *P.split_header(h)) for i, h in enumerate(header) if h and i != model_col]
        lines.append(f"## {ws.title}")
        for n, r in enumerate(rows[head_at + 1:], start=head_at + 2):
            model = normalize_model(str(r[model_col]).strip()) if model_col < len(r) and r[model_col] else ""
            if not model:
                continue
            rows_total += 1
            mine = []
            for i, name, unit in cols:
                if i >= len(r) or r[i] in (None, ""):
                    continue
                num, text, u = P.parse_value(r[i], unit)
                mine.append({"model": model, "param_key": P.param_key(name) or name[:40], "param_name": name, "value_num": num,
                             "value_text": text[:200], "unit": u, "source_ref": f"{ws.title} 第 {n} 行"})
            params.extend(mine)
            lines.append(P.describe(model, mine))
    if not params:
        raise Rejected("表格里没有找到带「型号」列的参数表")
    return {"markdown": "\n\n".join(lines), "parsed_by": "openpyxl", "params": params, "table_rows": rows_total,
            "models": sorted({p["model"] for p in params})}


# ---------------------------------------------------------------- 路线三：图纸

DIM_LABEL = {"flange_size": "法兰边长", "spigot_dia": "止口直径", "shaft_dia": "轴径", "shaft_len": "轴伸长度",
             "pcd": "安装孔分度圆", "hole_dia": "安装孔直径", "hole_count": "安装孔数量", "thread": "螺纹"}


def dwg_to_dxf(path: Path) -> Path:
    """DWG 是私有二进制格式，先用 ODA File Converter 转成 DXF"""
    exe = settings.oda_converter
    if not exe or not Path(exe).exists():
        raise Rejected("收到 DWG 图纸，但服务器没有配置 ODA File Converter，无法转换；请配置 ODA_CONVERTER，或者上传 DXF")
    out_dir = Path(tempfile.mkdtemp(prefix="oda_"))
    r = subprocess.run([exe, str(path.parent), str(out_dir), "ACAD2018", "DXF", "0", "1", path.name], capture_output=True, timeout=180)
    out = out_dir / (path.stem + ".dxf")
    if r.returncode != 0 or not out.exists():
        raise Rejected(f"DWG 转 DXF 失败：{(r.stderr or r.stdout).decode(errors='replace')[:150]}")
    return out


def read_dxf(path: Path) -> dict:
    """读矢量实体（实现见 cad.py：模型空间 + 图纸空间、块展开、几何块文字、堆叠公差、明细表网格重建……）"""
    from app.kbase.ingest import cad
    return cad.read(path)


def cross_check(drawing: dict, product_code: str | None) -> tuple[list[str], set[str]]:
    """图纸和金蝶数据的交叉校验（确定性规则，不让模型判断）。返回（发现的问题, 在金蝶里能对上的物料编码）"""
    issues, matched = [], set()
    codes = [r[1].strip() for r in drawing["bom"] if r[1].strip()]
    if codes:
        known = {r["f_number"]: r for r in store.query("SELECT f_number, f_name FROM kd_material WHERE f_number IN %s", (codes,))}
        matched = set(known)
        for c in codes:
            if c not in known:
                issues.append(f"图纸明细表里的物料 {c} 在金蝶物料档案里不存在")
    erp = {}
    if product_code:
        if not store.one("SELECT 1 FROM kd_material WHERE f_number = %s", (product_code,)):
            issues.append(f"图纸对应的产品 {product_code} 在金蝶物料档案里不存在")
        erp = {r["f_child"]: float(r["qty"]) for r in store.query(
            "SELECT f_child, f_numerator / NULLIF(f_denominator, 0) AS qty FROM kd_bom WHERE f_parent = %s", (product_code,))}
    for _, code, name, qty in drawing["bom"]:
        code = code.strip()
        try:
            q = float(qty)
        except ValueError:
            continue
        if not name.lstrip().startswith("└") and code in erp and abs(erp[code] - q) > 1e-6:      # 只比一级子件
            issues.append(f"物料 {code} 的用量不一致：图纸 {q:g}，金蝶 BOM {erp[code]:g}")
    shaft = drawing["dims"].get("shaft_dia", {}).get("value")
    for _, code, name, _q in drawing["bom"]:
        m = re.search(r"转轴\s*[ΦφØ⌀](\d+(?:\.\d+)?)", name)
        if m and shaft is not None and abs(float(m.group(1)) - shaft) > 0.01:
            issues.append(f"图纸轴径 Φ{shaft:g} 与明细表物料 {code.strip()}（{name.strip(' └')}）的轴径 Φ{m.group(1)} 不一致，需工程确认")
    return issues, matched


_KIND_CN = {"dia": "直径", "radius": "半径", "linear": "线性", "thread": "螺纹", "angle": "角度"}


def _tol_text(v: dict) -> str:
    if v.get("plus") is None:
        return ""
    if v.get("minus") is not None and abs(v["plus"] + v["minus"]) < 1e-9 and v["plus"]:
        return f" ±{v['plus']:g}"
    return f" {v['plus']:+g}/{v['minus']:+g}"


def cad_markdown(d: dict, issues: list[str]) -> str:
    t, card = d["title"], d.get("card") or {}
    out = [f"# {t.get('NAME', '')}（图号 {t.get('DWG_NO', '')} 版本 {t.get('REV', '')}）", "",
           f"图号：{t.get('DWG_NO', '')}；版本：{t.get('REV', '')}；比例：{t.get('SCALE', '')}；设计：{t.get('DESIGNER', '')}；日期：{t.get('DATE', '')}"
           + (f"；材料：{t['MATERIAL']}" if t.get("MATERIAL") else ""), "",
           "## 关键尺寸（单位 mm）", "", "| 特征 | 公称值 | 上偏差 | 下偏差 | 配合 |", "|---|---|---|---|---|"]
    for k, label in DIM_LABEL.items():
        v = d["dims"].get(k)
        if v:
            out.append(f"| {label} | {v['value']:g} | {v.get('plus') if v.get('plus') is not None else '-'} | "
                       f"{v.get('minus') if v.get('minus') is not None else '-'} | {v.get('fit') or '-'} |")
    if d["dims"].get("thread"):
        th = d["dims"]["thread"]
        out.append(f"| 螺纹 | M{th['value']:g}{'×' + format(th['pitch'], 'g') if th.get('pitch') else ''} | - | - | - |")
    others = [x for x in card.get("dims", []) if x.get("source") == "exploded" or x.get("fit") or x.get("kind") in ("thread", "radius")]
    if others:
        out += ["", "## 其他标注", ""]
        out += [f"- {_KIND_CN.get(x['kind'], x['kind'])} {('M' if x['kind'] == 'thread' else 'Φ' if x['kind'] == 'dia' else 'R' if x['kind'] == 'radius' else '')}"
                f"{x['value']:g}{_tol_text(x)}{(' ' + x['fit']) if x.get('fit') else ''}{('（' + str(x['count']) + ' 处）') if x.get('count') else ''}"
                f"（原文 {x['text']}）" for x in others]
    out += ["", "## 技术要求", "", *d["notes"], "", "## 明细表（BOM）", "", "| 序号 | 代码 | 名称 | 数量 |", "|---|---|---|---|"]
    out += [f"| {a} | {b} | {c.strip()} | {q} |" for a, b, c, q in d["bom"]]
    if issues:
        out += ["", "## 一致性校验", "", *[f"- {i}" for i in issues]]
    if card.get("missing"):
        out += ["", "## 解析缺项", "", f"自动解析没有读到：{'、'.join(card['missing'])}。" + (f"视觉模型复核：{card['review_note']}" if card.get("review_note") else "已转人工复核。")]
    return "\n".join(out) + "\n"


def parse_cad(path: Path, kind: str, work_dir: Path | None = None) -> dict:
    """一张图 → 结构化卡片 + Markdown。缺项的图渲染成 PNG 交给视觉模型复核（没有视觉模型就等人工）"""
    from app.kbase.ingest import cad
    dxf = dwg_to_dxf(path) if kind == "dwg" else path
    d = read_dxf(dxf)
    render = None
    if d["card"]["needs_review"] and work_dir is not None:
        render = cad.render_png(dxf, Path(work_dir) / "render.png")
        if render:
            d["card"]["review_note"] = cad.vl_review(render, d["card"]["missing"])
            d["card"]["render"] = render.name
    dwg_no = d["title"].get("DWG_NO", "")
    m = MODEL_RE.search(dwg_no) or MODEL_RE.search(path.stem.upper())
    product = m.group(0) if m else None
    if product and re.search(r"-\d{2}$", product) and not store.one("SELECT 1 FROM kd_material WHERE f_number = %s", (product,)):
        product = re.sub(r"-\d{2}$", "", product)             # 图号末尾的张号（-01）不属于型号
    issues, matched = cross_check(d, product)
    d["card"].update(product_code=product, erp_issues=issues)
    return {"markdown": cad_markdown(d, issues), "parsed_by": "ezdxf" + ("+oda" if kind == "dwg" else "") + ("+qwen-vl" if d["card"].get("review_note") else ""),
            "drawing": d, "product_code": product, "issues": issues, "matched": sorted(matched), "render": str(render) if render else None}
