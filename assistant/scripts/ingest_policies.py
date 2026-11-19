"""
把公司制度（演示用模拟文件）批量导入知识库：PYTHONPATH=. .venv/bin/python scripts/ingest_policies.py
  data/kbase_samples/policies_keli/<类别>/*.md   七大类制度（公司治理、财务、采购供应链、销售、研发质量生产、内控审计、证券事务）
  data/policies/*.md                              已有的人事和费用类制度
文档类型 POLICY，默认「内部 · 全员可看」；内部审计、内幕信息相关的只给管理层和财务。
任务交给知识库服务的批量入库队列（RabbitMQ），由服务里的消费线程执行；用 GET /batches/<批次号> 看进度。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.kbase.ingest import batchjob  # noqa: E402
from app.kbase.ingest.batch import meta_for  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "data"
CATS = {"01": "GOV", "02": "FIN", "03": "SCM", "04": "SAL", "05": "RQP", "06": "ICA", "07": "SEC"}
RESTRICTED = {"内部审计制度": "MANAGEMENT,FINANCE", "内幕信息知情人登记管理制度": "MANAGEMENT,FINANCE"}
OWNER = {"GOV": "sec01", "FIN": "fin01", "SCM": "buyer01", "SAL": "sales01", "RQP": "eng01", "ICA": "aud01", "SEC": "sec01", "HR": "hr01"}


def version_of(text: str) -> str:
    m = re.search(r"版本[：:]\s*V?(\d+(?:\.\d+)?)", text)
    return m.group(1) if m else "1.0"


def items() -> list[dict]:
    out = []
    for folder in sorted((ROOT / "kbase_samples" / "policies_keli").iterdir()):
        if not folder.is_dir():
            continue
        cat = CATS[folder.name[:2]]
        for i, f in enumerate(sorted(folder.glob("*.md")), start=1):
            title = f.stem
            meta = meta_for(title, "POLICY", "UPLOAD", owner=OWNER[cat])
            if title in RESTRICTED:
                meta["acl_groups"] = RESTRICTED[title]
            out.append({"file_path": str(f), "doc_key": f"KL-POL-{cat}-{i:02d}", "version": version_of(f.read_text(encoding="utf-8")[:400]), "meta": meta})
    for i, f in enumerate(sorted((ROOT / "policies").glob("*.md")), start=1):
        out.append({"file_path": str(f), "doc_key": f"KL-POL-HR-{i:02d}", "version": version_of(f.read_text(encoding="utf-8")[:400]),
                    "meta": meta_for(f.stem, "POLICY", "UPLOAD", owner=OWNER["HR"])})
    return out


if __name__ == "__main__":
    its = items()
    print(f"共 {len(its)} 份制度")
    r = batchjob.create("MANUAL", its, "演示数据导入", "公司规章制度")
    print(r)
