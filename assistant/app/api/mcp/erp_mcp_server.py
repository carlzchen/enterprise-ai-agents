"""
MiniERP MCP 服务：把 ERP 能力以 MCP 工具暴露给 AI 助手 / Agent（Claude、技术助手、财务分析 Agent 等）。

设计：
- 网关模式：工具不直连数据库，全部调用 ERP REST API，复用后端的 JWT 鉴权、RBAC 角色权限、敏感列裁剪和审计日志，
  MCP 调用方能看到的数据与同一用户在网页上看到的完全一致。
- 身份：HTTP 模式下由 MCP 客户端传 `Authorization: Bearer <ERP JWT>`；stdio 模式（本机桌面客户端）
  使用环境变量 MINIERP_TOKEN，或 MINIERP_SYNC_USER / MINIERP_SYNC_PASSWORD 登录换取 token。
- 追踪：每次工具调用生成 X-Trace-Id 传给 ERP，两边日志可按同一追踪号串联。
- 写操作只生成草稿（销售订单 = DRAFT、调拨单 = CREATED），确认 / 发出仍需人在 ERP 里操作。

运行：
  uv run python -m app.api.mcp.erp_mcp_server                  # streamable-http，默认 http://127.0.0.1:8092/mcp
  uv run python -m app.api.mcp.erp_mcp_server --transport stdio
"""
import argparse
import os
import time
import uuid
from datetime import date
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from app.infra.config.settings import settings
from app.shared.runtime.logger import logger

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
DRAFT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)

mcp = MCPServer(
    name="minierp",
    title="MiniERP 企业数据",
    version="1.0.0",
    instructions=(
        "MiniERP 是一家微特电机制造企业的 ERP。先用 list_datasets 看有哪些数据集及可过滤列，"
        "再用 query_dataset 查询；追溯、BOM、排产、对账有专用工具。金额单位为人民币元。"
        "写工具只生成草稿，需要用户在 ERP 中确认。"),
)

_token_cache: dict[str, Any] = {"token": None, "at": 0.0}


def _env_token() -> str:
    if os.getenv("MINIERP_TOKEN"):
        return os.environ["MINIERP_TOKEN"]
    if _token_cache["token"] and time.time() - _token_cache["at"] < 6 * 3600:
        return _token_cache["token"]
    if not (settings.minierp_sync_user and settings.minierp_sync_password):
        raise ToolError("未提供 ERP 身份：HTTP 模式请传 Authorization: Bearer <token>，stdio 模式请配置 MINIERP_TOKEN 或 MINIERP_SYNC_USER/PASSWORD")
    r = httpx.post(f"{settings.minierp_api_base}/auth/login",
                   json={"username": settings.minierp_sync_user, "password": settings.minierp_sync_password}, timeout=15)
    r.raise_for_status()
    _token_cache.update(token=r.json()["token"], at=time.time())
    return _token_cache["token"]


def _auth_header(ctx: Context | None) -> str:
    headers = None
    try:
        headers = ctx.headers if ctx is not None else None
    except Exception:  # stdio 或上下文不可用
        headers = None
    if headers:
        auth = headers.get("authorization") or headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            return auth
    return f"Bearer {_env_token()}"


