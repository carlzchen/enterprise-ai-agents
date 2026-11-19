"""
生成知识库的样例资料：uv run python scripts/make_kbase_samples.py
  选型表（Excel）、工艺规程（Word）、培训材料（PPT），再把 CadRag 生成的 6 张伺服电机安装尺寸图（DXF）拷过来；
  另外造几个"坏文件"（加密 PDF、损坏的 Word、不支持的类型），用来验证入口拦截和告警。
内容来自 MiniERP 的产品主数据和已有的作业指导书，都是模拟数据。
"""
import shutil
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.infra.config.settings import settings  # noqa: E402

OUT = ROOT / "data" / "kbase_samples"
SERIES_CN = {"SHADED_POLE": "罩极电机", "CROSSFLOW": "贯流风机", "BLDC": "无刷直流电机", "STEPPER": "步进电机", "SERVO": "交流伺服电机", "GEAR": "齿轮减速电机"}


def products() -> list[dict]:
    with pymysql.connect(host=settings.mysql_host, port=settings.mysql_port, user=settings.mysql_user, password=settings.mysql_password,
                         database=settings.erp_database, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor) as c, c.cursor() as cur:
        cur.execute("SELECT code, name, series, voltage, power_w, rated_speed_rpm FROM md_product WHERE status = 'ACTIVE' ORDER BY series, code")
        return list(cur.fetchall())


def selection_xlsx(rows: list[dict]) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for series, cn in SERIES_CN.items():
        ws = wb.create_sheet(cn)
        ws.append([f"科朗 {cn}选型表（模拟数据）"])
        ws.append([])
        ws.append(["型号", "额定电压", "额定功率(W)", "额定转速(rpm)", "额定转矩(N·m)", "防护等级", "绝缘等级"])
        for p in (r for r in rows if r["series"] == series):
            power, speed = float(p["power_w"] or 0), int(p["rated_speed_rpm"] or 0)
            torque = round(9.55 * power / speed, 3) if speed else None
            ws.append([p["code"], p["voltage"], power, speed, torque, "IP65" if series == "SERVO" else "IP54" if series in ("BLDC", "STEPPER") else "IP44",
                       "F 级" if series in ("SERVO", "BLDC") else "B 级"])
    path = OUT / "KL系列电机选型表.xlsx"
    wb.save(path)
    return path


def process_docx() -> Path:
    import docx
    d = docx.Document()
    d.add_heading("伺服电机定子嵌线与浸漆工艺规程", level=0)
    d.add_paragraph("文件编号：WI-SV-012　版本：2　适用产品：KL-SV 系列交流伺服电机　归口部门：工艺部")
    for title, items in [
        ("1 目的与范围", ["规定伺服电机定子嵌线、整形、绑扎、浸漆、烘干各工序的操作要求和检验标准。", "适用于 60、80、110、130 法兰伺服电机定子组件的生产。"]),
        ("2 嵌线", ["嵌线前检查铁芯槽内无毛刺、无油污，槽绝缘纸高出铁芯两端各 3mm。", "漆包线规格按 BOM 领用，不得混用线径。嵌线张力控制在 1.5 至 2.5N，避免漆膜划伤。",
                   "每槽匝数按设计图纸执行，嵌线完成后用匝间测试仪检测，冲击电压 1500V，波形面积差不大于 5%。"]),
        ("3 整形与绑扎", ["线圈端部整形后高度不得超过图纸规定的端部尺寸，端部与机壳的最小间隙不小于 4mm。", "使用聚酯绑扎带绑扎，绑扎间距 15 至 20mm，结头朝内。"]),
        ("4 浸漆与烘干", ["采用真空压力浸漆（VPI），真空度不高于 100Pa，保持 20 分钟；加压 0.4MPa，保持 30 分钟。", "滴干 30 分钟后进烘箱，分两段固化：第一段 90℃ 保温 2 小时，第二段 140℃ 保温 4 小时。",
                       "出烘箱后自然冷却到 50℃ 以下才能转序。浸漆后绝缘电阻不低于 100MΩ（500V 兆欧表）。"]),
        ("5 检验", ["耐压试验：1800V AC，1 分钟，漏电流不大于 5mA，无击穿和闪络。", "三相直流电阻不平衡度不大于 2%。", "检验不合格的定子挂红色标识隔离，由质量部判定返工或报废。"]),
        ("6 安全注意事项", ["浸漆间必须开启排风，操作人员佩戴防毒口罩和丁腈手套。", "绝缘漆属于危险化学品，按危化品管理规定存放，严禁明火。"]),
    ]:
        d.add_heading(title, level=1)
        for it in items:
            d.add_paragraph(it, style="List Bullet")
    d.add_heading("7 关键工艺参数汇总", level=1)
    t = d.add_table(rows=1, cols=3)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text, t.rows[0].cells[2].text = "工序", "参数", "要求"
    for row in [("嵌线", "张力", "1.5 至 2.5N"), ("匝间测试", "冲击电压", "1500V"), ("浸漆", "真空度", "不高于 100Pa"), ("浸漆", "加压", "0.4MPa，30 分钟"),
                ("烘干", "第二段温度", "140℃，4 小时"), ("耐压", "试验电压", "1800V AC，1 分钟")]:
        cells = t.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = row
    path = OUT / "伺服电机定子嵌线与浸漆工艺规程.docx"
    d.save(path)
    return path


