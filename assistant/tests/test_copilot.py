"""Copilot 核心逻辑的单元测试：都是纯函数，不依赖数据库、向量库和大模型"""
import re
from datetime import date
from pathlib import Path

import pytest
import sqlglot

from app.copilot import analysis, chart, labels, result, router, sqlguard
from app.copilot.permission import UserContext, access_level, hidden_columns, row_filter, secured_source
from app.copilot.policy import split_sections
from app.copilot.schemas import TOOL_MAP, SqlDraft
from app.copilot.semantic.loader import semantic
from app.copilot.timeparse import parse_period, previous, same_period_last_year

semantic.load(with_db=False)
T = date(2026, 10, 6)
GM = UserContext("gm", "MANAGEMENT", 1, "林总", 1, "总经办", (1,), ("总经办",), ())
HR = UserContext("hr01", "HR", 3, "何雅琴", 3, "人力资源部", (3,), ("人力资源部",), ())
PROD = UserContext("prod01", "PRODUCTION", 11, "孙宏伟", 11, "生产部", (11, 12, 13), ("生产部", "家电电机车间", "智能驱控车间"), ("L1", "L5"))
SALES = UserContext("sales01", "SALES", 7, "周销售", 7, "销售部", (7,), ("销售部",), ())
ENG = UserContext("eng01", "ENGINEER", 9, "研工", 9, "研发中心", (9,), ("研发中心",), ())


def guard(sql, ctx):
    return sqlguard.guard(sql, ctx, dry_run=False)


# ---------- 时间解析 ----------

@pytest.mark.parametrize("text,start,end", [
    ("上个月", "2026-09-01", "2026-09-30"), ("上上个月", "2026-08-01", "2026-08-31"), ("9月", "2026-09-01", "2026-09-30"),
    ("十二月", "2025-12-01", "2025-12-31"), ("2026年8月", "2026-08-01", "2026-08-31"), ("2026-08", "2026-08-01", "2026-08-31"),
    ("三季度", "2026-07-01", "2026-09-30"), ("2025年Q4", "2025-10-01", "2025-12-31"), ("上季度", "2026-07-01", "2026-09-30"),
    ("去年", "2025-01-01", "2025-12-31"), ("今年", "2026-01-01", "2026-10-06"), ("上半年", "2026-01-01", "2026-06-30"),
    ("最近三个月", "2026-07-01", "2026-09-30"), ("近半年", "2026-04-01", "2026-09-30"), ("1到6月", "2026-01-01", "2026-06-30"),
    ("2025年10月到2026年3月", "2025-10-01", "2026-03-31"), ("去年12月", "2025-12-01", "2025-12-31"),
    ("2025年12月底", "2025-12-01", "2025-12-31"), ("去年年底", "2025-01-01", "2025-12-31"), ("上个月底", "2026-09-01", "2026-09-30"),
])
def test_parse_period(text, start, end):
    p = parse_period(text, T)
    assert p and (p.start.isoformat(), p.end.isoformat()) == (start, end)


def test_parse_period_unknown_returns_none():
    assert parse_period("很久以前", T) is None and parse_period("", T) is None


def test_compare_periods():
    q3 = parse_period("三季度", T)
    assert previous(q3).start_month == "2026-04" and previous(q3).end.isoformat() == "2026-06-30"
    assert same_period_last_year(q3).start.isoformat() == "2025-07-01"
    assert previous(parse_period("上个月", T)).label == "2026-08"


# ---------- 维度归一化 ----------

@pytest.mark.parametrize("dim,value,expected", [
    ("产线", "伺服线", "L5"), ("产线", "L4", "L4"), ("产线", "步进", "L4"), ("产线", "冲压线", "L8"), ("产品系列", "伺服电机", "SERVO"),
    ("用工类型", "劳务派遣", "DISPATCH"), ("用工类型", "派遣工", "DISPATCH"), ("计薪方式", "计件", "PIECE"), ("内外销", "出口", 0),
    ("事业部", "智能驱控", "智能驱控事业部"), ("班组", "A班", "A"), ("产线", "火星线", None),
])
def test_normalize(dim, value, expected):
    assert labels.normalize(dim, value) == expected


def test_display_translates_codes():
    assert labels.display("产线", "L5") == "L5 伺服" and labels.display("用工类型", "DISPATCH") == "劳务派遣"
    assert labels.display("部门", "生产部") == "生产部"


# ---------- 权限 ----------

def test_access_levels():
    att, pay = semantic.views["v_attendance"], semantic.views["v_payslip"]
    assert access_level(att, "HR") == "all" and access_level(att, "PRODUCTION") == "dept" and access_level(att, "SALES") == "self"
    assert access_level(pay, "FINANCE") == "all" and access_level(pay, "ENGINEER") == "self"
    assert access_level(semantic.views["v_payroll"], "SALES") is None


