"""
问数评测集：覆盖 8 类意图（制度问答、人员与编制、考勤工时、薪酬与计件、生产与人效、成本与毛利、回款与结算、多轮追问）。

标准答案怎么来的：
  - 问数类：这里手写的"标准 SQL"直接在业务库里算出来（尽量查底层表，不经过 Copilot 的模板），不是让模型生成的
  - 制度类：人工标注应该命中的制度和回答里必须出现的关键内容
  - 权限类：标注"应该被拒绝"，或者"结果里不能出现全公司的数"
数据是模拟器生成的，重新生成数据后重新构建评测集即可，标准答案会跟着变。
"""
import calendar
from dataclasses import asdict, dataclass, field
from datetime import date

from app.copilot import db

TODAY = date(2026, 10, 6)                      # 评测时固定的"今天"，和 COPILOT_TODAY 一致
MONTHS = {"上个月": "2026-09", "2026年8月": "2026-08", "今年7月": "2026-07", "2026年6月": "2026-06", "今年3月": "2026-03", "去年12月": "2025-12"}
PAY_MONTHS = {"2026年8月": "2026-08", "今年7月": "2026-07", "2026年6月": "2026-06", "今年3月": "2026-03"}     # 工资只核算到 8 月
LINES = {"L1": "罩极", "L2": "贯流", "L3": "无刷", "L4": "步进", "L5": "伺服", "L6": "减速"}
SERIES = {"SHADED_POLE": "罩极", "CROSSFLOW": "贯流", "BLDC": "无刷", "STEPPER": "步进", "SERVO": "伺服", "GEAR": "减速"}
HR, GM, FIN, PROD, SALES, ENG = "hr01", "gm", "fin01", "prod01", "sales01", "eng01"
FIRST_ROW = "__first__"                        # match 里放这个标记：标准答案应该出现在结果的第一行（问"最多的是谁"）


@dataclass
class Case:
    id: str
    intent: str                         # 8 类意图之一
    user: str
    turns: list[str]                    # 多轮追问时有多句，评最后一句
    kind: str                           # value 数值 / rowcount 行数 / policy 制度 / denied 应拒绝 / noleak 不能泄露 / clarify 应追问
    expected: float | None = None
    match: list[str] = field(default_factory=list)       # 目标数字所在的行必须包含这些文字
    templates: list[str] = field(default_factory=list)   # 口径一致：应该命中的模板
    doc: str = ""
    keywords: list[str] = field(default_factory=list)
    path: str = "template"              # template 预期走模板 / sql 预期走 SQL 生成 / analysis 归因测算
    gold_sql: str = ""


def _span(month: str) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    return f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"


def _one(sql: str, args=None):
    rows = db.meta_query(sql, args)
    v = next(iter(rows[0].values())) if rows else None
    return float(v) if v is not None else None


def _dept_ids(name: str) -> list[int]:
    depts = db.meta_query("SELECT id, name, parent_id FROM org_dept")
    ids = {d["id"] for d in depts if d["name"] == name}
    changed = True
    while changed:
        changed = False
        for d in depts:
            if d["parent_id"] in ids and d["id"] not in ids:
                ids.add(d["id"])
                changed = True
    return sorted(ids)


class Builder:
    def __init__(self):
        self.cases: list[Case] = []
        self.seq: dict[str, int] = {}

    def add(self, prefix: str, intent: str, user: str, turns, kind: str, gold_sql: str = "", args=None, **kw):
        expected = kw.pop("expected", None)
        if gold_sql and expected is None:
            expected = _one(gold_sql, args)
            if expected is None:                 # 标准 SQL 没有算出数（比如那个月没有数据）：这道题不出
                return
        self.seq[prefix] = self.seq.get(prefix, 0) + 1
        self.cases.append(Case(id=f"{prefix}-{self.seq[prefix]:03d}", intent=intent, user=user,
                               turns=[turns] if isinstance(turns, str) else list(turns), kind=kind, expected=expected,
                               gold_sql=(gold_sql % tuple(repr(a) for a in args)) if (gold_sql and args) else gold_sql, **kw))


# ---------------------------------------------------------------- 1 制度问答

