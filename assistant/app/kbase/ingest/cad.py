"""
图纸结构化（ezdxf 读矢量实体，不做 OCR）。DWG 先经 ODA File Converter 转成 DXF（ACAD2018、UTF-8），见 parsers.dwg_to_dxf。

  遍历    模型空间 + 所有图纸空间布局；块参照（INSERT）递归展开成实际的线和字（标题栏块只读属性，不展开）
  尺寸    每个标注按这个顺序取值，取到为止：
            1. 标注几何块里的文字（图面上实际显示的那串，含 MTEXT 的 \\S 堆叠公差，如 %%c70{\\S 0.000^ -0.030;}）
            2. 标注的文字覆盖（<> 代表测量值）
            3. 测量值 + 标注样式里的公差（dimtol / dimtp / dimtm）
          已经被炸开的标注（只剩线和字）：在标注图层上的文字按正则识别
          支持 Φ（%%c、Ø、⌀）、R、M 螺纹（含螺距）、±（%%p）、上下偏差、H7 / h6 / H7/g6 这类配合代号、「4×」数量
  明细表  从表格线重建网格（横线定行、竖线定列），文字按坐标落进格子；表格线不全时按表头列位置 + 纵坐标聚类兜底
          识别 GB 明细栏（表头在下、序号自下而上），按序号排序；只有名称没有序号和代号的行是上一项的续行，合并
  标题栏  块属性（图号、名称、版本、比例、设计、日期、材料，中英文属性名都认）
  卡片    每张图一张结构化卡片：标题栏、全部尺寸、关键特征、明细表、技术要求、缺了什么；缺项的图渲染成图片交给视觉模型复核
"""
import re
from bisect import bisect_right
from pathlib import Path

from app.kbase.ingest.clean import Rejected
from app.shared.runtime.logger import logger

TITLE_TAGS = {"DWG_NO": ("DWG_NO", "DRAWING_NO", "图号", "图样代号", "代号"), "NAME": ("NAME", "TITLE", "名称", "图名", "图样名称"),
              "REV": ("REV", "REVISION", "版本", "版次"), "SCALE": ("SCALE", "比例"), "DESIGNER": ("DESIGNER", "设计", "DRAWN"),
              "DATE": ("DATE", "日期"), "MATERIAL": ("MATERIAL", "材料"), "WEIGHT": ("WEIGHT", "重量")}
_TAG = {alias.upper(): key for key, aliases in TITLE_TAGS.items() for alias in aliases}
BOM_HEAD = {"seq": ("序号",), "code": ("代号", "代码", "图号", "物料编码", "编码"), "name": ("名称",), "qty": ("数量",),
            "material": ("材料",), "remark": ("备注",)}
_HEAD = {w: k for k, ws in BOM_HEAD.items() for w in ws}
DIM_LAYER = re.compile(r"DIM|尺寸|标注", re.I)

# ---------------------------------------------------------------- 尺寸文字

_NUM = r"\d+(?:\.\d+)?"
_FIT = re.compile(r"^([A-Za-z]{1,2}\d{1,2})(?:/([A-Za-z]{1,2}\d{1,2}))?")


def mtext_plain(raw: str) -> tuple[str, tuple[float, float] | None]:
    """MTEXT 原始串 → （纯文字, 堆叠公差）。\\S+0.009^ -0.004; 是上下偏差堆叠；其余格式码（\\A0; \\H0.6x; {}）去掉"""
    stack = None
    m = re.search(r"\\S\s*([+\-]?\s*\d*\.?\d+)\s*[\^/#]\s*([+\-]?\s*\d*\.?\d+)\s*;", raw)
    if m:
        stack = (float(m.group(1).replace(" ", "")), float(m.group(2).replace(" ", "")))
        raw = raw[:m.start()] + raw[m.end():]
    t = raw.replace("\\P", "\n")                                # 段落换行先保留下来
    t = re.sub(r"\\[ACcFfHhQTWp][^;]*;", "", t)
    t = re.sub(r"\\[LlOoKkNX]", "", t).replace("{", "").replace("}", "")
    return t.strip(), stack


def _symbols(t: str) -> str:
    return (t.replace("%%c", "Φ").replace("%%C", "Φ").replace("Ø", "Φ").replace("ø", "Φ").replace("⌀", "Φ").replace("∅", "Φ").replace("φ", "Φ")
            .replace("%%p", "±").replace("%%P", "±").replace("%%d", "°").replace("%%D", "°").replace("×", "x").replace("X", "x"))