def test_hidden_columns_by_role():
    fact = semantic.views["v_sales_fact"]
    assert set(hidden_columns(fact, "SALES")) == {"cost_cny", "gross_profit_cny"} and not hidden_columns(fact, "FINANCE")
    assert "base_salary" in hidden_columns(semantic.views["v_employee"], "PRODUCTION")


def test_row_filter_dept_and_self():
    cond, note = row_filter(semantic.views["v_attendance"], PROD)
    assert cond.sql() == "dept_id IN (11, 12, 13)" and "生产部" in note
    cond, note = row_filter(semantic.views["v_payslip"], ENG)
    assert cond.sql() == "employee_id = 9" and "仅本人" in note
    cond, _ = row_filter(semantic.views["v_wo_report"], PROD)
    assert cond.sql() == "line_code IN ('L1', 'L5')"
    assert row_filter(semantic.views["v_attendance"], HR) == (None, None)


def test_user_without_employee_sees_nothing():
    cond, _ = row_filter(semantic.views["v_payslip"], UserContext("ghost", "ENGINEER"))
    assert cond.sql().upper() == "FALSE"


def test_secured_source_drops_hidden_columns():
    src, _, _ = secured_source(semantic.views["v_sales_fact"], SALES)
    sql = src.sql(dialect="mysql")
    assert "cost_cny" not in sql and "gross_profit_cny" not in sql and "revenue_cny" in sql
    assert secured_source(semantic.views["v_sales_fact"], GM)[0] is None        # 没有任何限制时直接查原视图


def test_whitelist_matches_database_grants():
    """YAML 白名单和数据库授权脚本必须一致，否则要么查不了，要么授权多了"""
    init = Path(__file__).parents[1] / "mysql-init"
    sql = (init / "02_copilot.sql").read_text(encoding="utf-8") + (init / "03_employee_pii.sql").read_text(encoding="utf-8")
    granted = set(re.findall(r"GRANT SELECT ON minierp\.(\w+)", sql))
    assert granted == set(semantic.views)


# ---------- SQL 校验与改写 ----------

@pytest.mark.parametrize("sql,code", [
    ("DELETE FROM v_employee", "NOT_SELECT"), ("UPDATE v_payslip SET gross_pay = 1", "NOT_SELECT"),
    ("DROP TABLE v_employee", "NOT_SELECT"), ("SELECT 1; SELECT 2", "MULTI"), ("SELECT * FROM hr_employee", "TABLE"),
    ("SELECT * FROM sys_user", "TABLE"), ("SELECT * FROM information_schema.tables", "SYSTEM_SCHEMA"),
    ("SELECT * FROM mysql.user", "SYSTEM_SCHEMA"), ("SELECT SLEEP(5)", "FUNCTION"), ("SELECT LOAD_FILE('/etc/passwd')", "FUNCTION"),
    ("SELECT @@version", "VARIABLE"), ("SELECT name FROM v_employee FOR UPDATE", "NOT_SELECT"),
    ("SELECT name FROM v_employee WHERE id IN (SELECT employee_id FROM hr_payslip)", "TABLE"),
    ("SELEC name FROM v_employee", "SYNTAX"),
])
def test_guard_rejects(sql, code):
    with pytest.raises(sqlguard.SqlRejected) as e:
        guard(sql, GM)
    assert e.value.code == code


def test_guard_rejects_into_outfile():
    with pytest.raises(sqlguard.SqlRejected):
        guard("SELECT name FROM v_employee INTO OUTFILE '/tmp/x'", GM)


def test_guard_table_denied_for_role():
    with pytest.raises(sqlguard.SqlRejected) as e:
        guard("SELECT * FROM v_payroll", SALES)
    assert e.value.code == "TABLE_DENIED" and not e.value.repairable


def test_guard_sensitive_column_denied():
    with pytest.raises(sqlguard.SqlRejected) as e:
        guard("SELECT series, SUM(gross_profit_cny) FROM v_sales_fact GROUP BY series", SALES)
    assert e.value.code == "COLUMN_DENIED" and "毛利" in e.value.message
    with pytest.raises(sqlguard.SqlRejected):
        guard("SELECT name, base_salary FROM v_employee", PROD)


def test_guard_star_cannot_leak_hidden_columns():
    g = guard("SELECT * FROM v_sales_fact", SALES)
    assert "cost_cny" not in g.sql and "gross_profit_cny" not in g.sql


def test_guard_injects_row_filter_inside_subquery():
    g = guard("SELECT dept_name, COUNT(*) FROM v_attendance a WHERE a.work_date >= '2026-09-01' GROUP BY dept_name", PROD)
    assert "dept_id IN (11, 12, 13)" in g.sql and ") AS a" in g.sql and g.scope_notes
    g = guard("SELECT SUM(gross_pay) FROM v_payslip", ENG)
    assert "employee_id = 9" in g.sql


