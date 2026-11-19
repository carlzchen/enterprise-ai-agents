"""
评测集生成：分层抽样，约 300 题，标准答案都来自知识库里的原始数据，不是凭印象写的。
  PARAM  参数题：从结构化参数表抽（型号 + 参数名 → 数值），答案就是表里的值
  DESC   说明题：按文档类型分层抽切片，让大模型针对这一段写一个问题和参考答案
  MIXED  混合题：同一型号的一个参数 + 规格书里的一段说明
  REFUSE 拒答题：知识库里没有的内容、敏感内容、没权限看的内容，正确的表现是不回答
生成一次后存成 data/kbase_eval/dataset.json，之后每次评测用同一份，结果才能前后对比。
"""
import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.copilot import llm
from app.kbase import store
from app.shared.runtime.logger import PROJECT_ROOT, logger

PATH = PROJECT_ROOT / "data" / "kbase_eval" / "dataset.json"
QUOTA = {"DATASHEET": 38, "INSPECTION": 24, "MANUAL": 22, "DRAWING": 14, "SOP": 12, "PROCESS": 8, "SELECTION": 6, "TRAINING": 6}   # 说明题各类型配额
_ASK = ["{m} 的{p}是多少？", "{m}{p}多少", "请问 {m} 这款的{p}是多少", "查一下 {m} 的{p}"]

_GEN = """你在给一个电机制造企业的知识库出评测题。下面是知识库里的一段原文，出自《{title}》的「{section}」。

原文：
{content}

请针对这段原文出一道题：
1. 问题要像一线员工（销售、工程师、质检、售后）平时会问的话，口语化，一句话
2. 问题必须能独立看懂：要带上具体的型号、文档主题或对象（比如"{subject}"），不能出现"这段话""本文""上述"
3. 只用这段原文就能完整回答，不需要别的资料
4. 不要问单个参数的数值（比如额定转速是多少），要问做法、要求、原因、注意事项、适用场景、步骤、判定标准这一类
5. 参考答案完全依据原文，简明完整，不要添加原文没有的内容

只输出 JSON：{{"question": "...", "answer": "..."}}"""

_OUT_OF_SCOPE = [
    "今天深圳天气怎么样", "帮我写一首关于春天的诗", "科朗今天的股价是多少", "公司年假有几天", "食堂今天中午吃什么",
    "KL-SV-130-3KW 的额定转矩是多少", "KL-SV-180-7.5KW 的额定电流多少", "你们的伺服电机能用在核潜艇上吗，具体参数是多少",
    "竞争对手汇川的 SV660 驱动器报警 Er.210 怎么处理", "特斯拉 Model 3 的驱动电机功率是多少", "帮我算一下个人所得税",
    "KL-XYZ-999 的防护等级是多少", "公司去年的净利润是多少", "怎么申请出差报销", "推荐一部好看的电影"]


def _params(rnd: random.Random, n: int) -> list[dict]:
    rows = store.query(
        "SELECT p.model, p.param_key, p.param_name, MIN(p.value_text) v, MIN(p.unit) unit, COUNT(DISTINCT p.value_text) kinds, "
        "GROUP_CONCAT(DISTINCT p.doc_key) docs FROM kb_param p JOIN kb_document d ON d.doc_key = p.doc_key AND d.active_version = p.version "
        "GROUP BY p.model, p.param_key, p.param_name HAVING kinds = 1")
    by_key: dict[str, list] = {}
    for r in rows:
        by_key.setdefault(r["param_key"], []).append(r)
    picked = []
    while len(picked) < n and by_key:                         # 各参数轮流抽，避免全是额定转速
        for k in list(by_key):
            if not by_key[k]:
                del by_key[k]
                continue
            picked.append(by_key[k].pop(rnd.randrange(len(by_key[k]))))
            if len(picked) == n:
                break
    out = []
    for i, r in enumerate(picked):
        value = r["v"] + (f" {r['unit']}" if r["unit"] and r["unit"] not in r["v"] else "")
        out.append({"id": f"P{i + 1:03d}", "kind": "PARAM", "role": "ADMIN", "question": _ASK[i % len(_ASK)].format(m=r["model"], p=r["param_name"]),
                    "ground_truth": f"{r['model']} 的{r['param_name']}是 {value}。", "expected": r["v"], "model": r["model"], "param_key": r["param_key"],
                    "gold_docs": r["docs"].split(",")})
    return out