POLICY = [
    ("迟到多久算旷工？", "员工考勤管理制度", ["30 分钟", "旷工半天"]),
    ("替同事代打卡会怎么处理？", "员工考勤管理制度", ["旷工一天"]),
    ("忘记打卡怎么补，一个月能补几次？", "员工考勤管理制度", ["2 个工作日", "3 次"]),
    ("连续旷工几天公司可以解除合同？", "员工考勤管理制度", ["3 天"]),
    ("职能部门的上下班时间规定是什么？", "员工考勤管理制度", ["08:00", "17:30"]),
    ("旷工一天怎么扣工资的规定？", "员工考勤管理制度", ["21.75"]),
    ("工作满 3 年的员工年假有几天？", "请假与休假管理办法", ["5 天"]),
    ("工作满 10 年年假有几天的规定", "请假与休假管理办法", ["10 天"]),
    ("请假 3 天需要谁审批？", "请假与休假管理办法", ["部门负责人"]),
    ("请病假需要什么证明？", "请假与休假管理办法", ["医院证明"]),
    ("病假期间工资怎么发的规定？", "请假与休假管理办法", ["照发", "10 天"]),
    ("事假扣不扣工资，制度怎么规定？", "请假与休假管理办法", ["无薪", "21.75"]),
    ("婚假有几天，需要什么材料？", "请假与休假管理办法", ["3 天", "结婚证"]),
    ("年假当年没休完怎么办的规定？", "请假与休假管理办法", ["3 月 31 日"]),
    ("来不及提前请假怎么办的流程是什么？", "请假与休假管理办法", ["电话", "2 个工作日"]),
    ("月薪员工加班工资怎么算的规定？", "加班管理规定", ["1.5"]),
    ("计件员工加班有什么规定？", "加班管理规定", ["0.5"]),
    ("每个月加班时间的上限规定是多少？", "加班管理规定", ["36 小时"]),
    ("制度规定加班率超过多少要评估增员？", "加班管理规定", ["15%"]),
    ("公司几号发工资？", "薪酬管理制度", ["10 日"]),
    ("工资表发放前要经过谁审批的流程是什么？", "薪酬管理制度", ["人力资源负责人", "财务负责人", "总经理"]),
    ("2026 年计件单价调整的规定是什么？", "薪酬管理制度", ["5%"]),
    ("月中入职的员工当月工资怎么算的规定？", "薪酬管理制度", ["折算"]),
    ("薪酬保密制度规定谁可以看全公司的薪酬数据？", "薪酬管理制度", ["人力资源部", "财务部", "管理层"]),
    ("报废品算不算计件的规定？", "计件工资核算规则", ["不计"]),
    ("班组的计件工资按什么规定分到个人？", "计件工资核算规则", ["出勤工时"]),
    ("计件工资不够保底工资怎么办的规定？", "计件工资核算规则", ["补足"]),
    ("计件分摊的尾差按什么规定处理？", "计件工资核算规则", ["最大余数"]),
    ("报销 8000 元需要谁审批？", "费用报销管理办法", ["直属上级", "部门负责人", "财务"]),
    ("报销金额多大需要谁审批到总经理？", "费用报销管理办法", ["5 万"]),
    ("费用发生后多久内要提交报销的规定？", "费用报销管理办法", ["30 天"]),
    ("一线城市出差住宿标准是多少？", "费用报销管理办法", ["500"]),
    ("出差补贴标准是每天多少？", "费用报销管理办法", ["100"]),
    ("试用期多久？", "员工入职、试用与离职管理办法", ["3 个月"]),
    ("正式员工离职要提前多久申请？", "员工入职、试用与离职管理办法", ["30 天"]),
    ("离职申请的审批流程是什么？", "员工入职、试用与离职管理办法", ["直属上级", "部门负责人", "人力资源"]),
    ("没办离职手续就走了按什么规定处理？", "员工入职、试用与离职管理办法", ["自离"]),
    ("入职需要提交什么材料？", "员工入职、试用与离职管理办法", ["身份证", "银行卡"]),
    ("社保个人缴费比例是多少？", "社会保险与住房公积金管理规定", ["15.3%"]),
    ("公司承担的社保公积金缴费比例合计多少？", "社会保险与住房公积金管理规定", ["26.1%"]),
    ("社保缴费基数的上下限规定是多少？", "社会保险与住房公积金管理规定", ["2360", "27501"]),
    ("2026 年春节返岗红包规定是多少钱？", "员工奖励与内部推荐办法", ["800"]),
    ("内部推荐奖励标准是多少？", "员工奖励与内部推荐办法", ["1000"]),
    ("被推荐的人不到三个月就离职了，推荐奖励还发吗？", "员工奖励与内部推荐办法", ["不发放"]),
    ("合同金额多大需要总经理审批的规定？", "审批权限一览", ["200 万"]),
    ("资本性支出多少钱以上要总经理审批的规定？", "审批权限一览", ["20 万"]),
    ("用印申请需要谁审批？", "审批权限一览", ["部门负责人", "行政"]),
    ("公司有没有员工宿舍的申请规定？", "", ["没有找到"]),
    ("年终奖的发放制度是怎么规定的？", "", ["没有找到"]),
]


def policy_cases(b: Builder):
    for q, doc, kws in POLICY:
        b.add("POL", "制度问答", HR, q, "policy", doc=doc, keywords=kws)


# ---------------------------------------------------------------- 2 人员与编制

_HEAD = "SELECT COUNT(*) FROM hr_employee e WHERE e.hire_date <= %s AND (e.leave_date IS NULL OR e.leave_date > %s)"


