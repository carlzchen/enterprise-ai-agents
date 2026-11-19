"""
把金蝶 EAS 的物料和 BOM 同步到本地镜像表：uv run python -m app.kbase.kingdee.sync

为什么要镜像而不是每次现查：入库时要做"图纸明细表 vs ERP BOM"的交叉校验，问答时要按型号取 BOM，
都是高频的小查询，现查 EAS 的 WebService 又慢又占许可。镜像表每天全量刷新一次，查询全部走本地。
"""
from datetime import datetime

from app.kbase import store
from app.kbase.kingdee import forms
from app.kbase.kingdee.client import kingdee
from app.shared.runtime.logger import logger


def sync_all() -> dict:
    now = datetime.now().replace(microsecond=0)        # 去掉微秒：数据库里的时间只精确到秒，带微秒比较会把刚写入的行误删
    mats = kingdee.query(forms.MATERIAL)
    boms = kingdee.query(forms.BOM)
    if not mats:
        raise RuntimeError("金蝶 EAS 返回的物料为空，本次不覆盖本地镜像")       # 宁可保留旧数据，也不把镜像清空
    store.executemany(
        "REPLACE INTO kd_material (f_number, f_name, f_specification, f_group, f_erp_cls, f_unit, f_forbid, f_modify_date, synced_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(m["FNumber"], m["FName_L2"], m["FModel"], m["FGroupName"], m["FMaterialAttr"], m["FBaseUnitName"],
          "A" if str(m["FStatus"]) == "1" else "B", str(m["FLastUpdateTime"]).replace("T", " ")[:19], now) for m in mats])
    store.execute("DELETE FROM kd_material WHERE synced_at < %s", (now,))
    with store.transaction() as cur:                                        # BOM 整体替换，放在一个事务里
        cur.execute("DELETE FROM kd_bom")
        cur.executemany(
            "INSERT INTO kd_bom (f_bom_number, f_parent, f_child, f_child_name, f_numerator, f_denominator, f_unit, synced_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            [(b["FBomNumber"], b["FParentNumber"], b["FChildNumber"], b["FChildName"], b["FQty"], 1, b["FUnitName"], now) for b in boms])
    logger.info(f"金蝶 EAS 同步完成：物料 {len(mats)}，BOM 行 {len(boms)}")
    return {"materials": len(mats), "bom_lines": len(boms), "synced_at": now.isoformat(timespec="seconds")}


def material(code: str) -> dict | None:
    return store.one("SELECT * FROM kd_material WHERE f_number = %s", (code,))


def bom_of(parent: str) -> list[dict]:
    return store.query("SELECT f_child, f_child_name, f_numerator / NULLIF(f_denominator, 0) AS qty, f_unit FROM kd_bom "
                       "WHERE f_parent = %s ORDER BY id", (parent,))


if __name__ == "__main__":
    print(sync_all())
