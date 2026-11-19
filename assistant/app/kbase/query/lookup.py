"""
结构化查询：参数、尺寸、公差、BOM 这类"数值问题"从 SQL 里精确取，不靠向量检索。
全部是参数化查询；只查当前生效版本；按文档的可见范围过滤（和向量检索用同一个 Scope，见 app/kbase/authz.py）。
返回的每条事实都带出处（文档、版本、来源位置），和文档切片一样可以被引用。
金蝶 ERP 数据（BOM）：SSO 用户按工号映射到金蝶用户后才能查，每次查询写审计日志；没有映射的不返回 ERP 数据并说明原因。
"""
from app.kbase import authz, store
from app.kbase.query.intent import label

_ACTIVE = "FROM kb_param p JOIN kb_document d ON d.doc_key = p.doc_key AND d.active_version = p.version WHERE {where}"


def _active(scope) -> tuple[str, list]:
    where, args = authz.as_scope(scope).sql("d")
    return _ACTIVE.format(where=where), args
_COLS = "p.model, p.param_key, p.param_name, p.value_num, p.value_text, p.unit, p.source_ref, d.doc_key, d.title, d.doc_type, p.version"
_OPS = {"大于": ">", "超过": ">", "高于": ">", "不低于": ">=", "至少": ">=", "小于": "<", "低于": "<", "不超过": "<=", "不高于": "<="}


def _fact(title: str, text: str, row: dict, kind: str = "param") -> dict:
    return {"type": "sql", "kind": kind, "doc_key": row.get("doc_key"), "version": row.get("version"), "title": row.get("title") or title,
            "section": title, "page": None, "chunk_id": None, "content": text, "score": 1.0}


def params_of(models: list[str], keys: list[str], scope) -> list[dict]:
    """指定型号的参数。没指定参数名时返回这个型号的全部参数"""
    if not models:
        return []
    frm, args = _active(scope)
    sql = f"SELECT {_COLS} {frm} AND p.model IN %s"
    args = [*args, models]
    if keys:
        sql += " AND p.param_key IN %s"
        args.append(keys)
    rows = store.query(sql + " ORDER BY p.model, FIELD(d.doc_type, 'DRAWING', 'DATASHEET', 'SELECTION'), p.id", args)
    seen, facts = set(), []
    by_model: dict[tuple, list] = {}
    for r in rows:                                    # 同一参数在多份文档里都有时，各列一次并注明出处，由回答说明以哪份为准
        if (r["model"], r["param_key"], r["doc_key"]) in seen:
            continue
        seen.add((r["model"], r["param_key"], r["doc_key"]))
        by_model.setdefault((r["model"], r["doc_key"]), []).append(r)
    for (model, _), rs in by_model.items():
        text = f"{model}（出自《{rs[0]['title']}》版本 {rs[0]['version']}）：" + "；".join(
            f"{r['param_name']} {r['value_text']}" + (f" {r['unit']}" if r["unit"] and r["unit"] not in r["value_text"] else "") for r in rs)
        facts.append(_fact(f"结构化参数 · {model}", text, rs[0]))
    return facts


def rank(key: str, series: str | None, scope, superlative: str | None, compare: tuple | None, limit: int = 10) -> list[dict]:
    """按参数比大小或筛选：'伺服里功率最大的''额定转矩大于 2 的'"""
    frm, args = _active(scope)
    sql = f"SELECT {_COLS} {frm} AND p.param_key = %s AND p.value_num IS NOT NULL"
    args = [*args, key]
    if series:
        sql += " AND (d.series = %s OR p.model LIKE %s)"
        args += [series, {"SERVO": "KL-SV-%", "STEPPER": "KL-ST-%", "BLDC": "KL-BL-%", "SHADED_POLE": "KL-SP-%",
                          "CROSSFLOW": "KL-CF-%", "GEAR": "KL-GM-%"}.get(series, "%")]
    if compare:
        sql += f" AND p.value_num {_OPS[compare[0]]} %s"
        args.append(compare[1])
    desc = superlative is None or superlative[1] in "大高重长"
    rows = store.query(sql + f" ORDER BY p.value_num {'DESC' if desc else 'ASC'}, p.model LIMIT 200", args)
    best: dict[str, dict] = {}
    for r in rows:
        best.setdefault(r["model"], r)
    picked = list(best.values())[:limit]
    if not picked:
        return []
    cond = f"{compare[0]} {compare[1]:g}" if compare else (superlative or "")
    text = f"按{label(key)}{cond}筛选排序（共 {len(best)} 个型号符合，列出前 {len(picked)} 个）：" + "；".join(
        f"{r['model']} {r['value_text']}" + (f" {r['unit']}" if r["unit"] and r["unit"] not in r["value_text"] else "") for r in picked)
    return [_fact(f"结构化参数 · {label(key)}排序", text, picked[0], "rank")]


