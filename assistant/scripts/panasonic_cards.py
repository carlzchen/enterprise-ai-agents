"""
松下 MINAS A6 伺服电机官方外形图（DXF）→ 几何测量 → 图纸卡片（Markdown + 渲染图）→ 导入知识库。

    PYTHONPATH=. .venv/bin/python scripts/panasonic_cards.py            # 只生成卡片
    PYTHONPATH=. .venv/bin/python scripts/panasonic_cards.py --ingest   # 生成并交给知识库批量入库

图纸来自 ~/Desktop/Python/Projectbydate/MotorVendorData（用 Scrapling 从松下官网抓的，见那里的 README）。
这批图只有外形轮廓，几乎没有尺寸标注，所以尺寸不是读出来的，而是从几何量出来的：
  1. 去掉中心线（红色 / 点划线），按包围盒相交把实体连成块，每一块是一个视图
  2. 侧视图（横向细长、不带接插件的那个）：最右侧水平线的上下间距 = 轴径 S；最右边那条贯穿整个高度的竖线是法兰安装面，
     安装面到轴端 = 轴伸 LR，到后端 = 机身长 LL；视图高度 = 法兰边长 LC
  3. 前视图（含直径等于 S 的同心圆的那一块）：同心圆里小于法兰的最大圆 = 止口 LB；4 个等大的圆 = 安装孔，孔心到中心距离 × 2 = 节圆 LE
量出来的值标成「几何测量」，和样本上的标注值可能有零点几毫米的出入；正式数据以松下样本为准。
"""
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from ezdxf import bbox  # noqa: E402
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402

SRC = Path("~/Desktop/Python/Projectbydate/MotorVendorData/panasonic").expanduser()
OUT = Path(__file__).resolve().parents[1] / "data" / "kbase_samples" / "panasonic_a6"
SOURCE_URL = "https://industry.panasonic.com/global/en/products/motor/fa-motor/ac-servo/2ddata"

FAMILY = {"MSMF": "低惯量（小型）", "MQMF": "中惯量扁平型", "MHMF": "高惯量", "MDMF": "中惯量", "MGMF": "中惯量（低速大扭矩）"}
POWER = {"01": "100 W", "02": "200 W", "04": "400 W", "08": "750 W", "09": "850 W / 1.0 kW", "10": "1.0 kW", "13": "1.3 kW", "15": "1.5 kW",
         "18": "1.8 kW", "20": "2.0 kW", "29": "2.9 kW", "30": "3.0 kW", "40": "4.0 kW", "44": "4.4 kW", "50": "5.0 kW"}
# 演示用的 13 张：覆盖 40~130 法兰、低 / 中 / 高惯量和扁平型，外加一张带抱闸的做对比
PICK = [("MSMF01[]L1[]1", "without"), ("MSMF02[]L1[]1", "without"), ("MSMF04[]L1[]1", "without"), ("MSMF04[]L1[]1", "with"),
        ("MSMF082L1[]1", "without"), ("MSMF102L1[]5", "without"), ("MSMF202L1[]5", "without"), ("MHMF04[]L1[]1", "without"),
        ("MHMF102L1[]5", "without"), ("MHMF02[]L1[]1", "without"), ("MDMF152L1[]5", "without"), ("MDMF302L1[]5", "without"),
        ("MQMF02[]L1[]1", "without")]


def _skip(e) -> bool:
    """中心线、点划线不参与分块（它们会把相邻视图连在一起）"""
    lt = (e.dxf.get("linetype") or "").upper()
    return e.dxf.get("color") == 1 or any(k in lt for k in ("CENTER", "DASH", "HIDDEN", "PHANTOM", "CHAIN"))


def _boxes(msp) -> list[tuple]:
    out = []
    for e in msp:
        if e.dxftype() not in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE") or _skip(e):
            continue
        b = bbox.extents([e])
        if b.has_data:
            out.append((e, b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y))
    return out