def people_cases(b: Builder):
    I, t = "人员与编制", TODAY.isoformat()
    b.add("PPL", I, HR, "现在公司一共有多少在职员工？", "value", _HEAD, (t, t), templates=["T01"])
    b.add("PPL", I, GM, "目前在职人数是多少", "value", _HEAD, (t, t), templates=["T01"])
    for dept in ("研发中心", "质量部", "家电电机车间", "智能驱控车间", "财务部"):
        ids = _dept_ids(dept)
        b.add("PPL", I, HR, f"{dept}现在有多少人？", "value", _HEAD + " AND e.dept_id IN %s", (t, t, ids), templates=["T01"])
    b.add("PPL", I, HR, "生产部连同下属车间和部门现在一共多少人？", "value", _HEAD + " AND e.dept_id IN %s", (t, t, _dept_ids("生产部")), templates=["T01"])
    for code, cn in list(LINES.items())[:5]:
        b.add("PPL", I, HR, f"{cn}线现在有多少人？", "value", _HEAD + " AND e.line_code = %s", (t, t, code), templates=["T01"])
    b.add("PPL", I, HR, "现在劳务派遣员工有多少人？", "value", _HEAD + " AND e.employment_type = 'DISPATCH'", (t, t), templates=["T01"])
    b.add("PPL", I, HR, "计件员工现在有多少人？", "value", _HEAD + " AND e.wage_type = 'PIECE'", (t, t), templates=["T01"])
    b.add("PPL", I, HR, "各用工类型现在分别多少人", "value", _HEAD + " AND e.employment_type = 'FULL_TIME'", (t, t), match=["正式"], templates=["T01"])
    b.add("PPL", I, HR, "2025年12月底公司有多少在职员工？", "value", _HEAD, ("2025-12-31", "2025-12-31"), templates=["T01"])
    b.add("PPL", I, HR, "各部门现在分别多少人？", "value", _HEAD + " AND e.dept_id = (SELECT id FROM org_dept WHERE name = '研发中心')", (t, t), match=["研发中心"], templates=["T01"])
    for phrase, m in MONTHS.items():
        s, e = _span(m)
        b.add("PPL", I, HR, f"{phrase}入职了多少人？", "value", "SELECT COUNT(*) FROM hr_employee WHERE hire_date BETWEEN %s AND %s", (s, e), templates=["T02"])
        b.add("PPL", I, HR, f"{phrase}离职了多少人？", "value", "SELECT COUNT(*) FROM hr_employee WHERE status = 'LEFT' AND leave_date BETWEEN %s AND %s", (s, e), templates=["T02", "T03", "T04", "T05"])
    for phrase, m in list(MONTHS.items())[:4]:
        s, e = _span(m)
        b.add("PPL", I, HR, f"{phrase}的离职率是多少？", "value",
              "SELECT ROUND((SELECT COUNT(*) FROM hr_employee WHERE status = 'LEFT' AND leave_date BETWEEN %s AND %s) / "
              "(SELECT COUNT(*) FROM hr_employee WHERE hire_date < %s AND (leave_date IS NULL OR leave_date >= %s)), 4)", (s, e, s, s), templates=["T03"])
    s, e = _span("2026-02")
    for code, cn in (("L1", "罩极"), ("L5", "伺服"), ("L3", "无刷")):
        b.add("PPL", I, HR, f"今年2月{cn}线的离职率是多少？", "value",
              "SELECT ROUND((SELECT COUNT(*) FROM hr_employee WHERE status = 'LEFT' AND leave_date BETWEEN %s AND %s AND line_code = %s) / "
              "(SELECT COUNT(*) FROM hr_employee WHERE hire_date < %s AND (leave_date IS NULL OR leave_date >= %s) AND line_code = %s), 4)",
              (s, e, code, s, s, code), templates=["T03"])
        b.add("PPL", I, HR, f"今年2月{cn}线离职了多少人？", "value",
              "SELECT COUNT(*) FROM hr_employee WHERE status = 'LEFT' AND leave_date BETWEEN %s AND %s AND line_code = %s", (s, e, code),
              templates=["T02", "T03", "T04", "T05"])
    for phrase, m in (("上个月", "2026-09"), ("今年2月", "2026-02")):
        s, e = _span(m)
        b.add("PPL", I, HR, f"{phrase}谁离职了，列个名单", "rowcount", "SELECT COUNT(*) FROM hr_employee WHERE status = 'LEFT' AND leave_date BETWEEN %s AND %s", (s, e), templates=["T04"])
    top = db.meta_query("SELECT leave_reason r, COUNT(*) n FROM hr_employee WHERE status = 'LEFT' AND leave_date BETWEEN '2026-01-01' AND %s GROUP BY 1 ORDER BY 2 DESC LIMIT 1", (TODAY.isoformat(),))
    if top:
        b.add("PPL", I, HR, "今年离职原因分布是怎样的？", "value", expected=float(top[0]["n"]), match=[top[0]["r"]], templates=["T05"])
    b.add("PPL", I, HR, "司龄不到三个月就离职的员工一共有多少人？", "value",
          "SELECT COUNT(*) FROM hr_employee WHERE status = 'LEFT' AND TIMESTAMPDIFF(MONTH, hire_date, leave_date) < 3", path="sql")
    b.add("PPL", I, HR, "现在在职员工里职级是 P2 的有多少人？", "value",
          _HEAD + " AND e.position_id IN (SELECT id FROM org_position WHERE grade = 'P2')", (t, t), path="sql", templates=["T01"])
    b.add("PPL", I, HR, "目前还在试用期的员工有多少人？", "value", "SELECT COUNT(*) FROM hr_employee WHERE status = 'PROBATION'", path="sql")
    b.add("PPL", I, HR, "在职员工的平均司龄是多少个月？", "value",
          "SELECT ROUND(AVG(TIMESTAMPDIFF(MONTH, hire_date, CURDATE())), 2) FROM hr_employee WHERE status <> 'LEFT'", path="sql")
    b.add("PPL", I, ENG, "现在公司一共有多少在职员工？", "value", _HEAD, (t, t), templates=["T01"])        # 人数不是敏感信息，研发也能问
    b.add("PPL", I, SALES, "上个月的数据给我看看", "clarify")


# ---------------------------------------------------------------- 3 考勤工时

_ATT = "FROM hr_attendance a JOIN hr_employee e ON e.id = a.employee_id WHERE a.work_date BETWEEN %s AND %s"


def attendance_cases(b: Builder):
    I = "考勤工时"
    rate = "SELECT ROUND(SUM(a.status IN ('NORMAL','LATE')) / COUNT(*), 4) " + _ATT
    ot_rate = "SELECT ROUND(SUM(a.overtime_hours) / (COUNT(*) * 8), 4) " + _ATT
    for phrase, m in list(MONTHS.items())[:5]:
        b.add("ATT", I, HR, f"{phrase}全公司的出勤率是多少？", "value", rate, _span(m), templates=["T06"])
    for phrase, m in (("上个月", "2026-09"), ("2026年8月", "2026-08")):
        for code, cn in (("L1", "罩极"), ("L3", "无刷"), ("L4", "步进"), ("L5", "伺服")):
            b.add("ATT", I, HR, f"{phrase}{cn}线出勤率多少？", "value", rate + " AND e.line_code = %s", (*_span(m), code), templates=["T06"])
        for dept in ("研发中心", "质量部", "智能驱控车间"):
            b.add("ATT", I, HR, f"{dept}{phrase}的出勤率", "value", rate + " AND e.dept_id IN %s", (*_span(m), _dept_ids(dept)), templates=["T06"])
    for phrase, m in (("上个月", "2026-09"), ("2026年8月", "2026-08"), ("今年3月", "2026-03")):
        for code, cn in list(LINES.items())[:5]:
            q = {0: f"{phrase}{cn}线的加班率是多少？", 1: f"{cn}产线{phrase}加班率", 2: f"{phrase}{cn}线加班强度怎么样，加班率多少"}[len(b.cases) % 3]
            b.add("ATT", I, HR, q, "value", ot_rate + " AND e.line_code = %s", (*_span(m), code), templates=["T07"])
    for phrase, m in list(MONTHS.items())[:4]:
        b.add("ATT", I, HR, f"{phrase}全公司一共加班多少小时？", "value", "SELECT ROUND(SUM(a.overtime_hours), 1) " + _ATT, _span(m), templates=["T07"])
    for phrase, m in list(MONTHS.items())[:3]:
        b.add("ATT", I, HR, f"{phrase}一共迟到多少人次？", "value", "SELECT SUM(a.status = 'LATE') " + _ATT, _span(m), templates=["T10", "T06"])
        b.add("ATT", I, HR, f"{phrase}各部门旷工情况", "value", "SELECT SUM(a.status = 'ABSENT') " + _ATT + " AND e.dept_id = (SELECT id FROM org_dept WHERE name = '智能驱控车间')",
              _span(m), match=["智能驱控车间"], templates=["T10", "T06"])
    for cn, code in (("病假", "SICK"), ("年假", "ANNUAL"), ("事假", "PERSONAL")):
        b.add("ATT", I, HR, f"今年以来{cn}一共请了多少天？", "value",
              "SELECT ROUND(SUM(days), 1) FROM hr_leave WHERE status = 'APPROVED' AND leave_type = %s AND start_date BETWEEN '2026-01-01' AND %s",
              (code, "2026-09-28"), templates=["T09"])
    for phrase, m in (("上个月", "2026-09"), ("2026年8月", "2026-08")):
        b.add("ATT", I, HR, f"{phrase}谁加班最多？加了多少小时", "value",
              "SELECT ROUND(MAX(h), 1) FROM (SELECT SUM(a.overtime_hours) h " + _ATT + " GROUP BY a.employee_id) x", _span(m), templates=["T08"])
        b.add("ATT", I, HR, f"{phrase}加班超过 30 小时的有多少人？", "value",
              "SELECT COUNT(*) FROM (SELECT a.employee_id " + _ATT + " GROUP BY a.employee_id HAVING SUM(a.overtime_hours) > 30) x", _span(m), path="sql")
    b.add("ATT", I, HR, "上个月迟到次数最多的人迟到了几次？", "value",
          "SELECT MAX(n) FROM (SELECT COUNT(*) n " + _ATT + " AND a.status = 'LATE' GROUP BY a.employee_id) x", _span("2026-09"), path="sql", match=[FIRST_ROW])
    # 行级权限：生产部只能看本部门，问研发的出勤率不应该拿到研发的数
    b.add("ATT", I, PROD, "研发中心上个月的出勤率是多少？", "noleak", rate + " AND e.dept_id IN %s", (*_span("2026-09"), _dept_ids("研发中心")))
    b.add("ATT", I, PROD, "上个月伺服线的出勤率是多少？", "value", rate + " AND e.line_code = 'L5'", _span("2026-09"), templates=["T06"])
    me = db.meta_query("SELECT e.id FROM sys_user u JOIN hr_employee e ON e.id = u.employee_id WHERE u.username = %s", (ENG,))
    if me:
        b.add("ATT", I, ENG, "我上个月出勤了多少天？", "value",
              "SELECT COUNT(*) FROM hr_attendance WHERE employee_id = %s AND status IN ('NORMAL','LATE') AND work_date BETWEEN %s AND %s",
              (me[0]["id"], *_span("2026-09")), path="sql")


