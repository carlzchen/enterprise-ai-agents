"""
输入、输出两道内容检查：
  输入  敏感词（商业敏感信息、越权和注入类说法）命中就直接拒答，不进检索
  输出  回答里出现手机号、身份证号、银行卡号这类格式的内容时打码
权限不在这里做（权限在检索层前置过滤），这里只是内容层面的兜底。
"""
import re
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).resolve().parents[1] / "resources" / "sensitive_words.txt"
_PII = [(re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)"), r"\1****\2"),
        (re.compile(r"(?<!\d)(\d{4})\d{10}(\d{3}[\dXx])(?!\d)"), r"\1**********\2"),
        (re.compile(r"(?<!\d)(62\d{4})\d{6,9}(\d{4})(?!\d)"), r"\1******\2")]


@lru_cache(maxsize=1)
def _words() -> dict[str, list[str]]:
    out, section = {"拒绝": [], "脱敏": []}, None
    for line in _FILE.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
        elif section in out:
            out[section].append(s.lower())
    return out


def check_input(question: str) -> str | None:
    """返回命中的敏感词；没有命中返回 None"""
    q = re.sub(r"\s+", "", question).lower()
    return next((w for w in _words()["拒绝"] if w.replace(" ", "") in q), None)


def clean_output(text: str) -> tuple[str, int]:
    """输出脱敏。返回（处理后的文本, 打码的处数）"""
    n = 0
    for pattern, repl in _PII:
        text, k = pattern.subn(repl, text)
        n += k
    for w in _words()["脱敏"]:
        if w and w in text.lower():
            text = re.sub(re.escape(w), "＊" * len(w), text, flags=re.I)
            n += 1
    return text, n