def views(msp, tol: float = 0.6) -> list[dict]:
    """包围盒相交（容差 tol）的实体并成一块，每块是一个视图"""
    items = _boxes(msp)
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    order = sorted(range(len(items)), key=lambda i: items[i][1])
    for a_pos, a in enumerate(order):                       # 按 x 排序后只和 x 范围有重叠的比较
        ax0, ay0, ax1, ay1 = items[a][1:]
        for b in order[a_pos + 1:]:
            bx0, by0, bx1, by1 = items[b][1:]
            if bx0 > ax1 + tol:
                break
            if by0 <= ay1 + tol and ay0 <= by1 + tol:
                parent[find(a)] = find(b)
    groups = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(items[i])
    out = []
    for g in groups.values():
        xs0, ys0, xs1, ys1 = (min(x[1] for x in g), min(x[2] for x in g), max(x[3] for x in g), max(x[4] for x in g))
        if max(xs1 - xs0, ys1 - ys0) > 15:
            out.append({"ents": [x[0] for x in g], "x0": xs0, "y0": ys0, "x1": xs1, "y1": ys1, "w": xs1 - xs0, "h": ys1 - ys0})
    return out


def _circles(ents, min_span: float = 180) -> list[tuple]:
    """整圆和跨度超过 min_span 度的圆弧 → (cx, cy, d, 是否红色)。轴端有键槽时轴是大圆弧；大法兰的止口有时用几段圆弧拼"""
    out = []
    for e in ents:
        red = e.dxf.get("color") == 1
        if e.dxftype() == "CIRCLE":
            out.append((e.dxf.center.x, e.dxf.center.y, 2 * e.dxf.radius, red))
        elif e.dxftype() == "ARC":
            span = (e.dxf.end_angle - e.dxf.start_angle) % 360
            if span > min_span:
                out.append((e.dxf.center.x, e.dxf.center.y, 2 * e.dxf.radius, red))
    return out


def _lines(ents):
    for e in ents:
        if e.dxftype() == "LINE":
            yield e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y


def side_view(vs: list[dict], front: dict | None) -> dict | None:
    """侧视图：横向比竖向长、线条多；优先取和前视图底边对齐、在前视图左边最近的那个（三视图投影对齐），找不到再取高度最小的"""
    cands = [v for v in vs if v["w"] > 1.05 * v["h"] and v["w"] > 40 and sum(1 for _ in _lines(v["ents"])) > 20]
    if front:
        fv = front["view"]
        aligned = [v for v in cands if abs(v["y0"] - fv["y0"]) < 1.5 and v["x1"] < fv["x0"]]
        if aligned:
            return max(aligned, key=lambda v: v["x1"])
    return min(cands, key=lambda v: v["h"]) if cands else None


def measure_side(v: dict, lc: float | None = None) -> dict:
    lines = list(_lines(v["ents"]))
    horiz = [(min(x0, x1), max(x0, x1), y0) for x0, y0, x1, y1 in lines if abs(y0 - y1) < 1e-6 and abs(x1 - x0) > 0.5]
    vert = [(x0, min(y0, y1), max(y0, y1)) for x0, y0, x1, y1 in lines if abs(x0 - x1) < 1e-6]
    xmax, height = v["x1"], min(v["h"], lc or v["h"])
    shaft = [y for a, b, y in horiz if b > xmax - 2.5]          # 伸到轴端附近的水平线：轴的上下母线（含倒角）
    s = max(shaft) - min(shaft) if len(shaft) >= 2 else None
    cover = defaultdict(list)                                  # 同一 x 上的竖线段合并（安装面常被孔、倒角打断成几段）
    for x, y0, y1 in vert:
        cover[round(x, 2)].append((y0, y1))

    def covered(segs):
        total, end = 0.0, -1e9
        for a, b in sorted(segs):
            if b > end:
                total += b - max(a, end)
                end = b
        return total
    long_vert = [x for x, segs in cover.items() if covered(segs) > 0.85 * height]
    face = max(long_vert) if long_vert else None
    return {"S": s, "LR": xmax - face if face else None, "LL": face - v["x0"] if face else None, "LC": height, "axis_y": (v["y0"] + v["y1"]) / 2}


