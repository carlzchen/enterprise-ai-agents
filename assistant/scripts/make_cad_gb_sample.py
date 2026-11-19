"""
生成一张 GB 风格的测试图纸（虚构零件），覆盖图纸解析要处理的几种情况：
  模型空间：块里嵌着的线性标注（公差写在标注样式里）、几何块里带 \\S 堆叠公差的直径标注、
            已经炸开的标注（标注图层上的纯文字：4-Φ6.6、M5x0.8-6H、R3、Φ22H7、50±0.1、Φ19h6）
  图纸空间：标题栏块（中文属性名：图号、名称、版本、比例、设计、日期、材料）、
            GB 明细栏（表头在下、序号自下而上，第 2 项名称太长折到第二行）

用法：PYTHONPATH=. .venv/bin/python scripts/make_cad_gb_sample.py  → data/kbase_samples/drawings_gb/KL-SV-80-750W-SHAFT.dxf
"""
from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parents[1] / "data" / "kbase_samples" / "drawings_gb"


def title_block(doc) -> None:
    blk = doc.blocks.new("GB_TITLE")
    for x0, y0, x1, y1 in ((0, 0, 180, 0), (0, 56, 180, 56), (0, 0, 0, 56), (180, 0, 180, 56), (0, 28, 180, 28), (90, 0, 90, 56)):
        blk.add_line((x0, y0), (x1, y1), dxfattribs={"layer": "FRAME"})
    for i, (tag, x, y) in enumerate((("图号", 100, 40), ("名称", 100, 10), ("版本", 10, 40), ("比例", 10, 30), ("设计", 10, 10), ("日期", 50, 10),
                                     ("材料", 50, 40))):
        blk.add_attdef(tag, (x, y), dxfattribs={"height": 3.5, "layer": "TEXT"})


def bom(psp) -> None:
    """GB 明细栏：在标题栏上方，表头在最下面一行，序号 1 在表头正上方，往上排"""
    x0, y0, w, rh = 410, 70, 180, 7
    cols = [x0, x0 + 15, x0 + 55, x0 + 115, x0 + 130, x0 + 160, x0 + w]
    rows = 5                                                    # 表头 + 4 行（第 2 项占两行）
    for r in range(rows + 1):
        psp.add_line((x0, y0 + r * rh), (x0 + w, y0 + r * rh), dxfattribs={"layer": "FRAME"})
    for c in cols:
        psp.add_line((c, y0), (c, y0 + rows * rh), dxfattribs={"layer": "FRAME"})
    head = ["序号", "代号", "名称", "数量", "材料", "备注"]
    for c, text in zip(cols, head):
        psp.add_text(text, dxfattribs={"height": 3, "layer": "TEXT", "insert": (c + 2, y0 + 2)})
    body = [["1", "M-SH-14", "转轴 Φ14", "1", "40Cr", ""],
            ["2", "M-BR-6202", "深沟球轴承 6202-2RS", "2", "", "外购"],
            ["", "", "（含防尘盖，C3 游隙）", "", "", ""],
            ["3", "M-KEY-5", "平键 5×5×20", "1", "45", "GB/T 1096"]]
    for i, row in enumerate(body, start=1):
        for c, text in zip(cols, row):
            if text:
                psp.add_text(text, dxfattribs={"height": 3, "layer": "TEXT", "insert": (c + 2, y0 + i * rh + 2)})


def main() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    doc = ezdxf.new("R2018", setup=True)
    for name in ("FRAME", "OUTLINE", "CENTER", "DIM", "TEXT"):
        doc.layers.add(name)
    title_block(doc)
    msp = doc.modelspace()

    view = doc.blocks.new("SHAFT_VIEW")                         # 主视图做成块：解析时要展开才能读到里面的标注
    view.add_lwpolyline([(0, -7), (120, -7), (120, 7), (0, 7)], close=True, dxfattribs={"layer": "OUTLINE"})
    view.add_line((-5, 0), (125, 0), dxfattribs={"layer": "CENTER"})
    dim = view.add_linear_dim(base=(0, 20), p1=(0, 7), p2=(120, 7), dimstyle="EZDXF", dxfattribs={"layer": "DIM"},
                              override={"dimtol": 1, "dimtp": 0.1, "dimtm": 0.2})
    dim.render()
    msp.add_blockref("SHAFT_VIEW", (50, 150))

    d2 = msp.add_diameter_dim(center=(300, 150), radius=7, angle=45, dimstyle="EZ_RADIUS", dxfattribs={"layer": "DIM"},
                              text="%%c14{\\H0.6x;\\S+0.012^+0.001;}")
    d2.render()
    for text, (x, y) in (("4-Φ6.6", (60, 100)), ("M5x0.8-6H", (120, 100)), ("R3", (180, 100)), ("Φ22H7", (60, 90)), ("50±0.1", (120, 90)),
                         ("Φ19h6", (180, 90))):
        msp.add_text(text, dxfattribs={"height": 3.5, "layer": "DIM", "insert": (x, y)})
    msp.add_mtext("技术要求\\P1. 调质处理 HB220~250\\P2. 未注倒角 C0.5", dxfattribs={"insert": (40, 60), "char_height": 3.5, "layer": "TEXT"})

    psp = doc.layouts.get("Layout1")
    ref = psp.add_blockref("GB_TITLE", (410, 10))
    ref.add_auto_attribs({"图号": "KL-SV-80-750W-SHAFT", "名称": "伺服电机转轴 80法兰 750W", "版本": "A", "比例": "1:1", "设计": "eng02",
                          "日期": "2026-10-15", "材料": "40Cr"})
    bom(psp)
    out = OUT / "KL-SV-80-750W-SHAFT.dxf"
    doc.saveas(out)
    return out


if __name__ == "__main__":
    print(main())
