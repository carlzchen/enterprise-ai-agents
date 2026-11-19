"""
评测数据集。都由 scripts/workflow_gen_mock.py 生成，ERP 世界（客户、料号映射、价目表、在途订单）同一份（种子 42），只是邮件不同：

  dev        data/workflow_mock                      邮件种子 42。开发阶段 1–3 一直在用，规则和提示词是对着它调的
  test_2027  data/workflow_eval/sets/test_2027       邮件种子 2027，留出集：开发时没看过
  test_2028  data/workflow_eval/sets/test_2028       邮件种子 2028，留出集

正式指标看留出集（test_*）；dev 一起报，用来看「在调过的数据上」和「没见过的数据上」差多少。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.shared.runtime.logger import PROJECT_ROOT

SETS = {"dev": PROJECT_ROOT / "data" / "workflow_mock",
        "test_2027": PROJECT_ROOT / "data" / "workflow_eval" / "sets" / "test_2027",
        "test_2028": PROJECT_ROOT / "data" / "workflow_eval" / "sets" / "test_2028"}
HELD_OUT = ("test_2027", "test_2028")


@dataclass
class Dataset:
    name: str
    root: Path
    emails: list[dict]
    pos: dict[str, dict]
    manifest: dict
    by_id: dict[str, dict] = field(default_factory=dict)

    def raw(self, email_id: str) -> bytes:
        return (self.root / self.by_id[email_id]["file"]).read_bytes()

    def attachment(self, key: str) -> bytes:
        return (self.root / "attachments" / key).read_bytes()

    @property
    def held_out(self) -> bool:
        return self.name in HELD_OUT


def load(name: str) -> Dataset:
    root = SETS[name]
    if not (root / "ground_truth" / "emails.json").exists():
        raise FileNotFoundError(f"数据集 {name} 不存在，先运行 scripts/workflow_gen_mock.py（见 datasets.py 的说明）")
    emails = json.loads((root / "ground_truth" / "emails.json").read_text(encoding="utf-8"))
    pos = json.loads((root / "ground_truth" / "pos.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return Dataset(name=name, root=root, emails=emails, pos=pos, manifest={k: v for k, v in manifest.items() if k != "files"},
                   by_id={e["id"]: e for e in emails})


def by_customer(emails: list[dict]) -> list[list[dict]]:
    """按客户分组（组内保持时间顺序）。不同客户的 PO 版本互不影响，可以并行跑；同一客户必须按顺序跑"""
    groups: dict = {}
    for e in emails:
        groups.setdefault(e["customer_id"] or f"none-{e['id']}", []).append(e)
    return list(groups.values())
