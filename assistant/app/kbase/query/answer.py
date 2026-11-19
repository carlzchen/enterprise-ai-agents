"""
生成与溯源：
  - 模型只能依据给它的资料回答，每个结论后面标 [编号]
  - 生成之后做引用校验：编号必须存在；回答里的数值必须能在被引用的资料里找到。校验不过就用更严格的提示重答一次，
    再不过就降级成"摘录原文"，不让没有依据的内容出去
  - 三种兜底：没搜到 → 请用户补充信息；搜到的不相关 → 说明并给出最接近的资料；有资料但没权限 → 明确拒答
  - 上下文预算：按 32K（Qwen3-32B）减去给回答预留的部分来装。先放固定部分（提示词、问题、锁定的主题），再放对话历史（摘要 + 最近几轮，
    有上限），剩下的按资料的先后（结构化事实在前，文档按重排分数）往里装；装不下的截断到段落边界，再装不下的丢掉并记进链路
  - 出处：每条资料标文档号、章节、页码；文档里的图片按章节挂在出处上，展示时由服务端鉴权后现签临时链接
"""
import re

from app.copilot import llm
from app.infra.config.settings import settings
from app.kbase import store
from app.kbase.query import prompts

LOW_SCORE = 0.08
_NUM = re.compile(r"(?<![\w.\[])\d+(?:\.\d+)?(?![\w\]]*\])")
_CITE = re.compile(r"\[(\d+)\]")

_IMG = re.compile(r"!\[[^\]]*\]\(kbimg://([0-9a-f]{16})\.[a-z]+\)")


# ---------------------------------------------------------------- 上下文预算