def parse_dim_text(text: str, stack: tuple | None = None, measurement: float | None = None) -> dict | None:
    """一串标注文字 → {kind, value, plus, minus, fit, count, pitch, text}。认不出返回 None"""
    t = _symbols(text).replace("<>", f"{measurement:g}" if measurement is not None else "").strip()
    t = re.sub(r"\s+", "", t)
    out = {"text": text, "plus": None, "minus": None, "fit": None, "count": None, "pitch": None}
    m = re.match(rf"^(\d+)[x\-](?=[ΦRM]|{_NUM})", t)
    if m:
        out["count"], t = int(m.group(1)), t[m.end():]
    if t.startswith("M") and re.match(rf"^M{_NUM}", t):
        m = re.match(rf"^M({_NUM})(?:x({_NUM}))?(?:-?(\d[gGhH]|\d[gGhH]\d[gGhH]))?", t)
        out.update(kind="thread", value=float(m.group(1)), pitch=float(m.group(2)) if m.group(2) else None, fit=m.group(3))
        return out
    kind = "linear"
    if t.startswith("Φ"):
        kind, t = "dia", t[1:]
    elif t.startswith("R") and re.match(rf"^R{_NUM}", t):
        kind, t = "radius", t[1:]
    elif t.startswith("SΦ"):
        kind, t = "dia", t[2:]
    m = re.match(rf"^({_NUM})", t)
    if not m:
        if measurement is None:
            return None
        value, rest = float(measurement), t
    else:
        value, rest = float(m.group(1)), t[m.end():]
    if rest.startswith("°"):
        kind, rest = "angle", rest[1:]
    m = re.match(rf"^±({_NUM})", rest)
    if m:
        out["plus"], out["minus"], rest = float(m.group(1)), -float(m.group(1)), rest[m.end():]
    m = _FIT.match(rest)
    if m:
        out["fit"], rest = m.group(0), rest[m.end():]
    m = re.match(rf"^\(?([+\-]{_NUM})[/^]?([+\-]?{_NUM})?\)?", rest)        # 平铺写法：19+0.009-0.004 或 (+0.009/-0.004)
    if m and out["plus"] is None:
        a, b = float(m.group(1)), float(m.group(2)) if m.group(2) else 0.0
        out["plus"], out["minus"] = max(a, b), min(a, b)
    if stack and out["plus"] is None:
        out["plus"], out["minus"] = max(stack), min(stack)
    out.update(kind=kind, value=round(value, 4))
    return out


def _geometry_text(d) -> tuple[str | None, tuple | None]:
    """标注几何块里显示的文字"""
    try:
        blk = d.get_geometry_block()
    except Exception:
        blk = None
    for e in blk or []:
        if e.dxftype() == "MTEXT":
            return mtext_plain(e.text)
        if e.dxftype() == "TEXT":
            return e.dxf.text, None
    return None, None


def _style_tol(d) -> tuple[float | None, float | None]:
    from ezdxf.entities import DimStyleOverride
    try:
        ov = DimStyleOverride(d)
        if not ov.get("dimtol", 0):
            return None, None
        return round(float(ov.get("dimtp", 0)), 4), round(-float(ov.get("dimtm", 0)), 4)
    except Exception:
        return None, None


def _measure(d) -> float | None:
    try:
        m = d.get_measurement()
        if isinstance(m, (int, float)):
            return round(float(m), 4)
        return round(float(abs(m)), 4)
    except Exception:
        m = d.dxf.get("actual_measurement")
        return round(float(m), 4) if m else None


def read_dimension(d, space: str) -> dict | None:
    meas = _measure(d)
    shown, stack = _geometry_text(d)
    source = "geometry_block"
    if not shown:
        override = d.dxf.get("text", "")
        shown, source = (override if override not in ("", "<>") else None), "override"
        if shown:
            shown, stack = mtext_plain(shown)
    parsed = parse_dim_text(shown, stack, meas) if shown else None
    if parsed is None:
        if meas is None:
            return None
        kind = {3: "dia", 4: "radius", 2: "angle", 5: "angle"}.get(d.dimtype, "linear")
        parsed = {"text": f"{meas:g}", "kind": kind, "value": meas, "plus": None, "minus": None, "fit": None, "count": None, "pitch": None}
        source = "measurement"
    if parsed["plus"] is None:
        plus, minus = _style_tol(d)                           # 文字里没有公差：看标注样式
        if plus is not None:
            parsed.update(plus=plus, minus=minus)
            source += "+dimstyle"
    if d.dimtype == 3 and parsed["kind"] == "linear":
        parsed["kind"] = "dia"
    if d.dimtype == 4 and parsed["kind"] == "linear":
        parsed["kind"] = "radius"
    try:
        angle = float(d.dxf.get("angle", 0))
    except Exception:
        angle = 0.0
    return {**parsed, "source": source, "layer": d.dxf.layer, "space": space, "dimtype": d.dimtype, "measurement": meas, "angle": angle}