def _call(ctx: Context | None, method: str, path: str, *, params: dict | None = None, body: Any = None, tool: str = "") -> Any:
    trace_id = uuid.uuid4().hex[:16]
    start = time.time()
    with logger.contextualize(trace_id=trace_id):
        try:
            r = httpx.request(method, f"{settings.minierp_api_base}{path}", params=params, json=body, timeout=60,
                              headers={"Authorization": _auth_header(ctx), "X-Trace-Id": trace_id})
        except httpx.HTTPError as e:
            logger.exception(f"[mcp:{tool}] ERP 调用失败 {method} {path}")
            raise ToolError(f"ERP 服务不可用：{e}（追踪号 {trace_id}）") from e
        ms = int((time.time() - start) * 1000)
        if r.status_code >= 400:
            msg = r.json().get("message", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
            logger.warning(f"[mcp:{tool}] {method} {path} -> {r.status_code} {ms}ms {msg}")
            raise ToolError(f"ERP 返回 {r.status_code}：{msg}（追踪号 {trace_id}）")
        logger.info(f"[mcp:{tool}] {method} {path} -> {r.status_code} {ms}ms")
        return r.json() if r.content else None


def _find_id(ctx: Context | None, dataset: str, field: str, value: str) -> int:
    rows = _call(ctx, "GET", f"/views/{dataset}", params={"kw": value, "size": 50}, tool="lookup")["records"]
    for row in rows:
        if str(row.get(field)) == value:
            return row["id"]
    raise ToolError(f"找不到 {dataset}.{field} = {value}")


# ---------------- 只读工具 ----------------

@mcp.tool(annotations=READ)
def list_datasets(ctx: Context) -> list[dict]:
    """列出当前用户有权访问的 ERP 数据集（视图）：key、中文说明、可精确过滤的列、关键字搜索列、日期列。查询前先调用它。"""
    return _call(ctx, "GET", "/views", tool="list_datasets")


@mcp.tool(annotations=READ)
def query_dataset(ctx: Context, key: str, filters: dict[str, str] | None = None, keyword: str | None = None,
                  date_from: str | None = None, date_to: str | None = None, sort: str | None = None,
                  order: str = "desc", page: int = 1, size: int = 20) -> dict:
    """查询一个数据集。key 来自 list_datasets；filters 只能用该数据集声明的过滤列（精确匹配）；
    keyword 在关键字列中模糊搜索；date_from/date_to 为 YYYY-MM-DD，作用于日期列；size 最大 100。
    返回 {records, total, page, size}。"""
    params: dict[str, Any] = {k: v for k, v in (filters or {}).items()}
    params.update(page=page, size=min(size, 100), order=order)
    if keyword:
        params["kw"] = keyword
    if date_from:
        params["dateFrom"] = date_from
    if date_to:
        params["dateTo"] = date_to
    if sort:
        params["sort"] = sort
    return _call(ctx, "GET", f"/views/{key}", params=params, tool="query_dataset")


@mcp.tool(annotations=READ)
def get_stock(ctx: Context, item_code: str) -> list[dict]:
    """按物料 / 产品编码查询各仓库现存量（含半成品仓、前置仓）。有成本权限的用户同时返回平均成本和库存金额。"""
    rows = _call(ctx, "GET", "/views/stock", params={"kw": item_code, "size": 50}, tool="get_stock")["records"]
    return [r for r in rows if r.get("item_code") == item_code] or rows


@mcp.tool(annotations=READ)
def trace_lot(ctx: Context, lot_no: str) -> dict:
    """正向追溯：原材料批次（批次号 = 到货单号，如 GR2603050001）→ 用到它的组件工单 / 成品工单 → 发货单 → 客户，用于召回排查。"""
    return _call(ctx, "GET", f"/trace/lot/{lot_no}", tool="trace_lot")


@mcp.tool(annotations=READ)
def trace_complaint(ctx: Context, complaint_no: str) -> dict:
    """反向追溯：客诉单号 → 发货单 → 成品批次 → 逐层半成品批次 → 原材料批次、供应商、IQC 结果。"""
    cid = _find_id(ctx, "complaints", "complaint_no", complaint_no)
    return _call(ctx, "GET", f"/trace/complaint/{cid}", tool="trace_complaint")


@mcp.tool(annotations=READ)
def bom_tree(ctx: Context, product_code: str, qty: float = 1) -> dict:
    """多级 BOM 展开：成品 → 半成品 → 外购件，每层用量、总需求、现存量、参考成本、是否有替代料。"""
    pid = _find_id(ctx, "products", "code", product_code)
    return _call(ctx, "GET", f"/md/products/{pid}/bom-tree", params={"qty": qty}, tool="bom_tree")


@mcp.tool(annotations=READ)
def latest_production_plan(ctx: Context, only_exceptions: bool = True) -> dict:
    """最新 APS 排产方案。only_exceptions=True 时只返回延期、缺料、等待到货或使用替代料的工单。"""
    plan = _call(ctx, "GET", "/aps/latest", tool="latest_production_plan")
    items = plan.get("items", [])
    if only_exceptions:
        items = [i for i in items if i.get("late_days", 0) > 0 or i.get("kit_status") != "READY" or i.get("substitution")]
    keep = ("wo_no", "product_code", "bom_level", "parent_wo_no", "line_code", "qty", "start_date", "end_date",
            "due_date", "late_days", "kit_status", "earliest_start", "shortage", "substitution")
    return {"plan_no": plan.get("plan_no"), "plan_date": plan.get("plan_date"), "order_count": plan.get("order_count"),
            "late_count": plan.get("late_count"), "items": [{k: i.get(k) for k in keep} for i in items]}


@mcp.tool(annotations=READ)
def gl_reconciliation(ctx: Context) -> list[dict]:
    """总账与子账对账：原材料、自制半成品、库存商品、调拨在途、应收、应付、应付暂估的总账余额、子账余额与差异（需财务权限）。"""
    return _call(ctx, "GET", "/gl/reconciliation", tool="gl_reconciliation")


@mcp.tool(annotations=READ)
def income_statement(ctx: Context, date_from: str, date_to: str) -> dict:
    """利润表（不含期末结转凭证），日期格式 YYYY-MM-DD（需财务权限）。"""
    return _call(ctx, "GET", "/gl/income-statement", params={"from": date_from, "to": date_to}, tool="income_statement")


@mcp.tool(annotations=READ)
def monthly_kpis(ctx: Context, month: str) -> dict:
    """经营看板 KPI（收入、毛利、环比、同比等），month 格式 YYYY-MM。"""
    return _call(ctx, "GET", "/bi/kpis", params={"month": month}, tool="monthly_kpis")


@mcp.tool(annotations=READ)
def my_pending_approvals(ctx: Context, page: int = 1) -> dict:
    """当前用户的 OA 待办审批（请假、报销、出差、用印、离职、工资发放等）。批准 / 驳回需本人在 ERP 审批中心操作。"""
    return _call(ctx, "GET", "/oa/my/todo", params={"page": page, "size": 20}, tool="my_pending_approvals")


@mcp.tool(annotations=READ)
def approval_detail(ctx: Context, instance_id: int) -> dict:
    """审批流程详情：表单、审批路线（每个节点的审批人、会签 / 或签、自动跳过原因）、审批记录。"""
    return _call(ctx, "GET", f"/oa/instances/{instance_id}", tool="approval_detail")


@mcp.tool(annotations=READ)
def find_employee(ctx: Context, keyword: str, dept_id: int | None = None) -> dict:
    """按姓名 / 工号查员工（部门、岗位、直属上级、产线班组、在职状态）。薪资字段仅人事 / 财务 / 管理层可见。"""
    params: dict[str, Any] = {"kw": keyword, "size": 20}
    if dept_id:
        params["deptId"] = dept_id
    return _call(ctx, "GET", "/org/employees", params=params, tool="find_employee")


@mcp.tool(annotations=READ)
def hr_dashboard(ctx: Context, month: str | None = None) -> dict:
    """HR 看板：在职人数、入离职、流失率趋势、出勤率、按费用归集的人工成本、各产线人均工资 / 加班 / 单位人工成本。month 为 YYYY-MM。"""
    return _call(ctx, "GET", "/hr/dashboard", params={"month": month} if month else None, tool="hr_dashboard")


@mcp.tool(annotations=READ)
def shop_floor_board(ctx: Context, date: str | None = None) -> dict:
    """车间看板：某天各产线 OEE（时间 / 性能 / 合格品率）、劳动效率、停机损失、安灯、IPQC 不合格，及近 45 天 OEE 趋势。date 为 YYYY-MM-DD，默认最近一天。"""
    b = _call(ctx, "GET", "/mes/board", params={"date": date} if date else None, tool="shop_floor_board")
    b.pop("trend", None)  # 趋势数据量大，工具返回只保留当天与月度
    return b


@mcp.tool(annotations=READ)
def serial_trace(ctx: Context, sn: str) -> dict:
    """伺服整机序列号追溯：出厂测试值（反电势、空载电流、编码器零位、绝缘、振动）、生产工单批次、出货单与客户、该工单的首检巡检记录。"""
    return _call(ctx, "GET", f"/mes/serials/{sn}", tool="serial_trace")


@mcp.tool(annotations=READ)
def spc_analysis(ctx: Context, product_code: str, characteristic: str, date_from: str, date_to: str) -> dict:
    """SPC 均值-极差图与过程能力：characteristic = back_emf / no_load_current / encoder_offset / insulation_mohm / vibration。
    返回 Cpk、Ppk、控制限、判异点（超出控制限、连续 9 点同侧、连续 6 点趋势）与测试不合格率。"""
    r = _call(ctx, "GET", "/qms/spc", params={"product": product_code, "characteristic": characteristic, "from": date_from, "to": date_to},
              tool="spc_analysis")
    return {k: r[k] for k in ("product", "characteristic", "spec", "center", "ucl", "lcl", "sigma", "cp", "cpk", "ppk", "failRate", "violations")}


@mcp.tool(annotations=READ)
def equipment_reliability(ctx: Context, equipment_code: str, date_from: str | None = None, date_to: str | None = None) -> dict:
    """设备可靠性：期间故障次数、MTBF、MTTR、固有可用度、保养计划与最近维修记录。equipment_code 如 EQ-L8-PRE-01。"""
    eid = _find_id(ctx, "equipment", "code", equipment_code)
    params = {k: v for k, v in {"from": date_from, "to": date_to}.items() if v}
    r = _call(ctx, "GET", f"/eam/equipment/{eid}/reliability", params=params or None, tool="equipment_reliability")
    r["workOrders"] = r.get("workOrders", [])[:15]
    return r


@mcp.tool(annotations=READ)
def aql_sampling_plan(ctx: Context, lot_size: int, aql: float) -> dict:
    """GB/T 2828.1 抽样方案（一般检验水平 II，正常检验一次抽样）：样本量字码、样本量、Ac、Re。AQL 支持 0.4/0.65/1.0/1.5/2.5/4.0。"""
    return _call(ctx, "GET", "/qms/aql-plan", params={"lotSize": lot_size, "aql": aql}, tool="aql_sampling_plan")


@mcp.tool(annotations=READ)
def search_knowledge(ctx: Context, query: str, space: str | None = None) -> list[dict]:
    """知识库全文检索（中文）：制度流程 POL、工艺技术 PROC、质量经验 QUAL（含 8D 经验教训）、设备维修 EQP、研发设计 RND（含 ECN 说明）。
    返回页面 id、标题、空间与命中片段，再用 get_knowledge_page 读全文。"""
    params: dict[str, Any] = {"q": query, "limit": 10}
    if space:
        params["space"] = space
    return _call(ctx, "GET", "/kb/search", params=params, tool="search_knowledge")


@mcp.tool(annotations=READ)
def get_knowledge_page(ctx: Context, page_id: int) -> dict:
    """读取知识库页面全文（Markdown）与版本信息。"""
    p = _call(ctx, "GET", f"/kb/pages/{page_id}", tool="get_knowledge_page")
    return {k: p.get(k) for k in ("id", "title", "space_name", "version", "updated_at", "source_type", "source_ref", "body")}


@mcp.tool(annotations=READ)
def rd_project_status(ctx: Context, project_key: str) -> dict:
    """研发项目状态：负责人、关联 NPI 阶段、当前迭代（承诺 / 完成故事点）、看板各状态问题数、史诗进度、未关闭缺陷。
    project_key 如 JM（关节模组）、DH（灵巧手）、SV（伺服平台维护）。"""
    pid = _find_id(ctx, "pm-projects", "project_key", project_key)
    b = _call(ctx, "GET", f"/pm/projects/{pid}/board", tool="rd_project_status")
    by_status: dict[str, int] = {}
    for i in b.get("issues", []):
        by_status[i["status"]] = by_status.get(i["status"], 0) + 1
    return {"project": {k: b.get(k) for k in ("project_key", "name", "lead_name", "plm_project_no", "plm_stage", "open_issues", "open_bugs", "logged_hours")},
            "sprint": b.get("sprint"), "board": by_status, "epics": b.get("epics"),
            "open_bugs": [{k: i[k] for k in ("issue_key", "title", "priority", "assignee_name", "complaint_no")} for i in b.get("issues", [])
                          if i["type"] == "BUG" and i["status"] != "DONE"][:20]}


@mcp.tool(annotations=READ)
def fixed_assets_overview(ctx: Context) -> dict:
    """固定资产概览：按类别的原值、累计折旧、净值，以及每月折旧按费用科目（制造费用 5101 / 销售 6601 / 管理 6602 / 研发 6604）的分布。"""
    r = _call(ctx, "GET", "/fa/summary", tool="fixed_assets_overview")
    r["monthly"] = r.get("monthly", [])[-48:]
    return r


@mcp.tool(annotations=READ)
def bank_reconciliation(ctx: Context, period: str) -> dict:
    """银行余额调节表（期末口径）：账面与对账单余额、四类未达账项合计与明细、调节后是否一致。period 为 YYYY-MM。"""
    return _call(ctx, "POST", "/treasury/reconcile", params={"period": period}, tool="bank_reconciliation")


@mcp.tool(annotations=READ)
def cash_forecast(ctx: Context, start_date: str, weeks: int = 13) -> dict:
    """资金滚动预测：按周的流入、流出、期末余额与构成（客户回款、供应商付款、工资社保、税款、出口退税、日常费用），最低余额与低于安全余额的首周。"""
    return _call(ctx, "GET", "/treasury/forecast", params={"start": start_date, "weeks": weeks}, tool="cash_forecast")


@mcp.tool(annotations=READ)
def budget_execution(ctx: Context, year: int, month_to: int | None = None) -> dict:
    """费用预算执行：部门 × 费用类别的预算、实际、执行率、超预算月数，及月度汇总。"""
    params: dict[str, Any] = {"year": year}
    if month_to:
        params["monthTo"] = month_to
    return _call(ctx, "GET", "/treasury/budget", params=params, tool="budget_execution")


@mcp.tool(annotations=READ)
def find_contracts(ctx: Context, keyword: str | None = None, contract_type: str | None = None, status: str | None = "ACTIVE") -> dict:
    """查询合同：相对方 / 标题关键字，类型 SALES / PURCHASE / SERVICE / NDA，状态 ACTIVE / APPROVING / EXPIRED。返回金额、履约金额、期限与距到期天数。"""
    params: dict[str, Any] = {"size": 30}
    if keyword:
        params["kw"] = keyword
    if contract_type:
        params["type"] = contract_type
    if status:
        params["status"] = status
    return _call(ctx, "GET", "/views/contracts", params=params, tool="find_contracts")


@mcp.tool(annotations=READ)
def vat_returns(ctx: Context, period_from: str | None = None) -> list[dict]:
    """增值税月度申报（生产企业免抵退）：内销销项、进项、上期留抵、出口 FOB、应纳、免抵退、应退、免抵、期末留抵、附加税、缴纳与退税到账日期。"""
    rows = _call(ctx, "GET", "/views/vat-returns", params={"size": 36, "sort": "period"}, tool="vat_returns")["records"]
    return [r for r in rows if not period_from or r["period"] >= period_from]


@mcp.tool(annotations=READ)
def after_sales_overview(ctx: Context, date_from: str, date_to: str) -> dict:
    """售后服务概览（日期 YYYY-MM-DD）：工单量、SLA 达成率、平均响应 / 解决时长、一次修复率、满意度、RMA 与保内成本、
    质量保证金余额与计提比例、月度趋势、故障类型、伺服按生产月的现场故障率（每千台，可定位批次问题）。"""
    r = _call(ctx, "GET", "/aftersales/dashboard", params={"from": date_from, "to": date_to}, tool="after_sales_overview")
    r["servoFieldFailure"] = [x for x in r.get("servoFieldFailure", []) if x.get("shipped", 0) >= 200]
    return r


@mcp.tool(annotations=READ)
def warranty_lookup(ctx: Context, sn: str) -> dict:
    """按伺服序列号查询：产品、生产工单与出厂测试结果、客户与发货日期、保修期与是否在保、历史服务工单。"""
    return _call(ctx, "GET", f"/aftersales/warranty/{sn}", tool="warranty_lookup")


@mcp.tool(annotations=READ)
def it_service_overview(ctx: Context, date_from: str, date_to: str) -> dict:
    """IT 服务台概览：故障 / 请求 / 变更数量、SLA 达成率、MTTR、按优先级与分类统计、P1 重大故障及关联变更、CMDB 汇总、软件许可席位合规。"""
    return _call(ctx, "GET", "/itsm/dashboard", params={"from": date_from, "to": date_to}, tool="it_service_overview")


@mcp.tool(annotations=READ)
def ehs_overview(ctx: Context, date_from: str, date_to: str) -> dict:
    """安全环保概览：LTIFR / TRIR / 严重率、无损工天数、事故金字塔、隐患发现与按期整改率（按部门 / 类别）、作业票、可记录事故明细、危废贮存天数与预警。"""
    return _call(ctx, "GET", "/ehs/dashboard", params={"from": date_from, "to": date_to}, tool="ehs_overview")


@mcp.tool(annotations=READ)
def energy_report(ctx: Context, period: str) -> dict:
    """能源月报（period 为 YYYY-MM）：电量与尖峰平谷结构、电费 / 基本电费 / 水费、综合电价、单位产品电耗（按产线及趋势）、碳排放、
    各电表构成、空压站非生产日空载电量（识别管网泄漏）。"""
    r = _call(ctx, "GET", "/energy/dashboard", params={"period": period}, tool="energy_report")
    r.pop("daily", None)
    return r


# ---------------- 草稿类写工具（需人工确认后才生效） ----------------

@mcp.tool(annotations=DRAFT)
def submit_leave_request(ctx: Context, leave_type: str, start_date: str, end_date: str, reason: str) -> dict:
    """以当前用户身份发起请假审批（不会自动批准）。leave_type：ANNUAL 年假 / SICK 病假 / PERSONAL 事假；日期 YYYY-MM-DD。
    返回审批路线，按组织关系自动找直属上级、部门负责人（≥3 天）、人力资源（≥5 天或病假）。"""
    r = _call(ctx, "POST", "/oa/submit", tool="submit_leave_request", body={"defCode": "LEAVE", "form": {
        "leaveType": leave_type, "startDate": start_date, "endDate": end_date, "reason": reason}})
    return {"instance_no": r.get("instance_no"), "status": r.get("status"), "title": r.get("title"),
            "route": [{"name": n["name"], "approvers": n["approvers"], "note": n.get("note")} for n in r.get("route", [])]}


@mcp.tool(annotations=DRAFT)
def create_sales_order_draft(ctx: Context, customer_code: str, required_date: str, lines: list[dict],
                             remark: str | None = None) -> dict:
    """创建销售订单草稿（状态 DRAFT，不占用信用额度、不影响排产），需销售在 ERP 中确认。
    lines: [{"product_code": "KL-SV-80-750W", "qty": 100, "unit_price": 可选, 缺省用标准售价}]，单价为客户结算币种不含税。"""
    options = _call(ctx, "GET", "/md/options", tool="create_sales_order_draft")
    cust = next((c for c in options["customers"] if c["code"] == customer_code), None)
    if cust is None:
        raise ToolError(f"客户不存在：{customer_code}")
    products = {p["code"]: p for p in options["products"]}
    req_lines = []
    for l in lines:
        p = products.get(l["product_code"])
        if p is None:
            raise ToolError(f"产品不存在：{l['product_code']}")
        price = l.get("unit_price") or (p["listPrice"] if cust["currency"] == "CNY" else None)
        if not price:
            raise ToolError(f"外币客户必须提供 unit_price：{l['product_code']}")
        req_lines.append({"productId": p["id"], "qty": l["qty"], "unitPrice": price})
    order = _call(ctx, "POST", "/sales/orders", tool="create_sales_order_draft", body={
        "customerId": cust["id"], "orderDate": date.today().isoformat(), "requiredDate": required_date,
        "salesperson": None, "remark": (remark or "") + "（由 AI 助手创建的草稿）", "lines": req_lines})
    return {"order_no": order.get("orderNo"), "id": order.get("id"), "status": order.get("status"),
            "next_step": "请在 ERP 销售订单详情中核对并确认（确认时检查信用额度）"}


@mcp.tool(annotations=DRAFT)
def create_transfer_draft(ctx: Context, from_warehouse_code: str, to_warehouse_code: str, lines: list[dict],
                          remark: str | None = None) -> dict:
    """创建调拨单（状态 CREATED，库存未移动），需仓管在 ERP 中发出。
    仓库编码：WH-RAW / WH-SEMI / WH-FG / WH-FG-EC / WH-FG-HK；lines: [{"item_code": "...", "qty": 100}]，编码可为产品或物料。"""
    options = _call(ctx, "GET", "/md/options", tool="create_transfer_draft")
    whs = {w["code"]: w["id"] for w in options["warehouses"]}
    items = {p["code"]: ("PRODUCT", p["id"]) for p in options["products"]}
    items.update({m["code"]: ("MATERIAL", m["id"]) for m in options["materials"]})
    body_lines = []
    for l in lines:
        if l["item_code"] not in items:
            raise ToolError(f"编码不存在：{l['item_code']}")
        t, i = items[l["item_code"]]
        body_lines.append({"itemType": t, "itemId": i, "qty": l["qty"]})
    r = _call(ctx, "POST", "/transfers", tool="create_transfer_draft", body={
        "fromWarehouseId": whs[from_warehouse_code], "toWarehouseId": whs[to_warehouse_code],
        "remark": (remark or "") + "（由 AI 助手创建）", "lines": body_lines})
    return {"transfer_no": r.get("transfer_no"), "status": r.get("status"), "next_step": "请仓管在 ERP 调拨单详情中发出"}


@mcp.tool(annotations=DRAFT)
def create_rd_issue(ctx: Context, project_key: str, issue_type: str, title: str, description: str | None = None,
                    priority: str = "MEDIUM", story_points: int | None = None) -> dict:
    """在研发项目中新建问题（进入待办池，不分配、不改变任何迭代）。issue_type：STORY / TASK / BUG；priority：HIGHEST / HIGH / MEDIUM / LOW。"""
    pid = _find_id(ctx, "pm-projects", "project_key", project_key)
    r = _call(ctx, "POST", "/pm/issues", tool="create_rd_issue", body={"projectId": pid, "type": issue_type, "title": title,
              "description": (description or "") + "\n（由 AI 助手创建）", "priority": priority, "storyPoints": story_points})
    return {"issue_key": r.get("issue_key"), "status": r.get("status"), "next_step": "请项目负责人在看板中排期"}


@mcp.tool(annotations=DRAFT)
def create_it_ticket(ctx: Context, ticket_type: str, category: str, title: str, impact: str = "LOW", urgency: str = "MEDIUM",
                     description: str | None = None) -> dict:
    """以当前用户身份提交 IT 工单（只是报修 / 申请，由信息部受理）。ticket_type：INCIDENT 故障 / REQUEST 服务请求；
    category 如 电脑故障、网络、打印机、账号密码、邮箱、ERP、MES、权限申请、软件安装；impact / urgency：HIGH / MEDIUM / LOW，决定优先级 P1~P4。"""
    r = _call(ctx, "POST", "/itsm/tickets", tool="create_it_ticket", body={"type": ticket_type, "category": category, "title": title,
              "description": (description or "") + "（由 AI 助手代为提交）", "impact": impact, "urgency": urgency})
    return {"id": r.get("id"), "next_step": "信息部按优先级 SLA 受理，可在 ERP「IT 服务台 → IT 工单」查看进度"}


def main() -> None:
    parser = argparse.ArgumentParser(description="MiniERP MCP server")
    parser.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    parser.add_argument("--host", default=os.getenv("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_PORT", "8092")))
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run("stdio")
    else:
        logger.info(f"MiniERP MCP 服务启动：http://{args.host}:{args.port}/mcp")
        mcp.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
