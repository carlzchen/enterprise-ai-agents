"""
维度字典：编码 ↔ 中文。两个用途：
  1. 把用户说的"伺服线""劳务派遣"归一成数据库里的编码（过滤条件用）
  2. 把查询结果里的编码翻译成中文（展示用）
"""
LINES = {"L1": "罩极", "L2": "贯流", "L3": "无刷", "L4": "步进", "L5": "伺服", "L6": "减速", "L7": "伺服组件", "L8": "冲压叠片"}
SERIES = {"SHADED_POLE": "罩极", "CROSSFLOW": "贯流", "BLDC": "无刷", "STEPPER": "步进", "SERVO": "伺服", "GEAR": "减速"}
EMPLOYMENT = {"FULL_TIME": "正式", "DISPATCH": "劳务派遣", "INTERN": "实习"}
WAGE = {"MONTHLY": "月薪", "PIECE": "计件"}
GENDER = {"M": "男", "F": "女"}
LEAVE = {"ANNUAL": "年假", "SICK": "病假", "PERSONAL": "事假"}
COST = {"PRODUCTION": "制造费用", "SALES": "销售费用", "ADMIN": "管理费用", "RND": "研发费用"}
ATT = {"NORMAL": "正常", "LATE": "迟到", "ABSENT": "旷工", "LEAVE": "请假"}
EMP_STATUS = {"PROBATION": "试用", "ACTIVE": "在职", "LEFT": "离职"}
METHOD = {"BANK": "银行转账", "ACCEPTANCE": "承兑汇票"}
CATEGORY = {"COPPER": "铜材", "MAGNET": "磁钢", "STEEL": "硅钢", "BEARING": "轴承"}
BUSINESS_UNITS = ["家电电机事业部", "智能驱控事业部"]

# 结果列名（中文别名或原字段名）→ 用哪个字典翻译
COLUMN_LABELS = {
    "产线": LINES, "line_code": LINES, "产品系列": SERIES, "series": SERIES, "用工类型": EMPLOYMENT, "employment_type": EMPLOYMENT,
    "计薪方式": WAGE, "wage_type": WAGE, "性别": GENDER, "gender": GENDER, "请假类型": LEAVE, "leave_type": LEAVE,
    "成本类型": COST, "cost_type": COST, "收款方式": METHOD, "method": METHOD, "物料类别": CATEGORY, "category": CATEGORY,
}

# 过滤条件名 → (编码字典, 额外的口语说法)
_EXTRA = {
    "产线": {"罩极电机线": "L1", "贯流风机线": "L2", "无刷电机线": "L3", "步进电机线": "L4", "伺服电机线": "L5", "减速电机线": "L6",
             "组件线": "L7", "伺服组件线": "L7", "冲压线": "L8", "冲压叠片线": "L8", "叠片线": "L8"},
    "产品系列": {"无刷电机": "BLDC", "直流无刷": "BLDC", "步进电机": "STEPPER", "伺服电机": "SERVO", "罩极电机": "SHADED_POLE",
                 "贯流风机": "CROSSFLOW", "贯流电机": "CROSSFLOW", "减速电机": "GEAR", "齿轮电机": "GEAR"},
    "用工类型": {"派遣": "DISPATCH", "派遣工": "DISPATCH", "劳务工": "DISPATCH", "正式工": "FULL_TIME", "正式员工": "FULL_TIME", "实习生": "INTERN"},
    "计薪方式": {"计件工": "PIECE", "月薪制": "MONTHLY", "职员": "MONTHLY"},
    "性别": {"男性": "M", "女性": "F", "男工": "M", "女工": "F"},
    "请假类型": {},
    "成本类型": {"制造": "PRODUCTION", "生产": "PRODUCTION", "销售": "SALES", "管理": "ADMIN", "研发": "RND"},
    "收款方式": {"承兑": "ACCEPTANCE", "汇票": "ACCEPTANCE", "银行": "BANK", "转账": "BANK", "电汇": "BANK"},
    "物料类别": {"铜": "COPPER", "铜线": "COPPER", "漆包线": "COPPER", "磁铁": "MAGNET", "磁材": "MAGNET"},
    "内外销": {"内销": 1, "国内": 1, "外销": 0, "出口": 0, "海外": 0},
    "事业部": {"家电": "家电电机事业部", "家电电机": "家电电机事业部", "家电事业部": "家电电机事业部",
               "智能驱控": "智能驱控事业部", "驱控": "智能驱控事业部", "驱控事业部": "智能驱控事业部"},
}
_DICTS = {"产线": LINES, "产品系列": SERIES, "用工类型": EMPLOYMENT, "计薪方式": WAGE, "性别": GENDER, "请假类型": LEAVE,
          "成本类型": COST, "收款方式": METHOD, "物料类别": CATEGORY}


def normalize(dim: str, value) -> object | None:
    """把用户的说法归一成数据库里的值；认不出来返回 None（由调用方决定是模糊匹配还是追问）"""
    if value is None:
        return None
    v = str(value).strip()
    codes = _DICTS.get(dim, {})
    if v.upper() in codes:
        return v.upper()
    if dim == "事业部" and v in BUSINESS_UNITS:
        return v
    if dim == "班组" and v.upper().rstrip("班组") in ("A", "B", "C"):
        return v.upper().rstrip("班组")
    extra = _EXTRA.get(dim, {})
    if v in extra:
        return extra[v]
    for code, cn in codes.items():                      # "伺服线" "伺服产线" "伺服" 都认
        if v in (cn, cn + "线", cn + "产线", cn + "系列", cn + "电机", cn + "员工", cn + "工"):
            return code
    for k, code in extra.items():
        if k in v:
            return code
    for code, cn in codes.items():
        if cn in v:
            return code
    return None


def display(column: str, value):
    """结果展示：L5 → 'L5 伺服'，DISPATCH → '劳务派遣'"""
    d = COLUMN_LABELS.get(column)
    if d is None or value is None or value not in d:
        return value
    return f"{value} {d[value]}" if d is LINES else d[value]


def dictionary_prompt() -> str:
    """给模型看的维度取值说明"""
    return "\n".join([
        "产线：" + "、".join(f"{k}={v}" for k, v in LINES.items()),
        "产品系列：" + "、".join(f"{k}={v}" for k, v in SERIES.items()),
        "事业部：" + "、".join(BUSINESS_UNITS) + "（家电：罩极、贯流、减速；智能驱控：无刷、步进、伺服）",
        "用工类型：" + "、".join(f"{k}={v}" for k, v in EMPLOYMENT.items()),
        "计薪方式：" + "、".join(f"{k}={v}" for k, v in WAGE.items()),
        "请假类型：" + "、".join(f"{k}={v}" for k, v in LEAVE.items()),
        "成本类型：" + "、".join(f"{k}={v}" for k, v in COST.items()),
    ])