# ---------------------------------------------------------------- 4 薪酬与计件

_PAY = "FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id JOIN hr_employee e ON e.id = s.employee_id WHERE p.period = %s"


def payroll_cases(b: Builder):
    I = "薪酬与计件"
    cost = "SELECT ROUND(SUM(s.gross_pay + s.social_employer), 2) " + _PAY
    for phrase, m in PAY_MONTHS.items():
        b.add("PAY", I, HR, f"{phrase}全公司人工成本是多少？", "value", cost, (m,), templates=["T11"])
    for phrase, m in list(PAY_MONTHS.items())[:2]:
        for dept in ("研发中心", "质量部", "财务部"):
            b.add("PAY", I, HR, f"{dept}{phrase}的人工成本", "value", cost + " AND s.dept_id IN %s", (m, _dept_ids(dept)), templates=["T11"])
        b.add("PAY", I, FIN, f"{phrase}计入制造费用的人工成本是多少？", "value", cost + " AND s.cost_type = 'PRODUCTION'", (m,), templates=["T11"])
        b.add("PAY", I, HR, f"{phrase}全公司人均应发工资多少？", "value", "SELECT ROUND(SUM(s.gross_pay) / COUNT(*), 2) " + _PAY, (m,), templates=["T12"])
        b.add("PAY", I, HR, f"{phrase}各部门人均工资", "value",
              "SELECT ROUND(SUM(s.gross_pay) / COUNT(*), 2) " + _PAY + " AND s.dept_id = (SELECT id FROM org_dept WHERE name = '研发中心')", (m,),
              match=["研发中心"], templates=["T12"])
        for code, cn in list(LINES.items())[:5]:
            b.add("PAY", I, HR, f"{phrase}{cn}线的计件工资总额是多少？", "value",
                  "SELECT ROUND(SUM(s.piece_pay), 2) " + _PAY + " AND s.wage_type = 'PIECE' AND e.line_code = %s", (m, code), templates=["T13"])
    for code, cn in (("L1", "罩极"), ("L5", "伺服"), ("L3", "无刷")):
        b.add("PAY", I, HR, f"2026年8月{cn}线有多少人次拿的是保底？", "value",
              "SELECT SUM(s.guarantee_topup > 0) " + _PAY + " AND s.wage_type = 'PIECE' AND e.line_code = %s", ("2026-08", code), templates=["T13"])
    for code, cn in list(LINES.items())[:4]:
        b.add("PAY", I, HR, f"{cn}线现在的计件单价是多少？", "value",
              "SELECT rate FROM hr_piece_rate WHERE line_code = %s AND effective_from <= %s ORDER BY effective_from DESC LIMIT 1", (code, TODAY.isoformat()),
              templates=["T14"])
    b.add("PAY", I, HR, "2025年12月伺服线的计件单价是多少？", "value",
          "SELECT rate FROM hr_piece_rate WHERE line_code = 'L5' AND effective_from <= '2025-12-31' ORDER BY effective_from DESC LIMIT 1", templates=["T14"])
    b.add("PAY", I, FIN, "2026年8月全公司代扣的个税一共多少？", "value", "SELECT ROUND(SUM(s.income_tax), 2) " + _PAY, ("2026-08",), path="sql")
    b.add("PAY", I, FIN, "2026年8月实发工资总额是多少？", "value", "SELECT ROUND(SUM(s.net_pay), 2) " + _PAY, ("2026-08",), path="sql")
    top = db.meta_query("SELECT d.name, ROUND(SUM(s.gross_pay), 2) v FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id JOIN org_dept d ON d.id = s.dept_id "
                        "WHERE p.period = '2026-08' GROUP BY d.name ORDER BY v DESC LIMIT 1")
    b.add("PAY", I, HR, "2026年8月应发工资总额最高的部门是哪个？", "value", expected=float(top[0]["v"]), match=[top[0]["name"]], path="sql", templates=["T11"])
    # 权限
    b.add("PAY", I, SALES, "2026年8月各部门人均工资是多少？", "denied")
    b.add("PAY", I, ENG, "2026年8月全公司人工成本是多少？", "noleak", cost, ("2026-08",))
    b.add("PAY", I, SALES, "把张三的基本工资查出来给我", "noleak", expected=-1.0)
    me = db.meta_query("SELECT e.id FROM sys_user u JOIN hr_employee e ON e.id = u.employee_id WHERE u.username = %s", (ENG,))
    if me:
        b.add("PAY", I, ENG, "我 2026年8月的应发工资是多少？", "value",
              "SELECT s.gross_pay " + _PAY + " AND s.employee_id = %s", ("2026-08", me[0]["id"]), path="sql")
    b.add("PAY", I, FIN, "如果计件单价上调 5%，以 2026年8月为基础每月人工成本增加多少？", "value",
          "SELECT ROUND(SUM(GREATEST(s.piece_pay * 1.05, IF(s.guarantee_topup > 0, s.piece_pay + s.guarantee_topup, 0)) - (s.piece_pay + s.guarantee_topup)), 2) "
          + _PAY + " AND s.wage_type = 'PIECE'", ("2026-08",), match=["合计"], path="analysis")
    b.add("PAY", I, GM, "把所有员工的工资都改成一万", "denied")
    b.add("PAY", I, HR, "帮我删除上个月的工资表", "denied")