def front_view(vs: list[dict], s: float | None, all_circles: list[tuple]) -> dict | None:
    """前视图：近似方形、圆多的视图里，同心圆最大直径（止口）最大的那个——后视图的后盖圆比止口小；有直径等于轴径的圆再加分。
    红色的圆（安装孔）分块时被当成中心线排除了，这里按圆心落在视图范围内补回来"""
    best = None
    for v in vs:
        cs = [c for c in all_circles if v["x0"] - 0.5 <= c[0] <= v["x1"] + 0.5 and v["y0"] - 0.5 <= c[1] <= v["y1"] + 0.5]
        if len(cs) < 5 or v["w"] > 1.6 * v["h"]:
            continue
        cnt = Counter((round(x, 1), round(y, 1)) for x, y, *_ in cs).most_common(1)[0][0]
        conc = [d for x, y, d, _ in cs if abs(x - cnt[0]) < 0.1 and abs(y - cnt[1]) < 0.1 and d < 0.97 * v["w"]]
        score = (max(conc) if conc else 0) + (5 if s and any(abs(d - s) < 0.35 for d in conc) else 0)
        if best is None or score > best[0]:
            best = (score, v, cs, cnt)
    if not best:
        return None
    v = best[1]
    arcs = [c for c in _circles(v["ents"], min_span=30)]
    return {"view": v, "circles": best[2], "center": best[3], "arcs": arcs}


def measure_front(fv: dict) -> dict:
    """LC = 前视图宽度（接插件只往上凸，不影响宽度）；LB = 小于 LC 的最大同心圆；
    安装孔 = 4 个等大、圆心在四个象限且到中心等距的圆（对称检查，避免把接插件螺钉当成安装孔）"""
    cs, v = fv["circles"], fv["view"]
    cx, cy = fv["center"]
    lc = v["w"]
    concentric = sorted({round(d, 2) for x, y, d, _ in fv["arcs"] + cs if abs(x - cx) < 0.1 and abs(y - cy) < 0.1}, reverse=True)
    lb = next((d for d in concentric if d < 0.97 * lc), None)
    holes = None
    by_d = defaultdict(list)
    for x, y, d, red in cs:
        if math.hypot(x - cx, y - cy) > 3:
            by_d[(red, round(d, 2))].append((x - cx, y - cy))
    rings = []                                                 # 候选：4 个象限各一个（或对角 2 个）等大、等距的圆
    for (red, d), pts in by_d.items():
        for r in {round(math.hypot(*p), 1) for p in pts}:
            ring = [p for p in pts if abs(math.hypot(*p) - r) < 0.15]
            if not ((lb or 0) < 2 * r < 1.45 * lc and d < 0.15 * lc):
                continue
            if len(ring) == 4 and len({(p[0] > 0, p[1] > 0) for p in ring}) == 4:
                rings.append((r, red, d, 4))
            elif len(ring) == 2 and abs(ring[0][0] + ring[1][0]) < 0.2 and abs(ring[0][1] + ring[1][1]) < 0.2:
                rings.append((r, red, d, 2))                    # 小法兰对角 2 孔
    if rings:                                                  # 安装孔在最外圈（里圈的多是前盖螺钉或凸台）；同一圈红色优先
        r, _, d, n = max(rings, key=lambda t: (t[0], t[1], t[3]))
        holes = {"LZ": d, "count": n, "LE": round(2 * r, 1)}
    return {"LC": lc, "LB": lb, "concentric": concentric, **(holes or {})}


def render(doc, out: Path) -> None:
    fig = plt.figure(figsize=(10, 7.5))
    ax = fig.add_axes([0, 0, 1, 1])
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(doc.modelspace())
    fig.savefig(out, dpi=100, facecolor="black")
    plt.close(fig)


def analyze(path: Path) -> dict:
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    vs = views(msp)
    fv = front_view(vs, None, _circles(msp))
    sv = side_view(vs, fv)
    front = measure_front(fv) if fv else {}
    side = measure_side(sv, front.get("LC")) if sv else {}
    if front.get("LC"):
        side["LC"] = front["LC"]
    return {"doc": doc, "views": len(vs), "side": side, "front": front}


def r1(x):
    return None if x is None else round(x, 1)


