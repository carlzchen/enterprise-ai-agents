"""
切片主题识别：只对新增的切片逐块调用大模型（线上是 vLLM 部署的 Qwen3-32B，本地演示用 .env 里配置的主模型），
复用的切片沿用上一版的结果。

  模型输出：这块内容讲的是哪个（些）型号、哪个系列、什么类型的内容，以及支撑判断的原文片段
  证据校验（不信模型的结论，只信能在原文里找到的）：
    1. 证据片段必须能在切片原文里找到（忽略空白）
    2. 型号必须符合编码规则，并且出现在原文里，或者就是文档元数据里的型号
    3. 系列必须在系列表里，且和型号前缀一致
  校验不过或者模型调用失败 → 退回规则识别（正则找型号 + 文档元数据），并记下原因
  模型连续失败过半 → 剩下的切片直接用规则，不再一块块等超时

主题结果写进 kb_chunk.subject（含证据和识别方式）和 Milvus 的 item_name / series 字段，问答时「主题确认」锁定型号或系列后按它过滤。
"""
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from app.copilot import llm
from app.infra.config.settings import settings
from app.kbase.ingest.clean import normalize_model
from app.kbase.ingest.parsers import MODEL_RE
from app.shared.runtime.logger import logger

PREFIX_SERIES = {"KL-SV": "SERVO", "KL-ST": "STEPPER", "KL-BL": "BLDC", "KL-SP": "SHADED_POLE", "KL-CF": "CROSSFLOW", "KL-GR": "GEAR"}
SERIES_CN = {"SERVO": "伺服", "STEPPER": "步进", "BLDC": "无刷直流", "SHADED_POLE": "罩极", "CROSSFLOW": "贯流", "GEAR": "减速"}
TOPICS = ("参数规格", "尺寸图纸", "安装接线", "工艺规程", "检验标准", "故障处理", "选型说明", "其他")
WORKERS = 4
TIMEOUT = 30

PROMPT = """判断下面这段电机产品资料讲的是哪个型号、哪个系列、什么类型的内容。

文档：《{title}》（文档登记的型号：{doc_model}；系列：{doc_series}）
片段：
{text}

规则：
1. models：片段明确讲到的型号，照原文写（形如 KL-SV-80-750W）；只是顺带提到的不算；没有就给空数组
2. series：从 {series} 里选一个，判断不了给空字符串
3. topic：从 {topics} 里选一个
4. evidence：从片段里原样摘一句支撑你判断的话（20~60 字），不要改写
只输出 JSON：{{"models": [], "series": "", "topic": "", "evidence": ""}}"""


def series_of(model: str | None) -> str | None:
    return PREFIX_SERIES.get((model or "")[:5])


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def rule_subject(text: str, meta: dict) -> dict:
    """规则识别：片段里只出现一个型号就认它，否则用文档登记的型号；系列跟着型号走，再不行用文档的系列"""
    found = list(dict.fromkeys(MODEL_RE.findall(text)))
    models = found if len(found) == 1 else ([meta["product_code"]] if meta.get("product_code") else found[:3])
    series = series_of(models[0]) if models else None
    return {"models": models, "series": series or meta.get("series") or "", "topic": "", "evidence": "", "by": "rule"}


def check(out: dict, text: str, meta: dict) -> tuple[dict | None, str]:
    """证据校验。通过返回（清洗后的结果, ""），不通过返回（None, 原因）"""
    ev = (out.get("evidence") or "").strip()
    if len(_squash(ev)) < 6 or _squash(ev) not in _squash(text):
        return None, "证据片段在原文里找不到"
    models = []
    for m in out.get("models") or []:
        m = normalize_model(str(m).strip())
        if not MODEL_RE.fullmatch(m):
            return None, f"型号 {m} 不符合编码规则"
        if m not in text and m != meta.get("product_code"):
            return None, f"型号 {m} 在原文和文档元数据里都没有"
        models.append(m)
    series = (out.get("series") or "").strip().upper()
    if series and series not in SERIES_CN:
        return None, f"系列 {series} 不在系列表里"
    expect = {series_of(m) for m in models} - {None}
    if series and expect and series not in expect:
        return None, f"系列 {series} 和型号前缀不一致"
    series = series or (expect.pop() if len(expect) == 1 else "")
    topic = out.get("topic") if out.get("topic") in TOPICS else "其他"
    return {"models": models, "series": series, "topic": topic, "evidence": ev[:120], "by": "llm"}, ""


def _call(text: str, meta: dict) -> dict:
    p = PROMPT.format(title=meta.get("title", ""), doc_model=meta.get("product_code") or "无", doc_series=meta.get("series") or "无",
                      text=text[:2500], series="、".join(SERIES_CN), topics="、".join(TOPICS))
    msg = llm.invoke(p, json_mode=True, app="kbase")
    return json.loads(llm.strip_fence(msg.content))


def identify(chunks: list[dict], meta: dict, use_llm: bool | None = None) -> dict:
    """给 chunks（只传新增的）逐块识别主题，结果写回每个 chunk 的 subject / item_name / series。返回统计"""
    use_llm = settings.kbase_subject_llm if use_llm is None else use_llm
    stats = {"llm": 0, "rule": 0, "check_failed": 0, "call_failed": 0}
    lock, fails = threading.Lock(), {"n": 0, "done": 0}

    def one(c: dict) -> None:
        sub, why = None, ""
        tripped = fails["done"] >= 6 and fails["n"] / fails["done"] > 0.5
        if use_llm and not tripped:
            try:
                from app.platform import resilience as R
                sub, why = check(R.run_with_timeout("主题识别", TIMEOUT, _call, c["content"], meta), c["content"], meta)
                with lock:
                    fails["done"] += 1
                    stats["check_failed" if sub is None else "llm"] += 1
            except Exception as e:
                why = f"模型调用失败：{type(e).__name__}"
                with lock:
                    fails["done"] += 1
                    fails["n"] += 1
                    stats["call_failed"] += 1
        elif use_llm:
            why = "模型连续失败过半，改用规则"
        if sub is None:
            sub = {**rule_subject(c["content"], meta), "fallback_reason": why or "未启用模型识别"}
            with lock:
                stats["rule"] += 1
        c["subject"] = sub
        c["item_name"] = sub["models"][0] if len(sub["models"]) == 1 else (c.get("item_name") or "")
        c["series"] = sub["series"] or ""

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(one, chunks))
    if chunks:
        logger.info(f"[kbase] 主题识别 {len(chunks)} 块：模型 {stats['llm']}，规则 {stats['rule']}（证据校验不过 {stats['check_failed']}，调用失败 {stats['call_failed']}）")
    return stats