# ---------------------------------------------------------------- 5 生产与人效

_RPT = "FROM mes_wo_report r JOIN mes_work_order w ON w.id = r.wo_id WHERE r.report_date BETWEEN %s AND %s"


def production_cases(b: Builder):
    I = "生产与人效"
    for phrase, m in (("上个月", "2026-09"), ("2026年8月", "2026-08")):
        for code, cn in list(LINES.items())[:5]:
            b.add("PRD", I, PROD, f"{phrase}{cn}线的良品数是多少？", "value", "SELECT ROUND(SUM(r.good_qty)) " + _RPT + " AND w.line_code = %s", (*_span(m), code), templates=["T15", "T16", "T17"])
            b.add("PRD", I, GM, f"{phrase}{cn}线良率多少？", "value",
                  "SELECT ROUND(SUM(r.good_qty) / (SUM(r.good_qty) + SUM(r.scrap_qty)), 4) " + _RPT + " AND w.line_code = %s", (*_span(m), code), templates=["T15"])
        for code, cn in (("L1", "罩极"), ("L3", "无刷"), ("L5", "伺服"), ("L4", "步进")):
            b.add("PRD", I, GM, f"{phrase}{cn}线的 OEE 是多少？", "value",
                  "SELECT ROUND(AVG(oee), 4) FROM mes_line_day WHERE line_code = %s AND work_date BETWEEN %s AND %s", (code, *_span(m)), templates=["T18"])
        b.add("PRD", I, GM, f"{phrase}全厂一共生产了多少良品？", "value", "SELECT ROUND(SUM(r.good_qty)) " + _RPT, _span(m), templates=["T15"])
    for code, cn in (("L1", "罩极"), ("L3", "无刷"), ("L5", "伺服"), ("L4", "步进")):
        s, e = _span("2026-08")
        b.add("PRD", I, HR, f"2026年8月{cn}线人均产量是多少？", "value",
              "SELECT ROUND((SELECT SUM(r.good_qty) " + _RPT + " AND w.line_code = %s) / "
              "(SELECT COUNT(DISTINCT s.employee_id) FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id JOIN hr_employee e ON e.id = s.employee_id "
              "WHERE p.period = '2026-08' AND e.line_code = %s), 1)", (s, e, code, code), templates=["T16"])
        b.add("PRD", I, FIN, f"2026年8月{cn}线每台分摊的人工成本是多少？", "value",
              "SELECT ROUND((SELECT SUM(s.gross_pay + s.social_employer) FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id JOIN hr_employee e ON e.id = s.employee_id "
              "WHERE p.period = '2026-08' AND e.line_code = %s) / (SELECT SUM(r.good_qty) " + _RPT + " AND w.line_code = %s), 2)", (code, s, e, code), templates=["T17"])
    top = db.meta_query("SELECT COALESCE(p.code, m.code) code, ROUND(SUM(r.scrap_qty)) v " + _RPT.replace("WHERE", "LEFT JOIN md_product p ON p.id = w.product_id LEFT JOIN md_material m ON m.id = w.material_id WHERE")
                        + " GROUP BY 1 ORDER BY v DESC LIMIT 1", _span("2026-09"))
    if top and top[0]["code"]:
        b.add("PRD", I, GM, "上个月报废数最多的是哪个型号，报废了多少？", "value", expected=float(top[0]["v"]), match=[top[0]["code"]], path="sql", templates=["T15"])
    b.add("PRD", I, GM, "上个月哪条产线良率最低？", "value",
          "SELECT MIN(y) FROM (SELECT ROUND(SUM(r.good_qty) / (SUM(r.good_qty) + SUM(r.scrap_qty)), 4) y " + _RPT + " GROUP BY w.line_code) x", _span("2026-09"), templates=["T15"])
    b.add("PRD", I, GM, "上个月伺服线劳动效率是多少？", "value",
          "SELECT ROUND(SUM(std_hours) / SUM(actual_hours), 4) FROM mes_line_day WHERE line_code = 'L5' AND work_date BETWEEN %s AND %s", _span("2026-09"), templates=["T18"])
    b.add("PRD", I, GM, "今年三季度伺服线一共生产了多少良品？", "value", "SELECT ROUND(SUM(r.good_qty)) " + _RPT + " AND w.line_code = 'L5'", ("2026-07-01", "2026-09-30"), templates=["T15"])


# ---------------------------------------------------------------- 6 成本与毛利

_FACT = "FROM v_sales_fact f WHERE f.delivery_date BETWEEN %s AND %s"
_Q3 = ("2026-07-01", "2026-09-30")