_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def tokens(text: str) -> int:
    """估算 token 数：中文和全角符号按 1 个字 1 个 token，其余按 3.5 个字符 1 个 token。
    Qwen 的分词器对中文大约 0.7~1 token/字，这样估偏保守，不会超预算"""
    if not text:
        return 0
    # PDF column alignment contains long runs of spaces, encoded compactly by model tokenizers.
    counted = re.sub(r"[ \t]{4,}", lambda m: " " * max(1, (len(m[0]) + 7) // 8), text)
    cjk = len(_CJK.findall(counted))
    return cjk + int((len(counted) - cjk) / 3.5) + 1


def _cut(text: str, budget: int) -> str:
    """截到预算以内，尽量在段落或句子边界断开"""
    if tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:                                             # 二分找最长的前缀
        mid = (lo + hi + 1) // 2
        if tokens(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    head = text[:lo]
    cut = max(head.rfind("\n\n"), head.rfind("\n"), head.rfind("。"))
    return (head[:cut + 1] if cut > lo * 0.6 else head) + "\n……（篇幅所限，后文省略）"


def pack(question: str, sources: list[dict], history: str, subject_line: str = "", budget: int | None = None,
         reserve: int | None = None, history_cap: int = 3000) -> tuple[list[dict], str, dict]:
    """按 token 预算装上下文。返回（装进去的资料，可能有截断；对话历史文本；预算明细）"""
    budget = budget or settings.kbase_context_budget
    reserve = reserve if reserve is not None else settings.kbase_answer_reserve
    fixed = tokens(prompts.text(prompts.ANSWER)) + tokens(prompts.text(prompts.STRICT)) + tokens(question) + tokens(subject_line)
    hist = history if tokens(history) <= history_cap else _cut_history(history, history_cap)
    room = budget - reserve - fixed - tokens(hist)
    used, out, dropped, truncated = 0, [], [], []
    for s_ in sources:
        label = 60                                             # 编号和出处那一行
        need = tokens(s_["context"]) + label
        if used + need <= room:
            out.append(s_)
            used += need
        elif room - used > 300:
            out.append({**s_, "context": _cut(s_["context"], room - used - label), "truncated": True})
            truncated.append(s_["index"])
            used = room
        else:
            dropped.append(s_["index"])
    for i, s_ in enumerate(out, start=1):                      # 丢掉的资料不留空号：重新编号，出处列表也跟着用新编号
        s_["index"] = i
    stats = {"budget": budget, "reserve": reserve, "fixed": fixed, "history": tokens(hist), "evidence": used, "total": fixed + tokens(hist) + used,
             "sources_in": len(out), "truncated": truncated, "dropped": dropped}
    return out, hist, stats


def _cut_history(history: str, cap: int) -> str:
    """历史太长：先丢最早的几轮，摘要（第一行）保留"""
    lines = history.split("\n")
    head, rest = (lines[:1], lines[1:]) if lines and lines[0].startswith("（更早对话的摘要") else ([], lines)
    while rest and tokens("\n".join(head + rest)) > cap:
        rest = rest[1:]
    text = "\n".join(head + rest)
    return text if tokens(text) <= cap else _cut(text, cap)


def _images_of(docs: list[dict]) -> dict[tuple, list[dict]]:
    """文档切片所在章节里的图片（只取生效版本登记的）"""
    keys = {(d["doc_key"], d["version"], d.get("parent_seq")) for d in docs if d.get("type") == "kb" and d.get("parent_seq") is not None}
    if not keys:
        return {}
    rows = store.query("SELECT doc_key, version, parent_seq, sha, summary FROM kb_image WHERE (doc_key, version) IN %s AND parent_seq IS NOT NULL",
                       (list({(k[0], k[1]) for k in keys}),))
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        if (r["doc_key"], r["version"], r["parent_seq"]) in keys:
            out.setdefault((r["doc_key"], r["version"], r["parent_seq"]), []).append({"sha": r["sha"], "summary": (r["summary"] or "")[:120]})
    return out


def build_sources(facts: list[dict], docs: list[dict]) -> list[dict]:
    out, imgs = [], _images_of(docs)
    for i, d in enumerate([*facts, *docs], start=1):
        text = _IMG.sub("[附图]", d.get("context") or d["content"])
        out.append({"index": i, "type": d["type"], "doc_key": d.get("doc_key"), "version": d.get("version"), "title": d.get("title"),
                    "section": d.get("section"), "page": d.get("page") or None, "chunk_id": d.get("chunk_id"),
                    "score": round(d["score"], 4) if isinstance(d.get("score"), float) else None, "routes": d.get("routes"),
                    "images": [{**im, "path": f"/images/{d.get('doc_key')}/{im['sha']}"} for im in imgs.get((d.get("doc_key"), d.get("version"), d.get("parent_seq")), [])[:4]],
                    "snippet": _IMG.sub("[附图]", d.get("content") or "")[:240], "context": text})
    return out


def context_text(sources: list[dict]) -> str:
    kind = {"sql": "结构化参数", "erp": "金蝶 EAS BOM", "kg": "知识图谱", "kb": "文档"}
    blocks = []
    for s in sources:
        where = (f"文档 {s['doc_key']} " if s.get("doc_key") else "") + f"《{s['title']}》" + (f" 版本 {s['version']}" if s.get("version") else "") + \
                (f" · {s['section']}" if s.get("section") else "") + (f" · 第 {s['page']} 页" if s.get("page") else "")
        blocks.append(f"[{s['index']}]（{kind.get(s['type'], '资料')}：{where}）\n{s['context']}")
    return "\n\n".join(blocks)


def prompt(question: str, sources: list[dict], history: str, problem: str = "", subject_line: str = "") -> str:
    return prompts.text(prompts.ANSWER).format(strict=prompts.text(prompts.STRICT).format(problem=problem) if problem else "", subject=subject_line,
                                               history=history or "（无）", context=context_text(sources), question=question)


def no_answer(answer: str) -> bool:
    """模型看完资料后表示答不了（开头就说没有找到）。后面即使顺带提了几条相近的资料，也算没答上"""
    return bool(re.match(r"^.{0,60}?(没有找到|未找到|没有查到|无法回答)", answer.strip(), re.S))


def verify(answer: str, sources: list[dict], question: str = "") -> tuple[bool, str]:
    """引用校验。返回（是否通过, 问题说明）。用户问题里本来就有的数字（「80 万元的订单」）回答里复述不算无出处"""
    if no_answer(answer):
        return True, ""
    cited = {int(n) for n in _CITE.findall(answer)}
    valid = {s["index"] for s in sources}
    if not cited:
        return False, "回答没有标注任何出处"
    if cited - valid:
        return False, f"引用了不存在的编号 {sorted(cited - valid)}"
    pool = re.sub(r"[\s,，]", "", " ".join(s["context"] for s in sources if s["index"] in cited))
    asked = re.sub(r"[\s,，]", "", question or "")
    loose = []
    for m in _NUM.finditer(_CITE.sub("", answer)):
        n = m.group(0)
        if len(n.replace(".", "")) < 2:                       # 一位数太容易巧合，不查
            continue
        if n not in pool and n.rstrip("0").rstrip(".") not in pool and n not in asked:
            loose.append(n)
    if loose:
        return False, f"数值 {'、'.join(loose[:5])} 在引用的资料里找不到"
    return True, ""


def extractive(sources: list[dict]) -> str:
    """降级回答：不让模型组织语言，直接摘录原文"""
    lines = ["没能生成可靠的回答，下面是检索到的原文摘录，请直接查看："]
    for s in sources[:4]:
        lines.append(f"- 《{s['title']}》{' · ' + s['section'] if s.get('section') else ''}：{s['snippet'][:160]} [{s['index']}]")
    return "\n".join(lines)


def no_result(models: list[str]) -> str:
    hint = "" if models else "可以补充具体的型号（比如 KL-SV-80-750W）、"
    return f"知识库里没有找到和这个问题相关的内容。{hint}换一个说法，或者告诉我你想查的是规格参数、图纸尺寸、工艺要求还是故障处理。"


def low_relevance(sources: list[dict]) -> str:
    near = "；".join(f"《{s['title']}》{s.get('section') or ''}" for s in sources[:3])
    return f"没有找到能直接回答这个问题的资料，所以不作答。最接近的内容是：{near}。可以打开原文确认，或者把问题问得更具体一些。"


def denied() -> str:
    return "和这个问题相关的资料在知识库里是有的，但你的角色没有查看权限，所以不能回答。需要查看的话，请联系文档负责人或管理员。"


def generate(question: str, sources: list[dict], history: str, usage: llm.Usage, on_delta=None, problem: str = "", subject_line: str = "") -> str:
    p = prompt(question, sources, history, problem, subject_line)
    draft = llm.stream(p, on_delta, usage, app="kbase") if on_delta else (llm.invoke(p, usage, app="kbase").content or "").strip()
    if sources and re.search(r"[A-Za-z]", question) and not re.search(r"KL-", question, re.I):
        try:
            return review(question, sources, draft, usage, problem)
        except Exception:
            return draft
    return draft


def review(question: str, sources: list[dict], draft: str, usage: llm.Usage, problem: str = "") -> str:
    """Check a technical answer against the same retrieved evidence, without reference answers."""
    p = ("你是技术手册回答的复核编辑。只根据给出的资料核对初稿，输出最终中文回答并保留有效[编号]引用，不输出审查过程。\n"
         "逐项检查对象、表格行列、单位、适用配置以及是否完整回答问题。初稿正确则保留，存在错误则改正。"
         "接线图直接连接指定端子的电压标注可以回答电源范围；明确适用该端子的通用输出额定值可以回答端子电流。"
         "不要因为原文没有重复问题的措辞而拒答。产品线概览和系列导航中直接对应系列名称的分辨率标注也可作为依据，核对标签的归属即可；不能用邻近但不适用的型号数值补答案。"
         "只报告原文中的各对象数值，不添加相减结果、比例、单位换算或原文未写出的数字，比较时并列给出原值即可。公差的上下偏差与毫米/英寸换算分开，0不能漏掉。覆盖表圆点含义按脚注，不忽略空心圆。"
         "系列包含多种电源或减速机配置时按证据注明差异，不将单一配置泛化；只回答所问内容。"
         "资料不足才明确说明缺少什么，不能编数值。\n\n问题：" + question + "\n\n资料：" + context_text(sources) + "\n\n初稿：" + draft)
    if problem:
        p += "\n\n引用校验反馈：" + problem + "。请特别修复这一问题。每个数值引用实际含有该数值的资料编号；仅并列原文数值，不计算差值或换算单位。"
    return (llm.invoke(p, usage, app="kbase").content or draft).strip()