def test_row_filter_survives_or_condition():
    """行级条件加在子查询里，用户写 OR 1=1 也绕不过去"""
    g = guard("SELECT COUNT(*) FROM v_payslip WHERE period = '2026-08' OR 1 = 1", ENG)
    tree = sqlglot.parse_one(g.sql, read="mysql")
    inner = tree.find(sqlglot.exp.Subquery)
    assert inner is not None and "employee_id = 9" in inner.sql()


def test_guard_forces_limit():
    assert guard("SELECT name FROM v_employee", GM).sql.endswith("LIMIT 1000")
    assert guard("SELECT name FROM v_employee LIMIT 99999", GM).sql.endswith("LIMIT 1000")
    assert guard("SELECT name FROM v_employee LIMIT 5", GM).sql.endswith("LIMIT 5")


def test_guard_allows_cte_and_union():
    g = guard("WITH t AS (SELECT dept_name, COUNT(*) AS n FROM v_employee GROUP BY 1) SELECT * FROM t UNION ALL SELECT '合计', SUM(n) FROM t", GM)
    assert g.views == ["v_employee"]


# ---------- 归因 ----------

def test_pvm_effects_sum_to_total():
    base = {"A": (100, 1000.0, 700.0), "B": (50, 1000.0, 600.0), "C": (10, 100.0, 90.0)}
    cur = {"A": (120, 1140.0, 900.0), "B": (40, 840.0, 500.0), "D": (30, 600.0, 450.0)}       # C 停售，D 是新产品
    t = analysis.pvm(base, cur)["total"]
    assert t["delta"] == pytest.approx((1140 - 900 + 840 - 500 + 600 - 450) - (300 + 400 + 10))
    assert t["price"] + t["cost"] + t["volume"] + t["mix"] == pytest.approx(t["delta"])


def test_pvm_pure_price_change():
    t = analysis.pvm({"A": (100, 1000.0, 600.0)}, {"A": (100, 1100.0, 600.0)})["total"]
    assert t["price"] == pytest.approx(100) and t["cost"] == pytest.approx(0) and t["volume"] == pytest.approx(0) and t["mix"] == pytest.approx(0)


def test_pvm_pure_cost_change():
    t = analysis.pvm({"A": (100, 1000.0, 600.0)}, {"A": (100, 1000.0, 650.0)})["total"]
    assert t["cost"] == pytest.approx(-50) and t["price"] == pytest.approx(0)


# ---------- 路由规则、追问合并、工具参数 ----------

@pytest.mark.parametrize("q,tool", [
    ("迟到多久算旷工？", "policy_question"), ("年假有几天", "policy_question"), ("报销超过 5000 元要谁审批", "policy_question"),
    ("上个月迟到多少人次", None), ("各部门出勤率", None), ("把所有人的工资改成一万", "refuse_write"), ("删除张三的考勤记录", "refuse_write"),
    ("帮我审批通过这张请假单", "refuse_write"), ("如果把计件单价调到 4 元会怎样", None), ("新话题", "reset"),
])
def test_rule_route(q, tool):
    call = router.rule_route(q, {})
    assert (call["tool"] if call else None) == tool


def test_chart_switch_needs_previous_result():
    assert router.rule_route("换成饼图", {}) is None
    assert router.rule_route("换成饼图", {"last_result_id": "x"})["args"]["chart"] == "pie"
    assert router.rule_route("换成按季度看", {"last_result_id": "x"}) is None


def test_merge_followup_inherits_period_and_overrides_filter():
    last = {"tool": "run_template", "args": {"template_id": "T07", "filters": {"产线": "L5"}, "period": "上个月", "group_by": []}}
    call = {"tool": "run_template", "args": {"template_id": "T07", "filters": {"产线": "L4"}, "period": None, "group_by": [], "inherit": True}}
    merged, _ = router.merge_followup(call, last)
    assert merged["args"]["period"] == "上个月" and merged["args"]["filters"] == {"产线": "L4"}


def test_merge_followup_drops_filter_unsupported_by_new_template():
    last = {"tool": "run_template", "args": {"template_id": "T19", "filters": {"区域": "华东"}, "period": "三季度", "group_by": ["月份"]}}
    call = {"tool": "run_template", "args": {"template_id": "T07", "filters": {}, "period": None, "group_by": [], "inherit": True}}
    merged, notes = router.merge_followup(call, last)
    assert merged["args"]["filters"] == {} and notes and merged["args"]["period"] == "三季度"