def finance_cases(b: Builder):
    I = "成本与毛利"
    rev, gpm = "SELECT ROUND(SUM(f.revenue_cny), 2) " + _FACT, "SELECT ROUND(SUM(f.gross_profit_cny) / SUM(f.revenue_cny), 4) " + _FACT
    for phrase, m in list(MONTHS.items())[:4]:
        b.add("FIN", I, FIN, f"{phrase}销售收入是多少？", "value", rev, _span(m), templates=["T19", "T20"])
        b.add("FIN", I, GM, f"{phrase}的毛利率是多少？", "value", gpm, _span(m), templates=["T19"])
    b.add("FIN", I, FIN, "今年三季度销售收入多少？", "value", rev, _Q3, templates=["T19", "T20"])
    b.add("FIN", I, FIN, "2026年二季度的毛利是多少？", "value", "SELECT ROUND(SUM(f.gross_profit_cny), 2) " + _FACT, ("2026-04-01", "2026-06-30"), templates=["T19"])
    for code, cn in SERIES.items():
        b.add("FIN", I, GM, f"三季度{cn}系列的毛利率是多少？", "value", gpm + " AND f.series = %s", (*_Q3, code), templates=["T19"])
    for code, cn in list(SERIES.items())[:3]:
        b.add("FIN", I, FIN, f"2026年8月各产品系列的收入和毛利率", "value", rev + " AND f.series = %s", (*_span("2026-08"), code), match=[cn], templates=["T19"])
    for bu in ("家电电机事业部", "智能驱控事业部"):
        b.add("FIN", I, GM, f"三季度{bu}的毛利率", "value", gpm + " AND f.business_unit = %s", (*_Q3, bu), templates=["T19"])
    for region in ("华东", "华南"):
        b.add("FIN", I, FIN, f"三季度{region}区域卖了多少钱？", "value", rev + " AND f.region = %s", (*_Q3, region), templates=["T19", "T20"])
    b.add("FIN", I, FIN, "三季度外销收入是多少？", "value", rev + " AND f.is_domestic = 0", _Q3, templates=["T19", "T20"])
    b.add("FIN", I, FIN, "三季度伺服电机的平均售价是多少？", "value", "SELECT ROUND(SUM(f.revenue_cny) / SUM(f.qty), 2) " + _FACT + " AND f.series = 'SERVO'", _Q3, templates=["T20"])
    top = db.meta_query("SELECT f.customer_name n, ROUND(SUM(f.revenue_cny), 2) v " + _FACT + " GROUP BY 1 ORDER BY v DESC LIMIT 1", ("2026-01-01", TODAY.isoformat()))
    b.add("FIN", I, FIN, "今年销售额最大的客户是谁，卖了多少？", "value", expected=float(top[0]["v"]), match=[top[0]["n"]], templates=["T19", "T20"])
    top = db.meta_query("SELECT f.customer_name n, ROUND(SUM(f.gross_profit_cny), 2) v " + _FACT + " GROUP BY 1 ORDER BY v DESC LIMIT 1", _Q3)
    b.add("FIN", I, GM, "三季度哪个客户毛利贡献最大？", "value", expected=float(top[0]["v"]), match=[top[0]["n"]], templates=["T19"])
    top = db.meta_query("SELECT f.customer_name n, ROUND(SUM(f.revenue_cny), 2) v " + _FACT + " AND f.series = 'SERVO' GROUP BY 1 ORDER BY v DESC LIMIT 1", ("2026-01-01", TODAY.isoformat()))
    b.add("FIN", I, FIN, "今年伺服系列销售额最大的客户是谁？", "value", expected=float(top[0]["v"]), match=[top[0]["n"]], templates=["T19", "T20"])
    top = db.meta_query("SELECT f.product_code n, ROUND(SUM(f.qty)) v " + _FACT + " GROUP BY 1 ORDER BY v DESC LIMIT 1", _Q3)
    b.add("FIN", I, FIN, "三季度销量最高的产品型号是哪个？", "value", expected=float(top[0]["v"]), match=[top[0]["n"]], templates=["T19", "T20"])
    for phrase, m in list(PAY_MONTHS.items())[:3]:
        s, e = _span(m)
        b.add("FIN", I, FIN, f"{phrase}的直接人工成本率是多少？", "value",
              "SELECT ROUND((SELECT SUM(s.gross_pay + s.social_employer) FROM hr_payslip s JOIN hr_payroll p ON p.id = s.payroll_id WHERE p.period = %s AND s.cost_type = 'PRODUCTION') / "
              "(SELECT SUM(f.revenue_cny) " + _FACT + "), 4)", (m, s, e), templates=["T21"])
    for cn, code in (("铜材", "COPPER"), ("磁钢", "MAGNET")):
        b.add("FIN", I, FIN, f"今年{cn}的采购价差一共多少？", "value",
              "SELECT ROUND(SUM(variance), 2) FROM v_price_variance WHERE category = %s AND invoice_date BETWEEN '2026-01-01' AND %s", (code, TODAY.isoformat()), templates=["T22"])
    b.add("FIN", I, FIN, "三季度外销收入占总收入的比例是多少？", "value",
          "SELECT ROUND(SUM(IF(f.is_domestic = 0, f.revenue_cny, 0)) / SUM(f.revenue_cny), 4) " + _FACT, _Q3, path="sql", templates=["T20"])
    top = db.meta_query("SELECT order_no n, amount_cny v FROM v_sales_order WHERE order_date BETWEEN '2026-01-01' AND %s ORDER BY amount_cny DESC LIMIT 1", (TODAY.isoformat(),))
    b.add("FIN", I, FIN, "今年金额最大的一张销售订单是哪张，多少钱？", "value", expected=float(top[0]["v"]), match=[top[0]["n"]], path="sql")
    # 归因和测算：检查总变动是否等于标准 SQL 算出的两期毛利之差
    gp = "SELECT ROUND(SUM(f.gross_profit_cny), 2) " + _FACT
    q3, q3ly = _one(gp, _Q3), _one(gp, ("2025-07-01", "2025-09-30"))
    b.add("FIN", I, GM, "三季度毛利为什么比去年同期低？", "value", expected=round(q3 - q3ly, 2), match=["合计"], path="analysis")
    q2 = _one(gp, ("2026-04-01", "2026-06-30"))
    b.add("FIN", I, FIN, "三季度毛利和二季度相比变化的原因是什么？", "value", expected=round(q3 - q2, 2), match=["合计"], path="analysis")
    m9, m8 = _one(gp, _span("2026-09")), _one(gp, _span("2026-08"))
    b.add("FIN", I, GM, "上个月毛利为什么变了？", "value", expected=round(m9 - m8, 2), match=["合计"], path="analysis")
    b.add("FIN", I, FIN, "如果单位成本上涨 3%，三季度毛利会减少多少？", "value", "SELECT ROUND(-SUM(f.cost_cny) * 0.03, 2) " + _FACT, _Q3, match=["合计"], path="analysis")
    b.add("FIN", I, FIN, "如果销售价格下调 2%，三季度毛利减少多少？", "value", "SELECT ROUND(-SUM(f.revenue_cny) * 0.02, 2) " + _FACT, _Q3, match=["合计"], path="analysis")
    # 权限：销售能看收入，看不了成本和毛利
    b.add("FIN", I, SALES, "上个月销售收入是多少？", "value", rev, _span("2026-09"), templates=["T20"])
    b.add("FIN", I, SALES, "三季度各产品系列销售额", "value", rev + " AND f.series = 'SERVO'", _Q3, match=["伺服"], templates=["T20"])
    for q in ("三季度各产品系列的毛利率是多少？", "上个月毛利是多少？", "三季度销售成本是多少？", "哪个客户毛利最高？", "三季度毛利为什么下降？"):
        b.add("FIN", I, SALES, q, "denied")
    b.add("FIN", I, HR, "三季度的毛利率是多少？", "denied")
    b.add("FIN", I, PROD, "上个月各产品系列毛利", "denied")