_EXPLODED = re.compile(r"^(\d+[x×X\-])?\s*(%%[cC]|[ΦØ⌀φøR]|M(?=\d)|S[ΦØ])?\s*\d+(\.\d+)?")


def exploded_dims(texts: list[dict]) -> list[dict]:
    """被炸开的标注：标注图层上、看起来像尺寸的文字"""
    out = []
    for t in texts:
        if not DIM_LAYER.search(t["layer"]) or not _EXPLODED.match(t["text"].strip()):
            continue
        p = parse_dim_text(t["text"], t.get("stack"))
        if p:
            out.append({**p, "source": "exploded", "layer": t["layer"], "space": t["space"], "dimtype": None, "measurement": None, "angle": 0.0,
                        "x": t["x"], "y": t["y"]})
    return out


# ---------------------------------------------------------------- 遍历

def _walk(layout, space: str, depth: int = 0, out: dict | None = None) -> dict:
    """收集文字、标注、线、圆、块属性。块参照递归展开（最多 5 层）"""
    out = out if out is not None else {"texts": [], "dims": [], "lines": [], "circles": [], "inserts": [], "expanded": 0}
    for e in layout:
        kind = e.dxftype()
        if kind == "INSERT":
            if e.attribs:                                      # 带属性的块（标题栏）：读属性，不展开
                out["inserts"].append({"name": e.dxf.name, "attribs": {a.dxf.tag: a.dxf.text for a in e.attribs}, "space": space})
                continue
            if depth < 5:
                try:
                    out["expanded"] += 1
                    _walk(list(e.virtual_entities()), space, depth + 1, out)
                except Exception as ex:
                    logger.warning(f"[cad] 块 {e.dxf.name} 展开失败：{ex}")
        elif kind == "DIMENSION":
            out["dims"].append((e, space))
        elif kind == "TEXT":
            out["texts"].append({"text": e.dxf.text, "x": e.dxf.insert.x, "y": e.dxf.insert.y, "h": e.dxf.get("height", 2.5), "layer": e.dxf.layer,
                                 "space": space, "stack": None})
        elif kind == "MTEXT":
            text, stack = mtext_plain(e.text)
            for i, line in enumerate(text.split("\n")):
                if line.strip():
                    out["texts"].append({"text": line.strip(), "x": e.dxf.insert.x, "y": e.dxf.insert.y - i * e.dxf.get("char_height", 2.5) * 1.6,
                                         "h": e.dxf.get("char_height", 2.5), "layer": e.dxf.layer, "space": space, "stack": stack if i == 0 else None,
                                         "mtext": text})
        elif kind == "LINE":
            out["lines"].append(((e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y), e.dxf.layer, space))
        elif kind == "LWPOLYLINE":
            pts = [tuple(p[:2]) for p in e.get_points("xy")]
            if e.closed and pts:
                pts.append(pts[0])
            out["lines"] += [(a, b, e.dxf.layer, space) for a, b in zip(pts, pts[1:])]
        elif kind == "CIRCLE":
            out["circles"].append({"layer": e.dxf.layer, "dia": round(e.dxf.radius * 2, 3), "x": e.dxf.center.x, "y": e.dxf.center.y})
    return out


# ---------------------------------------------------------------- 标题栏

def title_block(inserts: list[dict], texts: list[dict]) -> dict:
    title = {}
    for ins in inserts:
        for tag, val in ins["attribs"].items():
            key = _TAG.get(tag.strip().upper())
            if key and val and key not in title:
                title[key] = val.strip()
    if not title.get("DWG_NO"):                                # 没有块属性：按「图号」标签右边最近的文字兜底
        for label, key in (("图号", "DWG_NO"), ("名称", "NAME"), ("比例", "SCALE")):
            lab = next((t for t in texts if t["text"].strip() == label), None)
            if lab:
                right = [t for t in texts if t is not lab and abs(t["y"] - lab["y"]) < lab["h"] and t["x"] > lab["x"]]
                if right:
                    title.setdefault(key, min(right, key=lambda t: t["x"])["text"].strip())
    return title


# ---------------------------------------------------------------- 明细表

