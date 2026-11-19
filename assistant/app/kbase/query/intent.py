"""
问题理解：改写（指代消解）、抽型号和参数名、判断走哪条路线。
  PARAM  问某个型号的参数、尺寸、公差，或者按参数筛选、比大小 → SQL 精确查询
  DESC   问原因、方法、工艺、故障处理 → 向量检索
  MIXED  两者都有 → 两路并行
为什么要分：向量检索分不清 750 和 400、Φ19 和 Φ14 这种相近的数；数值必须从结构化数据里取。
首轮提问用规则就能判断，不调用模型；只有追问（有上文、有指代）才让模型结合历史把问题补全。
"""
import json
import re
import time

from app.copilot import llm
from app.kbase.query import prompts
from app.platform import tracing
from app.kbase.ingest import params as P
from app.kbase.ingest.clean import normalize_model
from app.kbase.ingest.parsers import DIM_LABEL, MODEL_RE

SERIES = {"伺服": "SERVO", "步进": "STEPPER", "无刷": "BLDC", "罩极": "SHADED_POLE", "贯流": "CROSSFLOW", "减速": "GEAR", "齿轮": "GEAR"}
_DIM_ALIAS = {"spigot_dia": ["止口直径", "止口"], "shaft_dia": ["轴径", "轴直径", "出轴直径"], "shaft_len": ["轴伸长度", "轴伸", "出轴长度"],
              "flange_size": ["法兰边长", "法兰尺寸"], "pcd": ["分度圆", "安装孔距", "pcd"], "hole_dia": ["安装孔直径", "安装孔径", "孔径"],
              "hole_count": ["安装孔数量", "几个安装孔", "安装孔有几个", "几个孔"]}
_DESC = re.compile(r"为什么|原因|怎么办|怎么处理|如何|怎样|步骤|流程|注意事项|要求是什么|工艺|故障|报警|异常|处理方法|区别|适用|应用|能不能|可以.*吗|变更|改了什么")
_SUPER = re.compile(r"最大|最高|最小|最低|最重|最轻|最长|最短")
_CMP = re.compile(r"(大于|超过|高于|不低于|至少|小于|低于|不超过|不高于)\s*(\d+(?:\.\d+)?)")
_REF = re.compile(r"它|这个|那个|该型号|这款|那款|上面|刚才|前面|同样|也|呢[？?]?$")
_PRODUCT_CODE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9][A-Za-z0-9□■]*(?:[-.][A-Za-z0-9□■]+)*(?![A-Za-z0-9_-])")
_NON_PRODUCT_CODE = re.compile(
    r"(?:IP\d+|RS-?\d+|\d+(?:\.\d+)?(?:VDC|VAC|V|W|KW|HP|A|MA|HZ|KHZ|MHZ|RPM|MM|CM|M|NM|N|DEG)|[123]-?PHASE|[23]D)$"
)


def find_models(text: str) -> list[str]:
    return list(dict.fromkeys(MODEL_RE.findall(normalize_model(text))))


def find_external_identifiers(text: str) -> list[str]:
    codes = [code.upper() for code in _PRODUCT_CODE.findall(text)
             if re.search(r"\d", code) and re.search(r"[A-Za-z]", code)
             and not code.upper().startswith("KL-") and not _NON_PRODUCT_CODE.fullmatch(code.upper())]
    signals = re.findall(r"(?<![A-Za-z0-9_-])[A-Z]{2,}(?:-[A-Z]{2,})+[+-]?(?![A-Za-z0-9_-])|(?<![A-Za-z0-9_-])[A-Z]{2,}[+-](?![A-Za-z0-9_-])", text)
    return list(dict.fromkeys([*codes, *[signal for signal in signals if not signal.startswith('KL-')]]))


def find_params(text: str) -> list[str]:
    t = re.sub(r"\s+", "", text).lower()
    keys = [k for k, names in _DIM_ALIAS.items() if any(n.lower() in t for n in names)]
    for k, names in P.ALIASES.items():
        if any(n.lower().replace(" ", "") in t for n in names) and k not in keys:
            if k == "flange_size" and "flange_size" in keys:
                continue
            keys.append(k)
    if "公差" in t and not any(k in keys for k in ("spigot_dia", "shaft_dia")):
        keys += ["spigot_dia", "shaft_dia"]
    return keys


_NAMES = sorted({n.lower().replace(" ", "") for names in [*P.ALIASES.values(), *_DIM_ALIAS.values()] for n in names}, key=len, reverse=True)
_FILLER = re.compile(r"请问|帮我|查一下|查查|看一下|告诉我|是多少|有多少|多少|是什么|分别|各是|对比|比较|哪个|哪款|哪些|型号|这款|这个|电机|伺服|步进|"
                     r"公差|数值|参数|[的是和与跟及、呢吗啊呀吧了有]|[^\u4e00-\u9fff]")