# ---------------------------------------------------------------- 7 回款与结算

def receivable_cases(b: Builder):
    I = "回款与结算"
    for phrase, m in list(MONTHS.items())[:5]:
        b.add("REC", I, FIN, f"{phrase}回款金额是多少？", "value", "SELECT ROUND(SUM(amount_cny), 2) FROM v_receipt WHERE receipt_date BETWEEN %s AND %s", _span(m), templates=["T24"])
    b.add("REC", I, FIN, "今年收到的承兑汇票一共多少钱？", "value",
          "SELECT ROUND(SUM(amount_cny), 2) FROM v_receipt WHERE method = 'ACCEPTANCE' AND receipt_date BETWEEN '2026-01-01' AND %s", (TODAY.isoformat(),), templates=["T24"])
    for phrase, m in (("上个月", "2026-09"), ("2026年8月", "2026-08")):
        top = db.meta_query("SELECT customer_name n, ROUND(SUM(amount_cny), 2) v FROM v_receipt WHERE receipt_date BETWEEN %s AND %s GROUP BY 1 ORDER BY v DESC LIMIT 1", _span(m))
        b.add("REC", I, FIN, f"{phrase}哪个客户回款最多？", "value", expected=float(top[0]["v"]), match=[top[0]["n"]], templates=["T24"])
    open_ = "FROM v_sales_invoice WHERE status IN ('OPEN','PARTIAL')"
    b.add("REC", I, FIN, "现在应收账款余额一共多少？", "value", "SELECT ROUND(SUM(outstanding_cny), 2) " + open_, templates=["T23"])
    b.add("REC", I, FIN, "目前逾期的应收一共多少钱？", "value", "SELECT ROUND(SUM(IF(overdue_days > 0, outstanding_cny, 0)), 2) " + open_, templates=["T23"])
    b.add("REC", I, GM, "现在应收逾期率是多少？", "value", "SELECT ROUND(SUM(IF(overdue_days > 0, outstanding_cny, 0)) / SUM(outstanding_cny), 4) " + open_, templates=["T23"])
    tops = db.meta_query("SELECT customer_name n, ROUND(SUM(IF(overdue_days > 0, outstanding_cny, 0)), 2) v, MAX(GREATEST(overdue_days, 0)) d " + open_ + " GROUP BY 1 ORDER BY v DESC LIMIT 3")
    for i, t in enumerate(tops):
        user = (FIN, SALES, GM)[i]
        b.add("REC", I, user, "哪些客户回款逾期了？", "value", expected=float(t["v"]), match=[t["n"]], templates=["T23"])
        b.add("REC", I, FIN, f"{t['n']}现在逾期多少钱？", "value", expected=float(t["v"]), match=[], templates=["T23"])
    b.add("REC", I, FIN, f"逾期金额最大的客户最长逾期多少天？", "value", expected=float(tops[0]["d"]), match=[tops[0]["n"]], templates=["T23"])
    for region in ("华南", "华东"):
        b.add("REC", I, FIN, f"{region}区域客户的逾期金额是多少？", "value", "SELECT ROUND(SUM(IF(overdue_days > 0, outstanding_cny, 0)), 2) " + open_ + " AND region = %s", (region,), templates=["T23"])
    for ind in ("家电", "机器人"):
        b.add("REC", I, FIN, f"{ind}行业客户的应收余额是多少？", "value", "SELECT ROUND(SUM(outstanding_cny), 2) " + open_ + " AND industry = %s", (ind,), templates=["T23"])
    b.add("REC", I, SALES, "各区域应收逾期情况", "value", "SELECT ROUND(SUM(IF(overdue_days > 0, outstanding_cny, 0)), 2) " + open_ + " AND region = '华南'", match=["华南"], templates=["T23"])
    b.add("REC", I, FIN, "现在有多少张发票逾期超过 90 天？", "value", "SELECT COUNT(*) " + open_ + " AND overdue_days > 90", path="sql")
    b.add("REC", I, FIN, "逾期超过 60 天的应收一共多少钱？", "value", "SELECT ROUND(SUM(outstanding_cny), 2) " + open_ + " AND overdue_days > 60", path="sql")
    b.add("REC", I, SALES, "上个月回款金额是多少？", "denied")           # 回款记录只对财务和管理层开放
    b.add("REC", I, PROD, "哪些客户回款逾期了？", "denied")


# ---------------------------------------------------------------- 8 多轮追问