def _chunks() -> list[dict]:
    return store.query(
        "SELECT c.chunk_id, c.parent_id, c.doc_key, c.section, c.item_name, c.content, d.title, d.doc_type, d.acl_groups FROM kb_chunk c "
        "JOIN kb_document d ON d.doc_key = c.doc_key AND d.active_version = c.version WHERE CHAR_LENGTH(c.content) >= 60 "
        "AND c.section NOT IN ('变更记录', '1. 主要技术参数') AND c.section NOT LIKE '结构化参数%%'")


def _write(c: dict) -> dict | None:
    try:
        msg = llm.invoke(_GEN.format(title=c["title"], section=c["section"], content=c["content"][:1800], subject=c["item_name"] or c["title"]), json_mode=True)
        d = json.loads(msg.content)
        q, a = str(d["question"]).strip(), str(d["answer"]).strip()
        return {"question": q, "ground_truth": a} if len(q) >= 6 and len(a) >= 4 else None
    except Exception as e:
        logger.warning(f"[kbase-eval] 出题失败 {c['chunk_id']}：{e}")
        return None


def build(seed: int = 20261007) -> list[dict]:
    rnd = random.Random(seed)
    cases = _params(rnd, 110)

    by_type: dict[str, list] = {}
    for c in _chunks():
        by_type.setdefault(c["doc_type"], []).append(c)
    for v in by_type.values():
        rnd.shuffle(v)
    desc_src = [c for t, n in QUOTA.items() for c in by_type.get(t, [])[:n]]
    used = {c["chunk_id"] for c in desc_src}
    mixed_src = [c for c in by_type.get("DATASHEET", []) if c["chunk_id"] not in used and c["item_name"]][:30]
    used |= {c["chunk_id"] for c in mixed_src}
    denied_src = [c for t in ("INSPECTION", "SOP", "DRAWING", "PROCESS") for c in by_type.get(t, [])
                  if c["chunk_id"] not in used and "ALL" not in c["acl_groups"] and "SALES" not in c["acl_groups"]][:15]

    with ThreadPoolExecutor(8) as pool:
        written = list(pool.map(_write, [*desc_src, *mixed_src, *denied_src]))
    w_desc, w_mixed, w_denied = written[:len(desc_src)], written[len(desc_src):len(desc_src) + len(mixed_src)], written[len(desc_src) + len(mixed_src):]

    gold = lambda c: {"gold_docs": [c["doc_key"]], "gold_parent": c["parent_id"], "gold_chunk": c["chunk_id"], "doc_type": c["doc_type"]}
    n = 0
    for c, w in zip(desc_src, w_desc):
        if w:
            n += 1
            cases.append({"id": f"D{n:03d}", "kind": "DESC", "role": "ADMIN", **w, **gold(c)})
    param_of = {}
    for r in store.query("SELECT p.model, p.param_name, p.value_text v, p.unit, p.doc_key FROM kb_param p JOIN kb_document d ON d.doc_key = p.doc_key "
                         "AND d.active_version = p.version AND d.doc_type = 'DATASHEET'"):
        param_of.setdefault(r["model"], []).append(r)
    n = 0
    for c, w in zip(mixed_src, w_mixed):
        ps = param_of.get(c["item_name"])
        if not w or not ps:
            continue
        p = rnd.choice(ps)
        value = p["v"] + (f" {p['unit']}" if p["unit"] and p["unit"] not in p["v"] else "")
        n += 1
        cases.append({"id": f"M{n:03d}", "kind": "MIXED", "role": "ADMIN", "question": f"{c['item_name']} 的{p['param_name']}是多少？另外，{w['question']}",
                      "ground_truth": f"{c['item_name']} 的{p['param_name']}是 {value}。{w['ground_truth']}", "expected": p["v"], "model": c["item_name"], **gold(c)})
    for i, q in enumerate(_OUT_OF_SCOPE, start=1):
        cases.append({"id": f"R{i:03d}", "kind": "REFUSE", "sub": "知识库里没有", "role": "SALES", "question": q, "ground_truth": ""})
    n = len(_OUT_OF_SCOPE)
    for c, w in zip(denied_src, w_denied):
        if w:
            n += 1
            cases.append({"id": f"R{n:03d}", "kind": "REFUSE", "sub": "没有权限", "role": "SALES", "question": w["question"], "ground_truth": "", **gold(c)})
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {k: sum(1 for c in cases if c["kind"] == k) for k in ("PARAM", "DESC", "MIXED", "REFUSE")}
    logger.info(f"[kbase-eval] 评测集已生成：{len(cases)} 题 {kinds} → {PATH}")
    return cases


def load() -> list[dict]:
    return json.loads(PATH.read_text(encoding="utf-8")) if Path(PATH).exists() else build()


if __name__ == "__main__":
    build()
