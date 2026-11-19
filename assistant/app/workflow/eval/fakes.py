"""
按标准答案 / 剧本作答的假模型。用在两处：
  - tests/：测图和评测的「管道」本身对不对，不依赖真实大模型
  - 评测的 --fake 模式：流程自检。假模型模式跑出来的数字只说明评测代码能跑通，不是效果指标，报告里会写明

注意：假模型拿的是标准答案，所以 --fake 模式下路由、抽取这些环节必然全对；真正有意义的是代码负责的部分（附件配对、规则校验、
变更比对、审核规则、审批前不写）在标准答案输入下是否给出标准答案输出。
"""
import json
import random
import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.workflow import mailbox
from app.workflow.eval.datasets import Dataset


class GTSplit:
    """Supervisor 的假模型：按标准答案给子任务，但不给附件名、顺序打乱（附件配对必须由代码完成）"""

    def __init__(self, ds: Dataset, shuffle: bool = True):
        self.ds, self.shuffle = ds, shuffle
        self.index: dict[tuple[str, str], list[dict]] = {}
        for e in ds.emails:
            self.index.setdefault((e["subject"], e["from"]), []).append(e)
        self._bodies: dict[str, str] = {}

    def _body(self, e: dict) -> str:
        if e["id"] not in self._bodies:
            self._bodies[e["id"]] = (mailbox.parse(self.ds.raw(e["id"]))["body"] or "").strip()[:200]
        return self._bodies[e["id"]]

    def email_of(self, prompt: str) -> dict:
        subject = prompt.split("主题：", 1)[1].split("\n", 1)[0].strip()
        sender = prompt.split("发件人：", 1)[1].split("\n", 1)[0].strip()
        cands = self.index[(subject, sender)]
        return cands[0] if len(cands) == 1 else next(e for e in cands if self._body(e) in prompt)

    def __call__(self, prompt: str) -> str:
        e = self.email_of(prompt)
        out = []
        for st in e["subtasks"]:
            r = st["refs"]
            refs = {"po_no": r.get("po_no") or r.get("customer_po"), "order_no": r["order_no"] if r.get("asked_by") == "order_no" else None,
                    "customer_part_no": r.get("customer_part_no"), "requested_date": r.get("requested_date"), "params": r.get("params") or {},
                    "annual_volume": r.get("annual_volume")}
            out.append({"type": st["type"], "summary": st["type"], "refs": refs})
        if self.shuffle:
            random.Random(e["id"]).shuffle(out)
        return json.dumps({"subtasks": out}, ensure_ascii=False)


class GTExtract:
    """订单抽取的假模型（英文版式走大模型时才用到）：按文件名找标准答案，同名文件再按 PO 号和料号区分"""

    def __init__(self, ds: Dataset):
        self.by_name: dict[str, list[dict]] = {}
        for k, p in ds.pos.items():
            self.by_name.setdefault(k.split("/", 1)[1], []).append(p)

    def __call__(self, prompt: str) -> str:
        name = prompt.split("PO 文件名：", 1)[1].split("\n", 1)[0].strip()
        cands = self.by_name[name]
        p = cands[0] if len(cands) == 1 else next(c for c in cands if c["po_no"] in prompt and all(ln["customer_part_no"] in prompt for ln in c["lines"]))
        return json.dumps({"header": {"po_no": p["po_no"], "order_date": p["order_date"], "currency": p["currency"], "total_amount": p["printed_total"]},
                           "lines": [{"line_no": ln["line_no"], "customer_part_no": ln["customer_part_no"], "description": ln["desc"], "qty": ln["qty"],
                                      "unit_price": ln["unit_price"], "amount": ln["amount"], "delivery_date": ln["delivery_date"]} for ln in p["lines"]]})


def sections_of(prompt: str) -> str:
    """从合成回复的提示词里取出各子任务的段落，原样拼起来（相当于一个老实的写手）"""
    secs = prompt.split("各件事的结论：", 1)[1]
    parts = re.split(r"\n?\[S\d+ \w+\]\n", secs)
    return "您好，\n\n" + "\n\n".join(p.strip() for p in parts if p.strip()) + "\n\n科朗销售部"


class Writer:
    """Supervisor + 合成回复共用的假模型（两者都走 llm_json）。bad(round) 返回 True 的轮次往草稿里加一句 poison"""

    def __init__(self, split: GTSplit, bad=lambda n: False, poison: str = ""):
        self.split, self.rounds, self.bad, self.poison = split, 0, bad, poison

    def __call__(self, prompt: str) -> str:
        if "把这封邮件要我们做的事拆成子任务" in prompt:
            return self.split(prompt)
        body = sections_of(prompt)
        if self.bad(self.rounds):
            body = body.replace("\n\n科朗销售部", "\n\n" + self.poison + "\n\n科朗销售部")
        self.rounds += 1
        return json.dumps({"subject": "Re: 回复", "body": body}, ensure_ascii=False)


class DeliveryModel:
    """交付 Agent 的剧本模型：找单 → 查状态 →（加急）程序判断 → 不能满足就派计划部 → 交卷。段落只用工具结果里的数字"""

    def invoke(self, messages, tools):
        task = next(m.content for m in messages if isinstance(m, HumanMessage))
        po = re.search(r"PO 号 (\S+?)，", task)[1]
        order_no = re.search(r"我方订单号 (\S+?)，", task)[1]
        req = re.search(r"要求日期 (\S+)\n", task)[1]
        results = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
        n, names = len(results), {t["function"]["name"] for t in tools}
        call = lambda name, args: AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{n}"}])
        if "find_order" in names and n == 0:
            return call("find_order", {"customer_po": None if po == "无" else po, "order_no": None if order_no == "无" else order_no})
        found = results[0].get("orders", [{}])[0].get("order_no") if results else None
        if found and n == 1:
            return call("get_order_status", {"order_no": found})
        expedite = req != "无"
        if found and expedite and n == 2:
            return call("check_expedite", {"order_no": found, "requested_date": req})
        chk = results[2] if expedite and n >= 3 else {}
        if found and expedite and not chk.get("confirmable_date") and n == 3:
            return call("create_task", {"role": "PRODUCTION", "title": f"评估订单 {found} 能否提前"})
        st = results[1] if n >= 2 else {}
        if not found:
            ans = {"found": False, "section": "您提到的订单我们正在核实，稍后回复您。", "needs_human": True}
        elif expedite and chk.get("confirmable_date"):
            ans = {"found": True, "order_no": found, "can_meet_requested": "yes", "committed_date": chk["confirmable_date"],
                   "section": f"订单 {found} 可以按您要求的 {chk['confirmable_date']} 安排发货。"}
        elif expedite:
            ans = {"found": True, "order_no": found, "can_meet_requested": "no", "section": f"订单 {found} 提前交货的要求已安排计划部评估，我们会尽快回复您。"}
        else:
            ans = {"found": True, "order_no": found, "section": f"订单 {found} 目前状态为 {st['status']}，原交期 {st['required_date']}。"}
        return call("submit", ans)
