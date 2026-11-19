"""
参数结构化：把 datasheet 和选型表里的参数拆成"型号—参数名—数值—单位"的记录。
数值类问题（额定转矩多少、哪个型号功率最大）之后走 SQL 精确查询，不让向量检索去区分 750 和 400。
"""
import re

# 标准参数名：不同文档里的叫法统一到同一个 key
ALIASES = {
    "rated_voltage": ["额定电压", "电压", "工作电压", "rated voltage"],
    "rated_power": ["额定功率", "功率", "输出功率", "rated power"],
    "rated_speed": ["额定转速", "转速", "rated speed"],
    "max_speed": ["最高转速", "最大转速"],
    "rated_torque": ["额定转矩", "额定扭矩", "转矩", "rated torque"],
    "peak_torque": ["峰值转矩", "最大转矩", "瞬时最大转矩"],
    "holding_torque": ["保持转矩", "保持力矩"],
    "rated_current": ["额定电流", "相电流", "电流"],
    "efficiency": ["效率"],
    "insulation_class": ["绝缘等级"],
    "protection_class": ["防护等级", "ip 等级"],
    "temp_rise": ["温升"],
    "ambient_temp": ["工作环境温度", "环境温度", "使用温度"],
    "life_hours": ["寿命"],
    "noise": ["噪声", "噪音"],
    "rotor_inertia": ["转子惯量", "转动惯量"],
    "step_angle": ["步距角"],
    "flange_size": ["法兰尺寸", "法兰", "机座号"],
    "weight": ["重量", "质量"],
    "encoder": ["编码器"],
    "poles": ["极数", "极对数"],
    "gear_ratio": ["减速比", "速比"],
    "list_price": ["标准售价", "参考价", "单价"],
}
LABEL = {k: v[0] for k, v in ALIASES.items()}
_BY_ALIAS = sorted(((a.lower(), k) for k, names in ALIASES.items() for a in names), key=lambda x: -len(x[0]))
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_HEAD_UNIT = re.compile(r"^(.*?)[\s]*[（(\[]\s*([^）)\]]+)\s*[）)\]]\s*$")


def param_key(name: str) -> str | None:
    n = re.sub(r"\s+", "", name).lower()
    for alias, key in _BY_ALIAS:
        if alias.replace(" ", "") in n:
            return key
    return None


def split_header(header: str) -> tuple[str, str]:
    """'额定转矩(N·m)' → ('额定转矩', 'N·m')"""
    m = _HEAD_UNIT.match(header.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (header.strip(), "")


def parse_value(raw, unit_hint: str = "") -> tuple[float | None, str, str]:
    """'750 W' → (750, '750 W', 'W')；'≥ 88%' → (88, '≥ 88%', '%')；'F 级' → (None, 'F 级', '')"""
    if raw is None:
        return None, "", unit_hint
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw), f"{raw:g}" + (f" {unit_hint}" if unit_hint else ""), unit_hint
    text = str(raw).strip()
    if "～" in text or "~" in text or re.search(r"\d\s*[-–—]\s*[+\-]?\d", text):     # 范围值不拆成单个数
        return None, text, unit_hint
    m = _NUM.search(text)
    if not m:
        return None, text, unit_hint
    unit = unit_hint or re.sub(r"[（(].*", "", text[m.end():]).strip()[:20]
    return float(m.group(0)), text, unit


def from_markdown(md: str, model: str, source: str = "") -> list[dict]:
    """从规格书的"参数｜数值"表格和"- 名称：值"列表里抽参数。只收能对上标准参数名的行"""
    out, seen = [], set()

    def add(name: str, raw: str, ref: str) -> None:
        key = param_key(name)
        if not key or key in seen or not raw.strip():
            return
        num, text, unit = parse_value(raw)
        seen.add(key)
        out.append({"model": model, "param_key": key, "param_name": name.strip(), "value_num": num, "value_text": text[:200],
                    "unit": unit, "source_ref": ref})

    section = ""
    for line in md.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            section = s.lstrip("# ").strip()
        elif s.startswith("|") and not re.fullmatch(r"\|[\s:|\-]+\|", s):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if len(cells) == 2 and cells[0] not in ("参数", "项目", "名称"):
                add(cells[0], cells[1], f"{source} {section}".strip())
        else:
            m = re.match(r"^[-*]\s*([^：:]{2,12})[：:]\s*(.+)$", s)
            if m:
                add(m.group(1), m.group(2), f"{source} {section}".strip())
    return out


def describe(model: str, params: list[dict]) -> str:
    """把一个型号的参数写成一句话，进向量库（让"说明类"问题也能检索到这个型号）"""
    parts = [f"{p['param_name']} {p['value_text']}" for p in params]
    return f"{model}：" + "，".join(parts) + "。"
