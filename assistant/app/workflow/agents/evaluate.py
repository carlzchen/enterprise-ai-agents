"""
三种方案的对比评测：python -m app.workflow.agents.evaluate [multi single_agent single_call]

同一批非标询价，分别交给：
  single_call   一次调用：不给工具，把资料一次塞给模型
  single_agent  单 Agent：一个 Agent 拿全部工具
  multi         多 Agent：技术、成本、交期、商务 + 审查
比较：基础型号选得对不对、改动类型找得全不全、最后还剩多少「各部分对不上」的问题、说明里有没有来源不明的数字、耗时和 token。

标准答案是我按产品 BOM 和常见改型需求编的 12 个用例（没有真实的历史非标案例），只能用来比较三种方案的相对好坏，不代表真实准确率。
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from app.shared.runtime.logger import PROJECT_ROOT
from app.workflow import erp, rules, store
from app.workflow.agents import nonstd

OUT = PROJECT_ROOT / "data" / "workflow_mailbox" / "nonstd_eval.json"
CUSTOMER = 4

CASES = [
    ("c01", "交流伺服 400W 220V，轴伸要加长到 40mm（你们标准是 30mm），年用量 5000", {"series": "SERVO", "voltage_v": 220, "power_w": 400, "annual_volume": 5000}, "KL-SV-80-400W", {"SHAFT"}),
    ("c02", "750W 伺服，出线不要飞线，改成航空插头，年用量 3000", {"series": "SERVO", "voltage_v": 220, "power_w": 750, "annual_volume": 3000}, "KL-SV-80-750W", {"CONNECTOR"}),
    ("c03", "42 步进电机，引出线改成端子插头，方便我们装配，年用量 20000", {"series": "STEPPER", "frame_size": "42", "annual_volume": 20000}, "KL-ST-42x48", {"CONNECTOR"}),
    ("c04", "无刷 90W 310V，工作温度高，磁钢要用 N42SH 高温牌号，年用量 8000", {"series": "BLDC", "voltage_v": 310, "power_w": 90, "annual_volume": 8000}, "KL-BL-90W-310", {"MAGNET"}),
    ("c05", "罩极电机 18W，但要 110V 的（出口），年用量 30000", {"series": "SHADED_POLE", "voltage_v": 110, "power_w": 18, "annual_volume": 30000}, "KL-SP-YJ61-18W-220", {"WINDING"}),
    ("c06", "贯流风机 65X480，机壳安装孔位要按我们的图纸改，年用量 15000", {"series": "CROSSFLOW", "frame_size": "65", "voltage_v": 220, "annual_volume": 15000}, "KL-CF-65X480", {"HOUSING"}),
    ("c07", "减速电机 12V 12W，噪音要低，含油轴承换成滚珠轴承，年用量 40000", {"series": "GEAR", "voltage_v": 12, "power_w": 12, "annual_volume": 40000}, "KL-GR-GM37", {"BEARING"}),
    ("c08", "57 步进电机要双出轴（后端也出轴装编码器），年用量 6000", {"series": "STEPPER", "frame_size": "57", "annual_volume": 6000}, "KL-ST-57x56", {"SHAFT"}),
    ("c09", "无刷 90W 310V，每台要单独彩盒包装（我们直接上架卖），年用量 10000", {"series": "BLDC", "voltage_v": 310, "power_w": 90, "annual_volume": 10000}, "KL-BL-90W-310", {"PACKAGING"}),
    ("c10", "伺服电机 3kW 380V，年用量 800", {"series": "SERVO", "voltage_v": 380, "power_w": 3000, "annual_volume": 800}, None, set()),
    ("c11", "罩极 25W 120V，美国市场 60Hz，转速要比 50Hz 的高一些，年用量 20000", {"series": "SHADED_POLE", "voltage_v": 120, "power_w": 25, "annual_volume": 20000}, "KL-SP-YJ68-25W-120", {"WINDING"}),
    ("c12", "400W 伺服，轴伸加长到 40mm，同时出线改航空插头，年用量 5000", {"series": "SERVO", "voltage_v": 220, "power_w": 400, "annual_volume": 5000}, "KL-SV-80-400W", {"SHAFT", "CONNECTOR"}),
]


def one(case: tuple, mode: str) -> dict:
    cid, text, req, want_model, want_types = case
    req = {**req, "raw_text": text}
    sel = rules.select(req, erp.products(), erp.product_params, store.rule("selection"))
    if sel["grade"] == "EXACT":                               # 用例都应该是规则接不住的；万一接住了也照样评估
        sel = {**sel, "grade": "NEAR"}
    t0 = time.time()
    try:
        rep = nonstd.MODES[mode](req, CUSTOMER, sel)
    except Exception as e:
        return {"case": cid, "mode": mode, "error": f"{type(e).__name__}: {str(e)[:200]}", "seconds": round(time.time() - t0, 1)}
    failed = [a for a in rep.get("agents", []) if not a["ok"] and a.get("error")]
    if mode == "single_call" and failed:                   # 一次调用的方案只有一次请求，请求失败就是这个用例出错，不能算成「答错」
        return {"case": cid, "mode": mode, "error": failed[0]["error"][:200], "seconds": rep.get("seconds")}
    tech = rep.get("tech") or {}
    got_types = {c["type"] for c in tech.get("changes", [])}
    new_dev = tech.get("feasibility") == "需要新开发"
    model_ok = (want_model is None and (new_dev or not tech.get("base_model"))) or tech.get("base_model") == want_model
    return {"case": cid, "mode": mode, "model_ok": model_ok, "base_model": tech.get("base_model"), "want_model": want_model,
            "types": sorted(got_types), "want_types": sorted(want_types), "types_hit": len(got_types & want_types), "types_extra": len(got_types - want_types),
            "types_miss": len(want_types - got_types), "issues_left": len(rep.get("issues", [])), "warnings": len(rep.get("warnings", [])), "rounds": rep.get("rounds"),
            "tokens": rep.get("tokens", 0), "seconds": rep.get("seconds"), "agents_failed": sum(1 for a in rep.get("agents", []) if not a["ok"]),
            "unit_cost": (rep.get("cost") or {}).get("unit_cost"), "unit_cost_gap": rep.get("unit_cost_gap"), "warning_text": rep.get("warnings", []),
            "issue_text": [i.get("message") for i in rep.get("issues", [])]}


def main(modes: list[str]) -> dict:
    store.ensure()
    rows = []
    for mode in modes:
        with ThreadPoolExecutor(2) as pool:
            rows += list(pool.map(lambda c: one(c, mode), CASES))
    summary = {}
    for mode in modes:
        rs = [r for r in rows if r["mode"] == mode]
        ok = [r for r in rs if not r.get("error")]
        want = sum(len(c[4]) for c in CASES)
        summary[mode] = {"用例": len(rs), "出错": len(rs) - len(ok), "基础型号正确": sum(r["model_ok"] for r in ok),
                         "改动类型召回": f"{sum(r['types_hit'] for r in ok)}/{want}", "多报的改动类型": sum(r["types_extra"] for r in ok),
                         "最后仍对不上的问题": sum(r["issues_left"] for r in ok), "说明里来源不明的数字": sum(r["warnings"] for r in ok),
                         "平均耗时秒": round(sum(r["seconds"] or 0 for r in ok) / max(1, len(ok)), 1), "平均 token": int(sum(r["tokens"] for r in ok) / max(1, len(ok)))}
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


if __name__ == "__main__":
    main(sys.argv[1:] or ["single_call", "single_agent", "multi"])
