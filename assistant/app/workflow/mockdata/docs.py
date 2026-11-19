"""
PO 文件渲染：4 种版式，按客户固定（真实情况也是每家客户一种格式）。

  A  中文 PDF，带表格线，表头是「字段：值」小表         reportlab + 内置宋体（STSong-Light）
  B  英文 PDF，没有表格线（pdfplumber 认不出表格，只能按文本行解析），日期写成 30-Oct-2026
  C  中文 Excel，一个工作表：上面几行是订单信息，下面是明细，日期是 Excel 日期单元格
  D  英文 Excel，两个工作表：Header（字段 / 值）和 Lines（明细）

为了能复现：PDF 打开 reportlab 的 invariant 模式；xlsx 重新打包成固定时间戳的 zip。同样的输入 → 字节完全相同的文件。
"""
import io
import zipfile
from datetime import date

from reportlab import rl_config

rl_config.invariant = 1
from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.cidfonts import UnicodeCIDFont  # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
CN = "STSong-Light"
SUPPLIER_CN, SUPPLIER_EN = "科朗电机集团股份有限公司", "Kelang Motor Group Co., Ltd."
PAY_CN = {30: "月结 30 天", 45: "月结 45 天", 60: "月结 60 天", 90: "月结 90 天"}


def money(v: float) -> str:
    return f"{v:,.2f}"


def qty(v: float) -> str:
    return f"{v:,.0f}"


def en_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def _amount(ln: dict) -> float:
    return round(ln["qty"] * ln["unit_price"], 2)


def render(po: dict, layout: str) -> tuple[bytes, str]:
    """返回（文件字节, 扩展名）"""
    if layout == "A":
        return _pdf_cn(po), "pdf"
    if layout == "B":
        return _pdf_en(po), "pdf"
    if layout == "C":
        return _xlsx_cn(po), "xlsx"
    return _xlsx_en(po), "xlsx"


# ---------------------------------------------------------------- PDF

def _pdf(story) -> bytes:
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=40, bottomMargin=40, title="PO", author="", creator="", subject="").build(story)
    return buf.getvalue()