def training_pptx() -> Path:
    from pptx import Presentation
    prs = Presentation()
    slides = [
        ("伺服系统常见故障与售后处理培训", "面向销售工程师和售后工程师　版本 1"),
        ("编码器类故障的判断", "现象：上电报编码器通讯异常，或运行中位置跳变。\n先查编码器线缆屏蔽层是否单端接地，线缆长度是否超过 20 米。\n再查接插件：2026 年 1 月之后的 B 版 BOM 已改为压接端子，之前批次为焊接，虚焊概率更高。\n确认是电机本体问题时，按序列号查保修，走先换后修流程。"),
        ("过载与过热的处理", "现象：驱动器报过载，或电机表面温度超过 80℃。\n先核对负载惯量比，一般不超过电机转子惯量的 10 倍，超过时调整增益或加减速机。\n检查抱闸是否完全打开；检查环境温度是否超过 40℃。\n长期在额定转矩 100% 以上运行会导致温升超标，应重新选型。"),
        ("客户现场信息收集清单", "电机型号和序列号、驱动器型号和固件版本。\n报警代码和出现的时机（上电、启动、运行中、停止）。\n负载类型、减速比、运行曲线。\n线缆长度、接地方式、现场照片。"),
        ("退换货流程要点", "保修期内的质量问题：售后开服务工单，优先发备机，故障件返回后质量部做 8D 分析。\n非质量问题或超过保修期：报价维修，客户确认后执行。\n所有 RMA 必须带序列号，仓库凭 RMA 单收货。"),
    ]
    for i, (title, body) in enumerate(slides):
        s = prs.slides.add_slide(prs.slide_layouts[0 if i == 0 else 1])
        s.shapes.title.text = title
        s.placeholders[1].text = body
    path = OUT / "伺服系统常见故障与售后处理培训.pptx"
    prs.save(path)
    return path


def bad_files() -> list[Path]:
    from pypdf import PdfReader, PdfWriter
    bad = OUT / "bad"
    bad.mkdir(exist_ok=True)
    src = ROOT / "data" / "samples" / "步进电机与驱动器应用指南.pdf"
    w = PdfWriter()
    for page in PdfReader(str(src)).pages[:2]:
        w.add_page(page)
    w.encrypt("secret")
    with (bad / "加密的手册.pdf").open("wb") as f:
        w.write(f)
    (bad / "损坏的工艺文件.docx").write_bytes(b"PK\x03\x04 this is not a real docx")
    (bad / "三维模型.step").write_text("ISO-10303-21;\nHEADER;\nENDSEC;\n", encoding="utf-8")
    (bad / "空文件.pdf").write_bytes(b"")
    return sorted(bad.iterdir())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    made = [selection_xlsx(products()), process_docx(), training_pptx()]
    cad = Path.home() / "Desktop/Python/Projectbydate/CadRag/out/dxf"
    (OUT / "drawings").mkdir(exist_ok=True)
    for f in sorted(cad.glob("*.dxf")):
        shutil.copy(f, OUT / "drawings" / f.name)
        made.append(OUT / "drawings" / f.name)
    made += bad_files()
    for m in made:
        print(m.relative_to(ROOT), m.stat().st_size)
