"""评测里用到的计算，全部是纯函数（有单元测试）"""
from collections import Counter


def ratio(n: int, d: int) -> dict:
    return {"n": n, "d": d, "rate": round(n / d, 4) if d else None}


def fmt(r: dict | None) -> str:
    """报告里的写法：96.7%（58/60）；分母为 0 写「—」"""
    if not r or not r["d"]:
        return "—"
    return f"{r['rate'] * 100:.1f}%（{r['n']}/{r['d']}）"


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": None if p is None else round(p, 4), "recall": None if r is None else round(r, 4),
            "f1": None if f is None else round(f, 4)}


def multiset_prf(pred: list, gold: list) -> tuple[int, int, int]:
    """两个多重集合的 tp / fp / fn"""
    p, g = Counter(pred), Counter(gold)
    tp = sum((p & g).values())
    return tp, sum(p.values()) - tp, sum(g.values()) - tp


def per_label(pairs: list[tuple[list, list]], key=lambda x: x) -> dict[str, dict]:
    """[(预测, 标准)] → 每个标签的 P / R / F1"""
    acc: dict[str, list[int]] = {}
    for pred, gold in pairs:
        labels = {key(x) for x in pred} | {key(x) for x in gold}
        for lab in labels:
            tp, fp, fn = multiset_prf([x for x in pred if key(x) == lab], [x for x in gold if key(x) == lab])
            a = acc.setdefault(str(lab), [0, 0, 0])
            a[0] += tp
            a[1] += fp
            a[2] += fn
    return {k: prf(*v) for k, v in sorted(acc.items())}


def micro(per: dict[str, dict]) -> dict:
    return prf(sum(v["tp"] for v in per.values()), sum(v["fp"] for v in per.values()), sum(v["fn"] for v in per.values()))


def num_eq(a, b, tol: float = 1e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def norm_value(v) -> str:
    """变更比对里的值：数字统一成不带多余零的写法，日期和文本原样"""
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v or "").strip()


def merge(parts: list[dict]) -> dict:
    """几个数据集的 ratio / prf 结果相加（报告里的「留出集合计」）"""
    if not parts:
        return {}
    out = {}
    for k, v in parts[0].items():
        if isinstance(v, dict) and set(v) == {"n", "d", "rate"}:
            n, d = sum(p[k]["n"] for p in parts if k in p), sum(p[k]["d"] for p in parts if k in p)
            out[k] = ratio(n, d)
        elif isinstance(v, dict) and {"tp", "fp", "fn"} <= set(v):
            out[k] = prf(*(sum(p[k][x] for p in parts if k in p) for x in ("tp", "fp", "fn")))
        elif isinstance(v, dict):
            keys = {kk for p in parts if isinstance(p.get(k), dict) for kk in p[k]}
            sub = [p[k] for p in parts if isinstance(p.get(k), dict)]
            out[k] = merge([{kk: x.get(kk) for kk in keys} | x for x in sub]) if sub else {}
        elif isinstance(v, int) and not isinstance(v, bool):                 # 计数（邮件数、出错数……）相加；比例和耗时不加
            out[k] = sum(p.get(k) or 0 for p in parts if isinstance(p.get(k), int))
    return out