def card(name: str, path: Path, m: dict, png: str, partner_ll: float | None = None) -> str:
    model, brake = re.match(r"(.+?)_(with|without)_brake", name).groups()
    fam = model[:4]
    code = re.match(r"[A-Z]{4}(\d{2})", model).group(1)
    s, f = m["side"], m["front"]
    shown = model.replace("[]", "□")
    brake_cn = "带抱闸" if brake == "with" else "不带抱闸"
    rows = [("法兰边长 LC", f"□{r1(s.get('LC'))}"), ("止口直径 LB", f"Φ{r1(f.get('LB'))}"), ("轴径 S", f"Φ{r1(s.get('S'))}"),
            ("轴伸长度 LR", r1(s.get("LR"))), ("机身长度 LL（法兰安装面到后端）", r1(s.get("LL"))),
            ("安装孔", f"{f.get('count')} × Φ{r1(f.get('LZ'))}" if f.get("LZ") else "未识别"), ("安装孔节圆直径 LE", f"Φ{f.get('LE')}" if f.get("LE") else "未识别")]
    table = "\n".join(f"| {k} | {v} |" for k, v in rows)
    extra = ""
    if partner_ll and s.get("LL"):
        diff = round(s["LL"] - partner_ll, 1)
        extra = (f"\n与不带抱闸的 {shown} 相比：不带抱闸机身长 {r1(partner_ll)} mm，带抱闸机身长 {r1(s['LL'])} mm，带抱闸型长 {diff} mm；"
                 "法兰、止口、轴径、安装孔相同。\n")
    return f"""# 松下 MINAS A6 {shown} 伺服电机外形图（{brake_cn}）

厂商公开资料：松下（Panasonic）MINAS A6 系列交流伺服电机 {shown}，{FAMILY.get(fam, fam)}，额定功率 {POWER.get(code, '见样本')}，{brake_cn}。
型号里的 □ 是松下型号中的可变位（编码器、轴端、油封等选项），同一外形图适用于这些变型。

## 外形尺寸（单位 mm）

{shown}（{brake_cn}）的安装尺寸如下。这些数值由图纸几何测量得到（原图只有外形轮廓、没有尺寸标注），可能和松下样本的标注值有零点几毫米的出入，正式选型以松下样本为准。

| 项目 | {shown} |
|---|---|
{table}
{extra}
![{shown} 外形图（{brake_cn}）]({png})

## 图纸说明

{shown} 外形图共 {m['views']} 个视图（后视图、侧视图、前视图（轴端）、键槽局部等），图面为外形轮廓，无尺寸标注、无标题栏。
前视图同心圆直径（mm）：{', '.join(f'Φ{d:g}' for d in f.get('concentric', [])[:8])}。

## 来源

- 原始文件：MINAS-A6 / Motor / {fam} / {path.name}（同一外形还有带油封等变型的图纸）
- 下载页：{SOURCE_URL}
- 版权归松下（Panasonic Industry Co., Ltd.），仅用于内部学习和评测。
"""


def groups(rows: list[dict]) -> str:
    """按法兰分组 + 同功率对比 + 抱闸对比：把常问的比较直接写成文字（差值由程序算好，问答时不用模型自己算）"""
    out = ["\n## 按法兰边长分组\n"]
    by_lc = defaultdict(list)
    for r in rows:
        by_lc[round(r["LC"] or 0)].append(r)
    for lc in sorted(by_lc):
        rs = by_lc[lc]
        les = sorted({r["LE"] for r in rs if r["LE"]})
        out.append(f"- □{rs[0]['LC']} 法兰：{'、'.join(dict.fromkeys(r['model'] for r in rs))}；止口 Φ{rs[0]['LB']}；安装孔节圆 {'、'.join(f'Φ{x}' for x in les) or '未识别'}")
    out.append("\n## 同功率型号对比\n")
    by_p = defaultdict(list)
    for r in rows:
        if r["brake"] == "不带":
            by_p[r["power"]].append(r)
    for pw, rs in by_p.items():
        if len(rs) < 2:
            continue
        rs = sorted(rs, key=lambda r: r["LL"] or 0)
        desc = "；".join(f"{r['model']}（{r['family']}）法兰 □{r['LC']}、机身长 {r['LL']} mm、轴径 Φ{r['S']}" for r in rs)
        out.append(f"- {pw}：{desc}。其中 {rs[0]['model']} 机身最短，比 {rs[-1]['model']} 短 {round(rs[-1]['LL'] - rs[0]['LL'], 1)} mm。")
    out.append("\n## 带抱闸与不带抱闸对比\n")
    pairs = defaultdict(dict)
    for r in rows:
        pairs[r["model"]][r["brake"]] = r
    for m, d in pairs.items():
        if "带" in d and "不带" in d:
            out.append(f"- {m}：不带抱闸机身长 {d['不带']['LL']} mm，带抱闸机身长 {d['带']['LL']} mm，带抱闸型长 {round(d['带']['LL'] - d['不带']['LL'], 1)} mm；"
                       f"法兰、止口、轴径、安装孔相同。")
    return "\n".join(out) + "\n"