def has_extra_ask(question: str) -> bool:
    """去掉型号、参数名和「是多少」这类问法之后还剩不少字，说明除了问参数还问了别的（要注意什么、为什么、哪个事业部……）"""
    t = normalize_model(question)
    for m in find_models(t):
        t = t.replace(m, "")
    t = re.sub(r"\s+", "", t).lower()
    for n in _NAMES:
        t = t.replace(n, "")
    return len(_FILLER.sub("", t)) >= 6


def find_series(text: str) -> str | None:
    return next((code for cn, code in SERIES.items() if cn in text), None)


def label(key: str) -> str:
    return DIM_LABEL.get(key) or P.LABEL.get(key) or key


def classify(question: str) -> dict:
    """规则判断路线"""
    models, keys = find_models(question), find_params(question)
    external_models = find_external_identifiers(question)
    cmp_ = _CMP.search(question)
    aggregate = bool(keys) and (bool(_SUPER.search(question)) or bool(cmp_))
    desc = bool(_DESC.search(question)) or has_extra_ask(question)
    want_bom = bool(re.search(r"BOM|物料清单|明细表|用了哪些|用到哪些|由哪些.*组成|用量", question, re.I))
    if external_models:
        # Public manufacturer codes have no structured parameter rows yet.
        # Keep their limits within document retrieval instead of ranking the internal catalogue.
        route = "DESC"
        external_models = [*models, *external_models]
        models = []
    elif (models and (keys or want_bom)) or aggregate:
        route = "MIXED" if desc else "PARAM"
    else:
        route = "DESC"
    return {"route": route, "models": models, "external_models": external_models, "param_keys": keys,
            "series": None if external_models else find_series(question), "want_bom": want_bom,
            "superlative": _SUPER.search(question).group(0) if _SUPER.search(question) else None,
            "compare": (cmp_.group(1), float(cmp_.group(2))) if cmp_ else None}


def needs_rewrite(question: str, history: list[dict]) -> bool:
    return bool(history) and (bool(_REF.search(question)) or (not find_models(question) and len(question) < 30))


def _dialogue(history: list[dict]) -> str:
    return "\n".join(f"{'用户' if h['role'] == 'user' else '助手'}：{h['text'][:300]}" for h in history[-6:]) or "（无）"


def _llm_json(name: str, prompt: str, usage: llm.Usage) -> dict:
    t, before = time.time(), usage.as_dict()
    msg = llm.invoke(prompt, usage, json_mode=True, app="kbase")
    after = usage.as_dict()
    tracing.generation(name, prompt, msg.content, {"input": after["input"] - before["input"], "output": after["output"] - before["output"]}, time.time() - t)
    return json.loads(llm.strip_fence(msg.content))


@tracing.observe("rewrite")
def rewrite(question: str, history: list[dict], summary: str, usage: llm.Usage) -> str:
    """追问改写（参数类问题用；说明类问题的改写和 HyDE 合在 rewrite_hyde 里一次做完）"""
    p = prompts.text(prompts.REWRITE).format(summary=summary or "无", history=_dialogue(history), question=question)
    try:
        return (_llm_json("改写", p, usage).get("rewritten") or "").strip() or question
    except (ValueError, AttributeError):
        return question


@tracing.observe("rewrite_hyde", output=lambda r: {"rewritten": r[0], "hyde": r[1]})
def rewrite_hyde(question: str, history: list[dict], summary: str, subject_line: str, usage: llm.Usage,
                 bilingual: bool = False) -> tuple[str, str]:
    """说明类问题：一次调用同时做改写（含指代消解、口语转术语）和 HyDE（假设答案，只用来检索）。返回（改写后的问题, 假设答案）"""
    language_instruction = ("面向英文资料检索。rewritten 保留完整中文原问题，并附准确的英文问法。"
                            "使用手册常见的英文术语，保留型号、文档号、单位、产品类型和数值条件。hyde 使用英文技术手册的写法。" if bilingual else "")
    p = prompts.text(prompts.REWRITE_HYDE).format(subject=subject_line, summary=summary or "无", history=_dialogue(history),
                                                question=question, language_instruction=language_instruction)
    out = _llm_json("改写+HyDE", p, usage)
    rewritten = str(out.get("rewritten") or "").strip() or question
    if bilingual and question not in rewritten:
        rewritten = question + "\n" + rewritten
    return rewritten, str(out.get("hyde") or "").strip()
