"""
附件解析：把 PDF / Excel 变成「文字 + 表格」。不调大模型。
  PDF    pdfplumber：逐页取文字和表格（有表格线的 PDF 能拿到表格，没有的只能拿文字）
  Excel  openpyxl：逐个工作表取单元格，日期单元格保留成 date
  扫描件 预留：文字太少时标记 needs_ocr，后面接 Qwen-VL（阶段 2 不做）
"""
import io
from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel


class ParsedDoc(BaseModel):
    name: str
    kind: str                                          # pdf / xlsx / unknown
    text: str = ""
    tables: list[list[list[str | None]]] = []          # PDF 里认出来的表格
    sheets: dict[str, list[list]] = {}                 # Excel：工作表名 → 行（单元格原值，日期是 date）
    needs_ocr: bool = False


def _cell(v):
    if isinstance(v, datetime):
        return v.date() if v.time() == datetime.min.time() else v
    return v


def parse_document(name: str, data: bytes) -> ParsedDoc:
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        import pdfplumber
        texts, tables = [], []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                texts.append(page.extract_text() or "")
                tables += [[[c.strip() if isinstance(c, str) else c for c in row] for row in t] for t in page.extract_tables()]
        text = "\n".join(texts)
        return ParsedDoc(name=name, kind="pdf", text=text, tables=tables, needs_ocr=len(text.strip()) < 30)
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        sheets = {ws.title: [[_cell(v) for v in row] for row in ws.iter_rows(values_only=True)] for ws in wb.worksheets}
        text = "\n".join(f"[{t}]\n" + "\n".join("\t".join("" if v is None else (v.isoformat() if isinstance(v, date) else str(v)) for v in row)
                                               for row in rows if any(v is not None for v in row)) for t, rows in sheets.items())
        return ParsedDoc(name=name, kind="xlsx", text=text, sheets=sheets)
    return ParsedDoc(name=name, kind="unknown")