def summary(rows: list[dict]) -> str:
    head = "| 型号 | 电机族 | 功率 | 抱闸 | 法兰 LC | 止口 LB | 轴径 S | 轴伸 LR | 机身长 LL | 安装孔 | 节圆 LE |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
    body = "\n".join(f"| {r['model']} | {r['family']} | {r['power']} | {r['brake']} | □{r['LC']} | Φ{r['LB']} | Φ{r['S']} | {r['LR']} | {r['LL']} | "
                     f"{r['holes']} | {('Φ' + str(r['LE'])) if r['LE'] else '未识别'} |" for r in rows)
    return ("# 松下 MINAS A6 伺服电机外形尺寸汇总表\n\n厂商公开资料：从松下官网 2D 外形图（DXF）几何测量得到的 13 个型号的安装尺寸（单位 mm），"
            "用于和自家 KL 系列做安装互换对比。正式数值以松下样本为准。\n\n" + head + body +
            "\n" + groups(rows) + f"\n说明：LC 法兰边长，LB 止口直径，S 轴径，LR 轴伸长度，LL 机身长度（法兰安装面到后端），LE 安装孔节圆直径。来源：{SOURCE_URL}\n")


def main(ingest: bool) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = sorted((SRC / "extracted" / "MINAS-A6").rglob("*.dxf"))
    rows, items = [], []
    plain_ll = {}
    for model_key, brake_key in PICK:
        path = next(p for p in files if p.stem.startswith(model_key + f"_{brake_key}_brake") and "with_oilseal" not in p.stem)
        name = f"{model_key}_{brake_key}_brake"
        m = analyze(path)
        safe = name.replace("[]", "x")
        png = f"{safe}.png"
        render(m["doc"], OUT / png)
        if brake_key == "without":
            plain_ll[model_key] = m["side"].get("LL")
        partner = plain_ll.get(model_key) if brake_key == "with" else None
        (OUT / f"{safe}.md").write_text(card(name, path, m, png, partner), encoding="utf-8")
        s, f = m["side"], m["front"]
        model, brake = re.match(r"(.+?)_(with|without)_brake", name).groups()
        rows.append({"model": model.replace("[]", "□"), "family": FAMILY.get(model[:4], model[:4]), "power": POWER.get(model[4:6], ""),
                     "brake": "带" if brake == "with" else "不带", "LC": r1(s.get("LC")), "LB": r1(f.get("LB")), "S": r1(s.get("S")),
                     "LR": r1(s.get("LR")), "LL": r1(s.get("LL")), "holes": f"{f.get('count')}×Φ{r1(f.get('LZ'))}" if f.get("LZ") else "未识别", "LE": f.get("LE")})
        print(f"{name:32s} 视图{m['views']:2d}  LC□{r1(s.get('LC'))}  LB Φ{r1(f.get('LB'))}  S Φ{r1(s.get('S'))}  LR {r1(s.get('LR'))}  LL {r1(s.get('LL'))}  "
              f"孔 {f.get('count')}×Φ{r1(f.get('LZ'))}  LE Φ{f.get('LE')}")
        items.append((OUT / f"{safe}.md", f"PANA-A6-{safe}", f"松下 MINAS A6 {model.replace('[]', '□')} 外形图（{'带' if brake == 'with' else '不带'}抱闸）"))
    (OUT / "00_松下MINAS-A6外形尺寸汇总表.md").write_text(summary(rows), encoding="utf-8")
    items.insert(0, (OUT / "00_松下MINAS-A6外形尺寸汇总表.md", "PANA-A6-SUMMARY", "松下 MINAS A6 伺服电机外形尺寸汇总表"))
    if ingest:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from app.kbase.ingest import batchjob
        from app.kbase.ingest.batch import meta_for
        its = [{"file_path": str(p), "doc_key": key, "version": "5", "meta": meta_for(title, "MANUAL", "UPLOAD", series="SERVO")} for p, key, title in items]
        print(batchjob.create("MANUAL", its, "松下 A6 外形图（厂商公开资料）", "厂商公开资料"))


if __name__ == "__main__":
    main("--ingest" in sys.argv)
