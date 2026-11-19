"""
从金蝶 EAS 取的两类主数据，以及每一列对应 EAS 的哪张表、哪个字段。
表名和字段名按 EAS 标准产品写的；每家公司都做过二次开发，接真实环境前要让 EAS 顾问核对一遍，只改这里。
"""

MATERIAL = "material"                  # 物料
MATERIAL_FIELDS = {
    "FNumber": "T_BD_Material.FNumber 物料编码",
    "FName_L2": "T_BD_Material.FName_L2 物料名称（简体中文）",
    "FModel": "T_BD_Material.FModel 规格型号",
    "FGroupName": "T_BD_MaterialGroup.FName_L2 物料分类",
    "FMaterialAttr": "物料属性：自制 / 外购 / 委外",
    "FBaseUnitName": "T_BD_MeasureUnit.FName_L2 基本计量单位",
    "FStatus": "状态：1 核准（启用），其他为未启用",
    "FLastUpdateTime": "T_BD_Material.FLastUpdateTime 最后修改时间",
}

BOM = "bom"                            # 物料清单
BOM_FIELDS = {
    "FBomNumber": "T_MM_BOM.FNumber BOM 编码",
    "FParentNumber": "T_MM_BOM.FMaterialID → 父项物料编码",
    "FChildNumber": "T_MM_BOMEntry.FMaterialID → 子项物料编码",
    "FChildName": "子项物料名称",
    "FQty": "T_MM_BOMEntry 单位用量",
    "FUnitName": "子项计量单位",
}