def _pdf_cn(po: dict) -> bytes:
    title = ParagraphStyle("t", fontName=CN, fontSize=16, leading=22, alignment=1)
    small = ParagraphStyle("s", fontName=CN, fontSize=9, leading=13)
    head = [["订单编号", po["po_no"], "订单日期", po["order_date"].strftime("%Y-%m-%d")],
            ["采购方", po["buyer"], "供应商", SUPPLIER_CN],
            ["币种", po["currency"], "付款条件", PAY_CN.get(po["payment_days"], f"月结 {po['payment_days']} 天")],
            ["交货地址", Paragraph(po["ship_to"], small), "联系人", po["contact"]]]
    rows = [["序号", "物料编码", "名称规格", "数量", "单位", "含税单价", "金额", "交货日期"]]
    for ln in po["lines"]:
        rows.append([str(ln["line_no"]), ln["customer_part_no"], Paragraph(ln["desc"], small), qty(ln["qty"]), "台", money(ln["unit_price"]),
                     money(_amount(ln)), ln["delivery_date"].strftime("%Y-%m-%d")])
    rows.append(["", "", "合计", qty(sum(x["qty"] for x in po["lines"])), "", "", money(po["printed_total"]), ""])
    grid = [("FONT", (0, 0), (-1, -1), CN, 9), ("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    story = [Paragraph("采购订单" + (f"（变更 第{po['version']}版）" if po["version"] > 1 else ""), title), Spacer(1, 8),
             Table(head, colWidths=[60, 170, 60, 230], style=grid + [("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke)]), Spacer(1, 10),
             Table(rows, colWidths=[28, 82, 150, 52, 26, 56, 64, 62], repeatRows=1, style=grid + [("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]),
             Spacer(1, 10), Paragraph(po.get("remark") or "请于收到订单后 2 个工作日内回传订单确认，交货时随货附送货单及出厂检验报告。", small)]
    return _pdf(story)


def _pdf_en(po: dict) -> bytes:
    title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=15, leading=20)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=9, leading=13)
    head = (f"PO Number: {po['po_no']}<br/>PO Date: {en_date(po['order_date'])}<br/>Revision: {po['version']}<br/>"
            f"Buyer: {po['buyer']}<br/>Vendor: {SUPPLIER_EN}<br/>Currency: {po['currency']}<br/>Payment Terms: Net {po['payment_days']} days<br/>"
            f"Ship To: {po['ship_to']}<br/>Contact: {po['contact']}")
    rows = [["Item", "Part No.", "Description", "Qty", "UOM", "Unit Price", "Amount", "Req. Date"]]
    for ln in po["lines"]:
        rows.append([str(ln["line_no"] * 10), ln["customer_part_no"], ln["desc"], qty(ln["qty"]), "PCS", money(ln["unit_price"]), money(_amount(ln)),
                     en_date(ln["delivery_date"])])
    rows.append(["", "", "TOTAL", "", "", "", f"{po['currency']} {money(po['printed_total'])}", ""])
    story = [Paragraph("PURCHASE ORDER", title), Spacer(1, 6), Paragraph(head, body), Spacer(1, 12),
             Table(rows, colWidths=[30, 64, 168, 44, 30, 56, 70, 62],
                   style=[("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.white)]),
             Spacer(1, 12), Paragraph(po.get("remark") or "Please acknowledge this order within 2 business days. Partial shipments require prior approval.", body)]
    return _pdf(story)


# ---------------------------------------------------------------- Excel

def _fixed_zip(data: bytes) -> bytes:
    """xlsx 本质是 zip：按固定顺序、固定时间戳重新打包，生成的文件才能逐字节复现"""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in sorted(src.infolist(), key=lambda i: i.filename):
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                import re
                body = re.sub(rb"<dcterms:(created|modified)[^>]*>[^<]*</dcterms:\1>", lambda m: m.group(0).split(b">")[0] + b">2026-01-01T00:00:00Z</dcterms:" + m.group(1) + b">", body)
            zi = zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            dst.writestr(zi, body)
    return out.getvalue()


def _save(wb: Workbook) -> bytes:
    wb.properties.creator = "buyer"
    buf = io.BytesIO()
    wb.save(buf)
    return _fixed_zip(buf.getvalue())


def _xlsx_cn(po: dict) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "采购订单"
    ws["A1"] = "采购订单" + (f"（第{po['version']}版）" if po["version"] > 1 else "")
    ws["A1"].font = Font(bold=True, size=14)
    for r, (k1, v1, k2, v2) in enumerate([("订单号", po["po_no"], "下单日期", po["order_date"]), ("采购方", po["buyer"], "供应商", SUPPLIER_CN),
                                           ("币种", po["currency"], "付款方式", PAY_CN.get(po["payment_days"], f"月结 {po['payment_days']} 天")),
                                           ("送货地址", po["ship_to"], "采购员", po["contact"])], start=3):
        ws.cell(r, 1, k1), ws.cell(r, 2, v1), ws.cell(r, 4, k2), ws.cell(r, 5, v2)
        if isinstance(v2, date):
            ws.cell(r, 5).number_format = "yyyy-mm-dd"
    header = ["行号", "客户料号", "物料描述", "订购数量", "含税单价", "金额", "要求交期"]
    for c, h in enumerate(header, start=1):
        ws.cell(8, c, h).font = Font(bold=True)
    r = 9
    for ln in po["lines"]:
        for c, v in enumerate([ln["line_no"], ln["customer_part_no"], ln["desc"], ln["qty"], ln["unit_price"], _amount(ln), ln["delivery_date"]], start=1):
            ws.cell(r, c, v)
        ws.cell(r, 7).number_format = "yyyy-mm-dd"
        r += 1
    ws.cell(r, 3, "合计"), ws.cell(r, 4, sum(x["qty"] for x in po["lines"])), ws.cell(r, 6, po["printed_total"])
    for col, width in zip("ABCDEFG", (8, 18, 32, 12, 12, 14, 14)):
        ws.column_dimensions[col].width = width
    return _save(wb)


def _xlsx_en(po: dict) -> bytes:
    wb = Workbook()
    h = wb.active
    h.title = "Header"
    for r, (k, v) in enumerate([("PO Number", po["po_no"]), ("Revision", po["version"]), ("PO Date", po["order_date"]), ("Buyer", po["buyer"]),
                                ("Vendor", SUPPLIER_EN), ("Currency", po["currency"]), ("Payment Terms", f"Net {po['payment_days']}"),
                                ("Ship To", po["ship_to"]), ("Contact", po["contact"]), ("Total Amount", po["printed_total"])], start=1):
        h.cell(r, 1, k).font = Font(bold=True)
        h.cell(r, 2, v)
        if isinstance(v, date):
            h.cell(r, 2).number_format = "dd-mmm-yyyy"
    ls = wb.create_sheet("Lines")
    for c, t in enumerate(["Line", "Customer P/N", "Description", "Quantity", "Unit Price", "Ext. Amount", "Need Date"], start=1):
        ls.cell(1, c, t).font = Font(bold=True)
    for r, ln in enumerate(po["lines"], start=2):
        for c, v in enumerate([ln["line_no"], ln["customer_part_no"], ln["desc"], ln["qty"], ln["unit_price"], _amount(ln), ln["delivery_date"]], start=1):
            ls.cell(r, c, v)
        ls.cell(r, 7).number_format = "dd-mmm-yyyy"
    return _save(wb)
