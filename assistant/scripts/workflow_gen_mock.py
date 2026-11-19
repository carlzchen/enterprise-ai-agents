"""
一条命令生成跟单多 Agent 的全部模拟数据（SPEC 阶段 1）：

    PYTHONPATH=. .venv/bin/python scripts/workflow_gen_mock.py              # 写补充数据表 + 生成文件
    PYTHONPATH=. .venv/bin/python scripts/workflow_gen_mock.py --no-db      # 只生成文件（不改数据库）
    PYTHONPATH=. .venv/bin/python scripts/workflow_gen_mock.py --no-db --email-seed 2027 --out data/workflow_eval/sets/test_2027   # 留出的评测集

输出到 data/workflow_mock/：inbox/*.eml、attachments/（PO 的 PDF / Excel）、ground_truth/（emails.json、pos.json）、manifest.json（统计 + 每个文件的 sha256）。
种子固定（WORKFLOW_MOCK_SEED，默认 42），同样的 ERP 数据 → 字节完全相同的文件。每次运行先清空输出目录。
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infra.config.settings import settings  # noqa: E402
from app.shared.runtime.logger import PROJECT_ROOT  # noqa: E402
from app.workflow.mockdata import emails, world  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "data" / "workflow_mock"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=settings.workflow_mock_seed)
    ap.add_argument("--email-seed", type=int, default=None, help="邮件用另一个种子（ERP 世界仍按 --seed）：生成留出的评测集，和开发集共用同一份补充数据")
    ap.add_argument("--no-db", action="store_true", help="只生成文件，不写补充数据表")
    a = ap.parse_args()
    w = world.load(a.seed)
    seeded = {} if a.no_db else world.seed_db(w)
    if a.out.exists():
        shutil.rmtree(a.out)
    a.out.mkdir(parents=True)
    m = emails.generate(w, a.email_seed if a.email_seed is not None else a.seed, a.out)
    print(json.dumps({"db": seeded or "跳过", **{k: v for k, v in m.items() if k != "files"}, "files": len(m["files"]), "out": str(a.out)},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
