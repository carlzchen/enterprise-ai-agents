"""
知识图谱的小评测：只测需要跨多层关系的问题（某个物料用在哪些产品上、两个型号共用哪些物料、同系列还有哪些型号），
比较「开图谱」和「关图谱」两种情况下答对了多少。标准答案从金蝶同步过来的 BOM 表直接算出来。
用法：python -m app.kbase.eval.kg_eval
"""
import json
import random

from app.kbase import kg, store
from app.kbase.eval.dataset import PATH
from app.kbase.query import graph
from app.shared.runtime.logger import logger


def _truth():
    mats = {m["f_number"]: m for m in store.query("SELECT f_number, f_name, f_group FROM kd_material")}
    children: dict[str, set] = {}
    for b in store.query("SELECT f_parent, f_child FROM kd_bom"):
        children.setdefault(b["f_parent"], set()).add(b["f_child"])
    products = [c for c, m in mats.items() if m["f_group"].startswith("成品-")]

    def leaves(code, depth=0):
        out = set()
        for c in children.get(code, ()):
            out.add(c)
            if depth < 3:
                out |= leaves(c, depth + 1)
        return out
    uses = {p: leaves(p) for p in products}
    return mats, products, uses


def build(seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    mats, products, uses = _truth()
    used_by: dict[str, list] = {}
    for p, ms in uses.items():
        for m in ms:
            used_by.setdefault(m, []).append(p)
    cases = []
    multi = [m for m, ps in used_by.items() if 2 <= len(ps) <= 8]
    for m in rnd.sample(sorted(multi), min(8, len(multi))):
        cases.append({"question": f"物料 {m}（{mats[m]['f_name']}）用在哪些产品上？", "expect": sorted(used_by[m]), "kind": "物料用在哪些产品"})
    pairs = [(a, b) for a in products for b in products if a < b and 3 <= len(uses[a] & uses[b])]
    for a, b in rnd.sample(pairs, min(6, len(pairs))):
        cases.append({"question": f"{a} 和 {b} 共用哪些物料？", "expect": sorted(uses[a] & uses[b]), "kind": "两个型号共用的物料"})
    by_series: dict[str, list] = {}
    for p in products:
        by_series.setdefault(mats[p]["f_group"], []).append(p)
    for p in rnd.sample([p for p in products if 2 <= len(by_series[mats[p]["f_group"]]) <= 9], 6):
        cases.append({"question": f"{p} 同系列还有哪些型号？", "expect": sorted(x for x in by_series[mats[p]["f_group"]] if x != p), "kind": "同系列型号"})
    return cases


def run() -> dict:
    cases = build()
    real = kg.search
    out = {"n": len(cases), "configs": {}, "cases": []}
    for name, fn in (("开图谱", real), ("关图谱", lambda q, m: [])):
        kg.search = fn
        rows = []
        for c in cases:
            r = graph.ask(c["question"], "eval", "ADMIN", options={"no_log": True})
            found = [e for e in c["expect"] if e.upper() in r["answer"].upper()]
            rows.append({"recall": len(found) / len(c["expect"]), "ok": len(found) == len(c["expect"]), "status": r["status"], "answer": r["answer"][:300]})
        out["configs"][name] = {"全部答全的题数": sum(x["ok"] for x in rows), "平均找全比例": round(sum(x["recall"] for x in rows) / len(rows), 4)}
        for c, x in zip(cases, rows):
            c[name] = x
    kg.search = real
    out["by_kind"] = {k: {n: sum(1 for c in cases if c["kind"] == k and c[n]["ok"]) for n in ("开图谱", "关图谱")} | {"n": sum(1 for c in cases if c["kind"] == k)}
                      for k in dict.fromkeys(c["kind"] for c in cases)}
    out["cases"] = cases
    (PATH.parent / "kg_eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info(f"[kbase-eval] 图谱评测：{out['configs']} {out['by_kind']}")
    return out


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k != "cases"}, ensure_ascii=False, indent=1))