def kd_user_of(scope) -> tuple[str | None, str]:
    """返回（金蝶用户, 说明）。MiniERP 账号本身就是 ERP 用户；SSO 用户按工号查映射表"""
    scope = authz.as_scope(scope)
    if scope.source != "sso":
        return scope.user, ""
    if not scope.employee_no:
        return None, "你的 SSO 账号没有工号，查不了金蝶 ERP 数据"
    row = store.one("SELECT kd_user FROM kb_kd_user WHERE employee_no = %s AND enabled = 1", (scope.employee_no,))
    return (row["kd_user"], "") if row else (None, f"工号 {scope.employee_no} 没有对应的金蝶账号，查不了 ERP 数据，请联系管理员开通")


def audit(scope, action: str, target: str, detail: dict, result: str, kd_user: str | None = None) -> None:
    scope = authz.as_scope(scope)
    store.execute("INSERT INTO kb_audit_log (user_id, employee_no, kd_user, action, target, detail, result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                  (scope.user, scope.employee_no, kd_user, action, target[:200], store.dumps(detail), result))


def bom_of(models: list[str], scope=None, notes: list | None = None) -> list[dict]:
    """金蝶 BOM（本地镜像）。BOM 属于 ERP 主数据，不按文档权限控制，按「这个人有没有金蝶账号」控制，并写审计"""
    kd_user, why = kd_user_of(scope)
    if kd_user is None:
        audit(scope, "ERP_BOM_QUERY", ",".join(models[:3]), {"reason": why}, "DENIED")
        if notes is not None:
            notes.append(why)
        return []
    facts = []
    for m in models[:3]:
        mat = store.one("SELECT f_name, f_specification FROM kd_material WHERE f_number = %s", (m,))
        rows = store.query("SELECT f_child, f_child_name, f_numerator / NULLIF(f_denominator, 0) AS qty, f_unit, f_bom_number FROM kd_bom "
                           "WHERE f_parent = %s ORDER BY id", (m,))
        if not rows:
            continue
        text = (f"{m}（{mat['f_name']}，规格 {mat['f_specification']}）" if mat else m) + f" 的金蝶 BOM（{rows[0]['f_bom_number']}）：" + "；".join(
            f"{r['f_child']} {r['f_child_name']} ×{float(r['qty']):g}{r['f_unit']}" for r in rows)
        facts.append({"type": "erp", "kind": "bom", "doc_key": None, "version": None, "title": f"金蝶 BOM · {m}", "section": "金蝶物料清单",
                      "page": None, "chunk_id": None, "content": text, "score": 1.0})
    if facts or models:
        audit(scope, "ERP_BOM_QUERY", ",".join(models[:3]), {"returned": [f["title"] for f in facts]}, "OK", kd_user)
    return facts


def alerts_of(models: list[str], scope) -> list[dict]:
    """图纸和 ERP 不一致的告警，问到这个型号时一并带出"""
    if not models:
        return []
    where, args = authz.as_scope(scope).sql("d")
    rows = store.query(
        "SELECT a.message, d.doc_key, d.title, a.version FROM kb_alert a JOIN kb_document d ON d.doc_key = a.doc_key AND d.active_version = a.version "
        f"WHERE a.status = 'OPEN' AND d.product_code IN %s AND {where}", (models, *args))
    return [_fact("图纸与 ERP 交叉校验", f"入库时的交叉校验发现（{r['title']} 版本 {r['version']}）：{r['message']}", r, "alert") for r in rows]