def _cluster(values: list[float], tol: float) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def bom_table(texts: list[dict], lines: list[tuple]) -> dict:
    """返回 {rows: [[序号, 代号, 名称, 数量]], extra: [{材料, 备注}], method, bottom_up}"""
    heads = [t for t in texts if t["text"].strip() in _HEAD]
    if len({_HEAD[t["text"].strip()] for t in heads}) < 3:
        return {"rows": [], "extra": [], "method": None, "bottom_up": False}
    hy = sorted(t["y"] for t in heads)[len(heads) // 2]
    head = {}
    for t in sorted(heads, key=lambda t: abs(t["y"] - hy)):
        head.setdefault(_HEAD[t["text"].strip()], t)
    space = head["seq"]["space"] if "seq" in head else heads[0]["space"]
    h = max(t["h"] for t in head.values())
    xs = sorted(t["x"] for t in head.values())
    x0, x1 = xs[0] - 15, xs[-1] + 60
    cand = [t for t in texts if t["space"] == space and x0 <= t["x"] <= x1 and t not in head.values() and abs(t["y"] - hy) > h * 0.5]
    above = [t for t in cand if t["y"] > hy and t["y"] - hy < 200]
    below = [t for t in cand if t["y"] < hy and hy - t["y"] < 200]
    bottom_up = len(above) > len(below)                        # GB 明细栏：表头在最下面，内容往上排
    body = above if bottom_up else below
    # 网格：表头附近的横线和竖线
    hs = [(a, b) for a, b, _, sp in lines if sp == space and abs(a[1] - b[1]) < 0.01 and min(a[0], b[0]) <= x0 + 20 and max(a[0], b[0]) >= xs[-1]]
    vs = [(a, b) for a, b, _, sp in lines if sp == space and abs(a[0] - b[0]) < 0.01 and x0 <= a[0] <= x1]
    ys = _cluster([a[1] for a, _ in hs], 0.5)
    vx = _cluster([a[0] for a, _ in vs], 0.5)
    cols = {k: t["x"] for k, t in head.items()}
    method = "grid"
    if len(ys) >= 3 and len(vx) >= 3:
        def cell(x: float) -> int:
            return bisect_right(vx, x + 0.5) - 1

        head_cell = {cell(t["x"]): k for k, t in head.items()}            # 每个表头所在的格子 = 这一列
        rows: dict[float, dict] = {}
        for t in body:
            row = max((v for v in ys if v <= t["y"] + 0.5), default=None)  # 文字所在行 = 它下面最近的那条横线
            col = head_cell.get(cell(t["x"]))
            if row is None or col is None:
                continue
            r = rows.setdefault(row, {})
            r[col] = (r.get(col, "") + " " + t["text"].strip()).strip()
    else:                                                      # 表格线不全：按列位置 + 纵坐标聚类
        method = "cluster"
        rows = {}
        for y in _cluster([t["y"] for t in body], h * 0.6):
            for t in body:
                if abs(t["y"] - y) <= h * 0.6:
                    c = min(cols, key=lambda k: abs(cols[k] - t["x"]) if t["x"] >= cols[k] - h * 2 else 1e9)
                    cell = rows.setdefault(y, {})
                    cell[c] = (cell.get(c, "") + " " + t["text"].strip()).strip()
    order = sorted(rows, reverse=not bottom_up)                 # 填写顺序：自上而下，或 GB 明细栏的自下而上
    items: list[dict] = []
    for y in order:
        r = rows[y]
        if not any(r.values()):
            continue
        if not r.get("seq") and not r.get("code") and r.get("name") and items:      # 续行：名称接到上一项
            items[-1]["name"] += r["name"]
            continue
        items.append({k: r.get(k, "") for k in ("seq", "code", "name", "qty", "material", "remark")})
    items.sort(key=lambda it: (0, int(it["seq"])) if str(it["seq"]).isdigit() else (1, 0))
    return {"rows": [[it["seq"], it["code"], it["name"], it["qty"]] for it in items],
            "extra": [{"material": it["material"], "remark": it["remark"]} for it in items], "method": method, "bottom_up": bottom_up}


# ---------------------------------------------------------------- 关键特征（电机安装尺寸）

def features(dims: list[dict], circles: list[dict]) -> dict:
    """把尺寸归到电机安装尺寸的几个特征上。归类规则：直径标注带数量的是安装孔，落在中心线圆上的是分度圆，其余是止口；
    线性标注按图层和方向归类（前视图横向是法兰边长，侧视图横向是轴伸、竖向是轴径）；螺纹单独记"""
    out: dict[str, dict] = {}
    for d in dims:
        v = {"value": d["value"], "plus": d.get("plus"), "minus": d.get("minus")}
        if d.get("fit"):
            v["fit"] = d["fit"]
        layer = (d.get("layer") or "").upper()
        if d["kind"] == "thread":
            out.setdefault("thread", {"value": d["value"], "pitch": d.get("pitch")})
        elif d["kind"] == "dia":
            if d.get("count"):
                out.setdefault("hole_dia", {"value": d["value"]})
                out.setdefault("hole_count", {"value": d["count"]})
            elif any(c["layer"].upper() == "CENTER" and abs(c["dia"] - d["value"]) < 0.01 for c in circles):
                out.setdefault("pcd", {"value": d["value"]})
            elif "SIDE" in layer:
                out.setdefault("shaft_dia", v)
            elif "FRONT" in layer:
                out.setdefault("spigot_dia", v)                # 其他图层上的直径不硬归类，只留在卡片的尺寸列表里
        elif d["kind"] == "linear":
            horizontal = abs(d.get("angle") or 0) < 1
            if "FRONT" in layer and horizontal:
                out.setdefault("flange_size", v)
            elif "SIDE" in layer and horizontal:
                out.setdefault("shaft_len", v)
            elif "SIDE" in layer:
                out.setdefault("shaft_dia", v)
    return out


# ---------------------------------------------------------------- 入口

def read(path: Path) -> dict:
    import ezdxf
    try:
        doc = ezdxf.readfile(str(path))
    except Exception as e:
        raise Rejected(f"DXF 无法读取：{str(e)[:100]}")
    found = _walk(doc.modelspace(), "model")
    paper = [l for l in doc.layouts if l.name != "Model"]
    for lay in paper:
        _walk(lay, f"paper:{lay.name}", out=found)
    dims = [x for x in (read_dimension(d, sp) for d, sp in found["dims"]) if x]
    dims += exploded_dims(found["texts"])
    title = title_block(found["inserts"], found["texts"])
    notes = []
    for t in found["texts"]:
        if t.get("mtext", "").startswith("技术要求"):
            notes = [x.strip() for x in t["mtext"].split("\n")[1:] if x.strip()]
            break
    bom = bom_table([t for t in found["texts"] if not DIM_LAYER.search(t["layer"])], found["lines"])
    feats = features(dims, found["circles"])
    missing = [label for key, label in (("DWG_NO", "图号"), ("REV", "版本"), ("NAME", "名称")) if not title.get(key)]
    if len(dims) < 3:
        missing.append(f"尺寸（只读到 {len(dims)} 个）")
    card = {"title": title, "dims": [{k: d.get(k) for k in ("kind", "value", "plus", "minus", "fit", "count", "pitch", "text", "source", "layer", "space")}
                                     for d in dims],
            "features": feats, "bom": bom["rows"], "bom_extra": bom["extra"], "bom_method": bom["method"], "bom_bottom_up": bom["bottom_up"],
            "notes": notes, "spaces": sorted({d["space"] for d in dims} | {t["space"] for t in found["texts"]}), "blocks_expanded": found["expanded"],
            "missing": missing, "needs_review": bool(missing)}
    return {"title": title, "dims": feats, "notes": notes, "bom": bom["rows"], "card": card}


def render_png(path: Path, out: Path) -> Path | None:
    """把图纸渲染成 PNG（模型空间），给视觉模型复核用。渲染失败返回 None"""
    try:
        import ezdxf
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        doc = ezdxf.readfile(str(path))
        fig = plt.figure(figsize=(16, 11), dpi=120)
        ax = fig.add_axes([0, 0, 1, 1])
        Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(doc.modelspace(), finalize=True)
        fig.savefig(out, dpi=120, facecolor="white")
        plt.close(fig)
        return out
    except Exception as e:
        logger.warning(f"[cad] 渲染 {path.name} 失败：{e}")
        return None


REVIEW_PROMPT = ("这是一张电机零部件的工程图纸。自动解析发现它缺少：{missing}。请只根据图面回答：标题栏里能看到的图号、名称、版本；"
                 "图上标注的主要尺寸（带公差的照写）；明细表的行数。看不清的写「看不清」，不要猜。用中文，200 字以内。")


def vl_review(png: Path, missing: list[str]) -> str:
    """视觉模型复核缺项的图纸。没有配置视觉模型时返回空串（等人工复核）"""
    from app.kbase.ingest import images
    if not images.vl_available():
        return ""
    import base64
    from langchain_core.messages import HumanMessage
    from app.infra.llm.providers import llm_providers
    uri = "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    msg = HumanMessage(content=[{"type": "image_url", "image_url": {"url": uri}}, {"type": "text", "text": REVIEW_PROMPT.format(missing="、".join(missing))}])
    try:
        return str(llm_providers.vision_chat().invoke([msg]).content).strip()[:800]
    except Exception as e:
        logger.warning(f"[cad] 视觉复核失败：{e}")
        return ""

