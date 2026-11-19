"""
批量导入：uv run python -m app.kbase.ingest.batch [plm|samples|all]
  plm      从 PLM 同步现行文档（文档号做 doc_key，PLM 版本做 version）
  samples  导入 data/samples（PDF 手册）和 data/kbase_samples（选型表、工艺规程、培训材料、图纸）
密级和可见范围按文档类型给默认值；真实环境里应该来自 PLM 的密级字段。
"""
import sys
from pathlib import Path

from app.infra.erp.minierp_client import minierp_client
from app.kbase.ingest import graph
from app.shared.runtime.logger import PROJECT_ROOT, logger

OWNER = "{}@kb-demo.example.com"
# 文档类型 → (密级, 可见角色)
POLICY = {
    "DATASHEET": ("PUBLIC", "ALL"),
    "MANUAL": ("PUBLIC", "ALL"),
    "SELECTION": ("PUBLIC", "ALL"),
    "SOP": ("INTERNAL", "PRODUCTION,QUALITY,ENGINEER,MANAGEMENT"),
    "PROCESS": ("INTERNAL", "PRODUCTION,QUALITY,ENGINEER,MANAGEMENT"),
    "INSPECTION": ("INTERNAL", "QUALITY,ENGINEER,PRODUCTION,PURCHASE,MANAGEMENT"),
    "TRAINING": ("INTERNAL", "SALES,ENGINEER,QUALITY,MANAGEMENT"),
    "DRAWING": ("CONFIDENTIAL", "ENGINEER,QUALITY,MANAGEMENT"),
    "POLICY": ("INTERNAL", "ALL"),                         # 公司制度：内部资料，全员可看（个别制度在清单里单独收窄）
}


def meta_for(title: str, doc_type: str, source: str, owner: str = "eng01", **extra) -> dict:
    level, acl = POLICY[doc_type]
    return {"title": title, "doc_type": doc_type, "source": source, "security_level": level, "acl_groups": acl,
            "owner_email": OWNER.format(owner), **extra}


def import_plm() -> list[dict]:
    out = []
    for d in minierp_client.current_documents():
        target = PROJECT_ROOT / "output" / "plm" / d["doc_no"] / f"v{d['revision']}" / d["file_name"]
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(minierp_client.download(d["id"]))
        meta = meta_for(d["title"], d["doc_type"], "PLM", owner=d.get("uploaded_by") or "eng01",
                        product_code=d.get("product_code"), series=d.get("series"))
        out.append(graph.ingest(str(target), d["doc_no"], str(d["revision"]), meta))
    return out


SAMPLES = [
    ("data/samples/KL系列交流伺服电机选型手册.pdf", "MAN-SV-SELECT", "1", "KL系列交流伺服电机选型手册", "MANUAL", {"series": "SERVO"}),
    ("data/samples/KD系列伺服驱动器报警代码手册.pdf", "MAN-KD-ALARM", "1", "KD系列伺服驱动器报警代码手册", "MANUAL", {"series": "SERVO"}),
    ("data/samples/步进电机与驱动器应用指南.pdf", "MAN-ST-GUIDE", "1", "步进电机与驱动器应用指南", "MANUAL", {"series": "STEPPER"}),
    ("data/kbase_samples/KL系列电机选型表.xlsx", "SEL-KL-2026", "1", "KL系列电机选型表", "SELECTION", {}),
    ("data/kbase_samples/伺服电机定子嵌线与浸漆工艺规程.docx", "WI-SV-012", "2", "伺服电机定子嵌线与浸漆工艺规程", "PROCESS", {"series": "SERVO"}),
    ("data/kbase_samples/伺服系统常见故障与售后处理培训.pptx", "TRN-SV-001", "1", "伺服系统常见故障与售后处理培训", "TRAINING", {"series": "SERVO"}),
]


def import_samples() -> list[dict]:
    out = []
    for rel, key, ver, title, kind, extra in SAMPLES:
        out.append(graph.ingest(str(PROJECT_ROOT / rel), key, ver, meta_for(title, kind, "FOLDER", **extra)))
    for f in sorted((PROJECT_ROOT / "data" / "kbase_samples" / "drawings").glob("*.dxf")):
        out.append(graph.ingest(str(f), f"DWG-{f.stem}-01", "A", meta_for(f"{f.stem} 安装尺寸图", "DRAWING", "FOLDER", series="SERVO")))
    return out


def main(what: str = "all") -> None:
    rows = (import_plm() if what in ("plm", "all") else []) + (import_samples() if what in ("samples", "all") else [])
    ok = sum(r["status"] == "DONE" for r in rows)
    skip = sum(r["status"] == "SKIPPED" for r in rows)
    logger.info(f"[kbase] 批量导入完成：{len(rows)} 个文件，成功 {ok}，跳过 {skip}，其他 {len(rows) - ok - skip}")
    for r in rows:
        if r["status"] not in ("DONE", "SKIPPED"):
            print("  !!", r["doc_key"], r["status"], r["stage"], r["error"])
    print({"total": len(rows), "done": ok, "skipped": skip, "chunks": sum(r["chunks"] for r in rows), "params": sum(r["params"] for r in rows)})


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "all")