def test_no_inherit_without_flag():
    last = {"tool": "run_template", "args": {"template_id": "T07", "filters": {"产线": "L5"}, "period": "上个月"}}
    call = {"tool": "run_template", "args": {"template_id": "T06", "filters": {}, "period": None, "inherit": False}}
    assert router.merge_followup(call, last)[0]["args"]["period"] is None


def test_tool_arguments_are_validated():
    with pytest.raises(Exception):
        TOOL_MAP["run_template"](template_id="加班率")              # 模板编号格式不对
    with pytest.raises(Exception):
        TOOL_MAP["what_if"](scenario="汇率", change_pct=5)          # 不支持的场景
    with pytest.raises(Exception):
        TOOL_MAP["what_if"](scenario="计件单价", change_pct=500)     # 幅度超出范围
    with pytest.raises(Exception):
        SqlDraft(sql="")
    assert TOOL_MAP["run_template"](template_id="T07", filters={"产线": "L5"}).group_by == []


# ---------- 语义层自检 ----------

def test_semantic_layer_is_consistent():
    assert len(semantic.templates) == 24
    names = {m.name for m in semantic.metrics}
    for m in semantic.metrics:
        assert set(m.depends_on) <= names, f"{m.name} 依赖了不存在的指标"
        assert set(m.templates) <= set(semantic.templates), f"{m.name} 引用了不存在的模板"
        assert m.view is None or m.view in semantic.views
    for t in semantic.templates.values():
        assert set(t.metrics) <= names, f"{t.id} 引用了口径库里没有的指标"
        assert set(t.fixed) <= set(t.dims) and set(t.default_group) <= set(t.dims)
        assert t.examples and t.time in ("range", "asof", "none")
        assert all(v in semantic.views for v in re.findall(r"\b(v_\w+)\b", t.sql)), f"{t.id} 用了白名单以外的视图"


# ---------- 结果整理、图表、制度切片 ----------

def test_result_types_and_one_line():
    r = result.build(["产线", "加班工时", "加班率", "人工成本", "人数"], [{"产线": "L5", "加班工时": 1022.0, "加班率": 0.1589, "人工成本": 345678.9, "人数": 46.0}])
    assert [c["type"] for c in r["columns"]] == ["text", "int", "pct", "money", "int"]
    assert r["rows"][0]["产线"] == "L5 伺服" and r["rows"][0]["人数"] == 46
    line = result.one_line("加班率（2026-09）", r)
    assert "15.89%" in line and "L5 伺服" in line
    assert result.one_line("x", {"columns": r["columns"], "rows": r["rows"] * 2}) is None


def test_null_dimension_is_labelled():
    r = result.build(["产线", "人数"], [{"产线": None, "人数": 97}, {"产线": "L1", "人数": 85}])
    assert r["rows"][0]["产线"] == "未分配"


def test_chart_selection():
    by_month = result.build(["月份", "离职率"], [{"月份": f"2026-0{i}", "离职率": 0.01 * i} for i in range(1, 6)])
    assert chart.build(by_month, hint="bar")["type"] == "line"
    by_line = result.build(["产线", "加班工时", "加班率"], [{"产线": f"L{i}", "加班工时": 100.0 * i, "加班率": 0.1} for i in range(1, 5)])
    c = chart.build(by_line, hint="bar")
    assert c["type"] == "bar" and [s["axis"] for s in c["series"]] == [0, 1]
    assert chart.build(by_line, hint="pie")["type"] == "pie"
    assert chart.build(result.build(["人数"], [{"人数": 450}])) is None
    two_dims = result.build(["月份", "产线", "良率"], [{"月份": m, "产线": l, "良率": 0.9} for m in ("2026-08", "2026-09") for l in ("L1", "L5")])
    assert len(chart.build(two_dims)["series"]) == 2


def test_policy_split_keeps_title_prefix():
    md = "# 考勤制度\n\n> 版本：2.0\n\n## 第一章 工时\n- 每天 8 小时\n\n## 第二章 迟到\n- 超过 30 分钟按旷工半天\n"
    chunks = split_sections(md, "考勤制度")
    assert [c["section"] for c in chunks] == ["文档信息", "第一章 工时", "第二章 迟到"]
    assert all(c["content"].startswith("【考勤制度】") and c["version"] == "2.0" for c in chunks)


def test_self_only_scope_is_reported():
    """只能看本人工资条的人问"各部门人均工资"，校验结果里要能看出数据被限定成了仅本人，由流程决定拒绝"""
    g = guard("SELECT dept_name, AVG(gross_pay) FROM v_payslip GROUP BY dept_name", ENG)
    assert g.self_only == ["工资条"]
    assert guard("SELECT dept_name, AVG(gross_pay) FROM v_payslip GROUP BY dept_name", HR).self_only == []
    assert guard("SELECT COUNT(*) FROM v_attendance", PROD).self_only == []          # 部门范围不算"仅本人"
