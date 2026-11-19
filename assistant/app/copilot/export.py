"""
报表导出：唯一会让数据离开系统的操作，所以分两步，中间必须由人确认。

  start    只生成预览（行数、列、敏感字段），把这次导出请求记在内存里，不生成文件
  resume   用户点了"确认导出"才真正写 Excel；点"取消"就丢弃

导出的是用户刚才看到的那份结果（已经过权限过滤和打码），文件首行带导出人和时间，并在审计日志里记一笔。
"""
import re
import uuid
from datetime import datetime
import threading
import time

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.copilot import audit, memory
from app.shared.runtime.logger import PROJECT_ROOT, logger

EXPORT_DIR = PROJECT_ROOT / "output" / "export"
_SENSITIVE = re.compile(r"工资|薪|应发|实发|补差|成本|毛利|单价|价差|身份证|银行卡|手机|信用额度")


PENDING_TTL = 30 * 60                      # 等待确认的导出请求保留 30 分钟
_pending: dict[str, dict] = {}             # export_id → {username, payload, audit_id, preview, created, file_path, file_name}
_lock = threading.Lock()


def _preview(payload: dict) -> dict:
    cols = [c["name"] for c in payload["result"]["columns"]]
    return {"title": payload["title"], "rows": len(payload["result"]["rows"]), "columns": cols,
            "sensitive": [c for c in cols if _SENSITIVE.search(c)], "masked": payload.get("masked", []),
            "scope_notes": payload.get("scope_notes", []), "extra_tables": [e["title"] for e in payload.get("extra", [])]}


def _sheet(ws, title: str, result: dict, banner: str) -> None:
    ws.append([banner])
    ws["A1"].font = Font(color="888888", italic=True, size=9)
    ws.append([title])
    ws["A2"].font = Font(bold=True, size=12)
    names = [c["name"] for c in result["columns"]]
    ws.append(names)
    for cell in ws[3]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="3A6EA5")
        cell.alignment = Alignment(horizontal="center")
    for r in result["rows"]:
        ws.append([r.get(n) for n in names])
    for i, c in enumerate(result["columns"], start=1):
        letter = ws.cell(row=3, column=i).column_letter
        ws.column_dimensions[letter].width = max(12, min(40, len(c["name"]) * 2 + 6))
        fmt = {"pct": "0.00%", "money": "#,##0.00", "int": "#,##0"}.get(c["type"])
        if fmt:
            for row in ws.iter_rows(min_row=4, min_col=i, max_col=i):
                row[0].number_format = fmt
    ws.freeze_panes = "A4"


def _write(export_id: str, req: dict) -> None:
    payload = req["payload"]
    stamp = datetime.now()
    banner = f"导出人：{req['username']}　导出时间：{stamp:%Y-%m-%d %H:%M:%S}　来源：经营与人力数据分析 Copilot　仅限内部使用"
    wb = Workbook()
    _sheet(wb.active, payload["title"], payload["result"], banner)
    wb.active.title = "结果"
    for i, e in enumerate(payload.get("extra", []), start=1):
        _sheet(wb.create_sheet(f"附表{i}"), e["title"], e["result"], banner)
    info = wb.create_sheet("说明")
    for line in [("问题", payload.get("question", "")), ("数据范围", "；".join(payload.get("scope_notes", [])) or "无限制"),
                 ("打码字段", "、".join(payload.get("masked", [])) or "无"), ("执行的 SQL", payload.get("sql", ""))]:
        info.append(list(line))
    info.column_dimensions["A"].width, info.column_dimensions["B"].width = 14, 120
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"{export_id}.xlsx"
    wb.save(path)
    req["file_path"] = str(path)
    req["file_name"] = f"{re.sub(r'[^\w一-龥]+', '_', payload['title'])[:40]}_{stamp:%Y%m%d_%H%M%S}.xlsx"
    if req.get("audit_id"):
        audit.mark_exported(req["audit_id"])
    logger.info(f"[copilot] 导出报表 {req['file_name']}，导出人 {req['username']}，{len(payload['result']['rows'])} 行")


def start(result_id: str, username: str, audit_id: int | None = None) -> dict:
    """发起导出：只返回预览，等用户确认"""
    payload = memory.result_get(result_id)
    if not payload:
        raise ValueError("这份结果已经过期，请重新查询后再导出")
    if payload["username"] != username:
        raise PermissionError("只能导出自己查询的结果")
    export_id = uuid.uuid4().hex[:16]
    preview = _preview(payload)
    with _lock:
        for k in [k for k, v in _pending.items() if time.time() - v["created"] > PENDING_TTL]:
            _pending.pop(k)
        _pending[export_id] = {"username": username, "payload": payload, "audit_id": audit_id or 0, "preview": preview, "created": time.time()}
    return {"export_id": export_id, "preview": preview}


def _mine(export_id: str, username: str) -> dict:
    req = _pending.get(export_id)
    if not req or req["username"] != username:
        raise PermissionError("导出请求不存在或不属于你")
    return req


def resume(export_id: str, username: str, decision: str) -> dict:
    """用户确认后才生成文件；取消则丢弃这次请求"""
    with _lock:
        req = _mine(export_id, username)
        if decision != "confirm":
            _pending.pop(export_id, None)
            return {"export_id": export_id, "confirmed": False, "file_name": ""}
        if not req.get("file_path"):
            _write(export_id, req)
    return {"export_id": export_id, "confirmed": True, "file_name": req["file_name"]}


def file_of(export_id: str, username: str) -> tuple[str, str]:
    req = _mine(export_id, username)
    if not req.get("file_path"):
        raise FileNotFoundError("文件还没有生成")
    return req["file_path"], req["file_name"]
