"""
时间解析：模型只负责把用户说的时间短语原样抽出来（"上个月""三季度""最近半年"），
换算成起止日期由这里的代码完成。模型不知道今天是几号，交给它算容易错。
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.infra.config.settings import settings

_CN = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str
    grain: str = "month"          # day / month / quarter / year，用来决定默认的对比期

    @property
    def start_month(self) -> str:
        return self.start.strftime("%Y-%m")

    @property
    def end_month(self) -> str:
        return self.end.strftime("%Y-%m")


def today() -> date:
    return date.fromisoformat(settings.copilot_today) if settings.copilot_today else date.today()


def _num(s: str) -> float:
    """'12' '十二' '三' '半' → 数字"""
    if re.fullmatch(r"\d+", s):
        return int(s)
    if s in _CN:
        return _CN[s]
    if s.startswith("十"):
        return 10 + (_CN.get(s[1:], 0) if len(s) > 1 else 0)
    if "十" in s:
        a, b = s.split("十", 1)
        return _CN[a] * 10 + (_CN.get(b, 0) if b else 0)
    raise ValueError(s)


def _month(y: int, m: int) -> Period:
    return Period(date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]), f"{y}-{m:02d}", "month")


def _quarter(y: int, q: int) -> Period:
    m = 3 * q - 2
    return Period(date(y, m, 1), date(y, m + 2, calendar.monthrange(y, m + 2)[1]), f"{y} 年第 {q} 季度", "quarter")


def _year(y: int, t: date) -> Period:
    if y == t.year:
        return Period(date(y, 1, 1), t, f"{y} 年至今", "year")
    return Period(date(y, 1, 1), date(y, 12, 31), f"{y} 年", "year")


def _shift_month(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


_Y = r"(?:(?P<y>\d{4})\s*年?|(?P<ry>今年|本年|去年|前年|明年))"
_N = r"(\d+|[一二两三四五六七八九十]+|半)"


def _year_of(m: re.Match, t: date, default: int | None = None) -> int | None:
    if m.groupdict().get("y"):
        return int(m.group("y"))
    ry = m.groupdict().get("ry")
    if ry:
        return {"今年": t.year, "本年": t.year, "去年": t.year - 1, "前年": t.year - 2, "明年": t.year + 1}[ry]
    return default


def _single(text: str, t: date) -> Period | None:
    s = re.sub(r"\s+", "", text).replace("份", "").replace("的", "")
    if s.endswith(("年底", "年末")):                 # "去年年底""2025年底" → 那一年
        s = s[:-2] or "今年"
    elif s.endswith(("月底", "月末")):               # "2025年12月底" → 那个月
        s = s[:-1]
    if not s:
        return None
    # 具体日期
    m = re.fullmatch(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", s)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return Period(d, d, d.isoformat(), "day")
    if s in ("今天", "今日"):
        return Period(t, t, t.isoformat(), "day")
    if s in ("昨天", "昨日"):
        d = t - timedelta(days=1)
        return Period(d, d, d.isoformat(), "day")
    if s in ("本周", "这周", "这个星期"):
        d = t - timedelta(days=t.weekday())
        return Period(d, t, "本周", "day")
    if s in ("上周", "上个星期", "上星期"):
        d = t - timedelta(days=t.weekday() + 7)
        return Period(d, d + timedelta(days=6), "上周", "day")
    # 月
    if s in ("本月", "这个月", "当月", "这月"):
        return Period(date(t.year, t.month, 1), t, f"{t.year}-{t.month:02d}（本月至今）", "month")
    m = re.fullmatch(r"(上+)(?:个)?月", s)
    if m:
        return _month(*_shift_month(t.year, t.month, -len(m.group(1))))
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", s) or re.fullmatch(r"(\d{4})(\d{2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return _month(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(_Y + r"?" + _N + r"月", s)
    if m:
        mon = int(_num(m.group(3)))
        if 1 <= mon <= 12:
            y = _year_of(m, t)
            if y is None:                                   # 没说年份：不晚于本月就是今年，否则是去年
                y = t.year if mon <= t.month else t.year - 1
            return _month(y, mon)
    # 季度
    if s in ("本季度", "这个季度", "本季", "当季"):
        q = (t.month - 1) // 3 + 1
        p = _quarter(t.year, q)
        return Period(p.start, t, f"{t.year} 年第 {q} 季度至今", "quarter")
    if s in ("上季度", "上个季度", "上季"):
        y, mon = _shift_month(t.year, t.month, -3)
        return _quarter(y, (mon - 1) // 3 + 1)
    m = re.fullmatch(_Y + r"?[-]?(?:第)?(?:Q|q)?" + r"([1-4一二三四])(?:季度|季)?", s)
    if m and (re.search(r"[Qq季]", s)):
        q = int(_num(m.group(3)))
        y = _year_of(m, t)
        if y is None:
            y = t.year if 3 * q - 2 <= t.month else t.year - 1
        return _quarter(y, q)
    # 半年
    m = re.fullmatch(_Y + r"?(上|下)半年", s)
    if m:
        y = _year_of(m, t, t.year)
        if m.group(3) == "上":
            return Period(date(y, 1, 1), date(y, 6, 30), f"{y} 年上半年", "quarter")
        end = t if (y == t.year and t.month >= 7) else date(y, 12, 31)
        return Period(date(y, 7, 1), end, f"{y} 年下半年", "quarter")
    # 年
    if s in ("年初至今", "今年以来", "截至目前", "到目前为止", "YTD", "ytd", "今年至今", "今年到现在"):
        return _year(t.year, t)
    m = re.fullmatch(_Y + r"(?:全年|整年|一年)?", s)
    if m:
        return _year(_year_of(m, t), t)
    # 最近 N 个月 / 天：取已经过完的完整月份，不含本月
    m = re.fullmatch(r"(?:最近|近|过去|前)" + _N + r"(?:个)?月", s)
    if m:
        n = max(1, int(_num(m.group(1)))) if m.group(1) != "半" else 1
        ys, ms = _shift_month(t.year, t.month, -n)
        ye, me = _shift_month(t.year, t.month, -1)
        return Period(date(ys, ms, 1), _month(ye, me).end, f"最近 {n} 个月（{ys}-{ms:02d} 至 {ye}-{me:02d}）", "month")
    m = re.fullmatch(r"(?:最近|近|过去)" + _N + r"年", s)
    if m:
        n = 6 if m.group(1) == "半" else int(_num(m.group(1))) * 12
        ys, ms = _shift_month(t.year, t.month, -n)
        ye, me = _shift_month(t.year, t.month, -1)
        return Period(date(ys, ms, 1), _month(ye, me).end, f"最近 {n} 个月（{ys}-{ms:02d} 至 {ye}-{me:02d}）", "month")
    m = re.fullmatch(r"(?:最近|近|过去)" + _N + r"(天|日|周|个星期|星期)", s)
    if m:
        n = int(_num(m.group(1))) * (1 if m.group(2) in ("天", "日") else 7)
        return Period(t - timedelta(days=n - 1), t, f"最近 {n} 天", "day")
    return None


def parse_period(text: str | None, now: date | None = None) -> Period | None:
    """解析时间短语；解析不了返回 None"""
    if not text:
        return None
    t = now or today()
    s = re.sub(r"\s+", "", str(text))
    whole = _single(s, t)
    if whole:
        return whole
    # 区间："1月到6月""2025年10月至2026年3月""2026-01~2026-06"
    m = re.fullmatch(r"(.+?)(?:到|至|~|—|－|--)(.+)", s)
    if m:
        left, right = m.group(1), m.group(2)
        b = _single(right, t)
        if b:
            a = _single(left, t)
            if a is None and re.fullmatch(_N + r"月?", left):          # "1到6月"：左边沿用右边的年份
                a = _month(b.end.year, int(_num(left.rstrip("月"))))
            if a is None and re.fullmatch(r"\d{4}年?" + _N + r"月?", left):
                a = _single(left if left.endswith("月") else left + "月", t)
            if a and a.start <= b.end:
                return Period(a.start, b.end, f"{a.start.isoformat()} 至 {b.end.isoformat()}", a.grain if a.grain == b.grain else "month")
    return None


def previous(p: Period) -> Period:
    """默认的对比期：上一个同样长度的期间（环比）"""
    if p.grain == "month" and p.start.day == 1 and p.start_month == p.end_month:
        return _month(*_shift_month(p.start.year, p.start.month, -1))
    if p.grain == "quarter" and p.start.day == 1 and p.start.month % 3 == 1 and (p.end.month - p.start.month) == 2:
        y, m = _shift_month(p.start.year, p.start.month, -3)
        return _quarter(y, (m - 1) // 3 + 1)
    if p.grain == "year":
        return same_period_last_year(p)
    days = (p.end - p.start).days + 1
    return Period(p.start - timedelta(days=days), p.start - timedelta(days=1), "上一期", p.grain)


def same_period_last_year(p: Period) -> Period:
    """同比：去年同期"""
    def back(d: date) -> date:
        return date(d.year - 1, d.month, min(d.day, calendar.monthrange(d.year - 1, d.month)[1]))
    return Period(back(p.start), back(p.end), f"去年同期（{back(p.start).isoformat()} 至 {back(p.end).isoformat()}）", p.grain)