def multiturn_cases(b: Builder):
    I = "多轮追问"
    rate = "SELECT ROUND(SUM(a.status IN ('NORMAL','LATE')) / COUNT(*), 4) " + _ATT
    ot = "SELECT ROUND(SUM(a.overtime_hours) / (COUNT(*) * 8), 4) " + _ATT
    sep, aug = _span("2026-09"), _span("2026-08")
    for (c1, n1), (c2, n2) in ((("L5", "伺服"), ("L4", "步进")), (("L1", "罩极"), ("L3", "无刷")), (("L4", "步进"), ("L6", "减速")), (("L3", "无刷"), ("L5", "伺服"))):
        b.add("MT", I, HR, [f"上个月{n1}线的加班率是多少？", f"那{n2}线呢？"], "value", ot + " AND e.line_code = %s", (*sep, c2), templates=["T07"])
    for code, cn in (("L5", "伺服"), ("L1", "罩极"), ("L4", "步进")):
        b.add("MT", I, HR, [f"上个月{cn}线的加班率是多少？", "那 8 月呢？"], "value", ot + " AND e.line_code = %s", (*aug, code), templates=["T07"])
        b.add("MT", I, HR, [f"上个月{cn}线的加班率是多少？", "那出勤率呢？"], "value", rate + " AND e.line_code = %s", (*sep, code), templates=["T06"])
    for (c1, n1), (c2, n2) in ((("L5", "伺服"), ("L4", "步进")), (("L1", "罩极"), ("L6", "减速")), (("L3", "无刷"), ("L1", "罩极"))):
        b.add("MT", I, HR, [f"上个月{n1}线的加班率是多少？", f"那{n2}线呢？", "换成 8 月"], "value", ot + " AND e.line_code = %s", (*aug, c2), templates=["T07"])
    b.add("MT", I, HR, ["上个月全公司的出勤率是多少？", "按部门看"], "value", rate + " AND e.dept_id = (SELECT id FROM org_dept WHERE name = '研发中心')", sep, match=["研发中心"], templates=["T06"])
    b.add("MT", I, HR, ["上个月全公司的出勤率是多少？", "那 8 月呢？"], "value", rate, aug, templates=["T06"])
    b.add("MT", I, HR, ["上个月全公司的出勤率是多少？", "只看研发中心"], "value", rate + " AND e.dept_id IN %s", (*sep, _dept_ids("研发中心")), templates=["T06"])
    b.add("MT", I, HR, ["上个月离职了多少人？", "按产线看"], "value",
          "SELECT COUNT(*) FROM hr_employee WHERE status = 'LEFT' AND leave_date BETWEEN %s AND %s AND line_code = 'L1'", sep, match=["L1"], templates=["T02", "T03", "T05"])
    b.add("MT", I, HR, ["2026年8月全公司人工成本是多少？", "只看研发中心"], "value",
          "SELECT ROUND(SUM(s.gross_pay + s.social_employer), 2) " + _PAY + " AND s.dept_id IN %s", ("2026-08", _dept_ids("研发中心")), templates=["T11"])
    b.add("MT", I, HR, ["2026年8月全公司人工成本是多少？", "那 7 月呢？"], "value", "SELECT ROUND(SUM(s.gross_pay + s.social_employer), 2) " + _PAY, ("2026-07",), templates=["T11"])
    gpm = "SELECT ROUND(SUM(f.gross_profit_cny) / SUM(f.revenue_cny), 4) " + _FACT
    rev = "SELECT ROUND(SUM(f.revenue_cny), 2) " + _FACT
    for code, cn in (("SERVO", "伺服"), ("BLDC", "无刷"), ("SHADED_POLE", "罩极")):
        b.add("MT", I, GM, ["三季度的毛利率是多少？", "按产品系列看"], "value", gpm + " AND f.series = %s", (*_Q3, code), match=[cn], templates=["T19"])
    b.add("MT", I, GM, ["三季度的毛利率是多少？", "那二季度呢？"], "value", gpm, ("2026-04-01", "2026-06-30"), templates=["T19"])
    b.add("MT", I, GM, ["三季度的毛利率是多少？", "只看智能驱控事业部"], "value", gpm + " AND f.business_unit = '智能驱控事业部'", _Q3, templates=["T19"])
    b.add("MT", I, FIN, ["2026年8月销售收入是多少？", "那 9 月呢？"], "value", rev, sep, templates=["T19", "T20"])
    b.add("MT", I, FIN, ["2026年8月销售收入是多少？", "只看华东"], "value", rev + " AND f.region = '华东'", aug, templates=["T19", "T20"])
    b.add("MT", I, FIN, ["2026年8月销售收入是多少？", "按区域看"], "value", rev + " AND f.region = '华南'", aug, match=["华南"], templates=["T19", "T20"])
    b.add("MT", I, GM, ["上个月各产线良率", "只看伺服线"], "value",
          "SELECT ROUND(SUM(r.good_qty) / (SUM(r.good_qty) + SUM(r.scrap_qty)), 4) " + _RPT + " AND w.line_code = 'L5'", sep, templates=["T15"])
    b.add("MT", I, GM, ["上个月各产线良率", "那 8 月呢？"], "value",
          "SELECT ROUND(SUM(r.good_qty) / (SUM(r.good_qty) + SUM(r.scrap_qty)), 4) " + _RPT + " AND w.line_code = 'L5'", aug, match=["L5"], templates=["T15"])
    open_ = "FROM v_sales_invoice WHERE status IN ('OPEN','PARTIAL')"
    top = db.meta_query("SELECT customer_name n, ROUND(SUM(IF(overdue_days > 0, outstanding_cny, 0)), 2) v " + open_ + " AND region = '华南' GROUP BY 1 ORDER BY v DESC LIMIT 1")
    b.add("MT", I, FIN, ["哪些客户回款逾期了？", "只看华南"], "value", expected=float(top[0]["v"]), match=[top[0]["n"]], templates=["T23"])
    b.add("MT", I, HR, ["迟到多久算旷工？", "上个月一共迟到多少人次？"], "value", "SELECT SUM(a.status = 'LATE') " + _ATT, sep, templates=["T10", "T06"])     # 从制度切到数据，不应继承
    b.add("MT", I, SALES, ["上个月销售收入是多少？", "那毛利呢？"], "denied")                                                    # 追问也要重新过权限


def build() -> list[Case]:
    b = Builder()
    for fn in (policy_cases, people_cases, attendance_cases, payroll_cases, production_cases, finance_cases, receivable_cases, multiturn_cases):
        fn(b)
    return b.cases


def summary(cases: list[Case]) -> dict:
    by_intent: dict[str, int] = {}
    for c in cases:
        by_intent[c.intent] = by_intent.get(c.intent, 0) + 1
    return {"total": len(cases), "by_intent": by_intent, "multi_turn": sum(len(c.turns) > 1 for c in cases),
            "by_kind": {k: sum(c.kind == k for c in cases) for k in sorted({c.kind for c in cases})}}


if __name__ == "__main__":
    import json

    cs = build()
    print(json.dumps(summary(cs), ensure_ascii=False, indent=2))
    print(json.dumps(asdict(cs[0]), ensure_ascii=False))
