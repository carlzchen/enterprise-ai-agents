"""
生成 3 份带插图的 PDF 产品手册（参数与 MiniERP 数据库一致），用于演示 PDF → MinerU → 图片理解 → 入库链路。
用法：uv run python scripts/make_sample_pdfs.py
输出：data/samples/*.pdf
"""
import io
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.infra.erp.erp_repository import erp_repository

OUT = Path(__file__).resolve().parents[1] / "data" / "samples"
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
FONT = "STSong-Light"
H1 = ParagraphStyle("h1", fontName=FONT, fontSize=18, leading=24, spaceAfter=8)
H2 = ParagraphStyle("h2", fontName=FONT, fontSize=14, leading=20, spaceBefore=10, spaceAfter=6)
BODY = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=16)

for cand in ["/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"]:
    if Path(cand).exists():
        font_manager.fontManager.addfont(cand)
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=cand).get_name()
        break
plt.rcParams["axes.unicode_minus"] = False


def fig_image(fig, width_mm=160) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth, img.drawHeight = width_mm * mm, width_mm * mm * ratio
    return img


def table(rows, widths=None) -> Table:
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), FONT, 9), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dfe7f2")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def p(text):
    return Paragraph(text, BODY)


# ---------------- 1. 伺服选型手册 ----------------

def servo_manual():
    servos = erp_repository.products_by_series("SERVO")
    inertia = {200: 0.18, 400: 0.34, 750: 1.05, 1200: 5.2, 1500: 6.5, 2000: 8.9}
    story = [Paragraph("KL 系列交流伺服电机选型手册", H1),
             p("本手册适用于科力（KL）系列交流伺服电机，法兰规格 60 / 80 / 110 / 130，功率 200W～2kW，"
               "额定转速 3000 rpm，最高转速 5000 rpm，过载能力 3 倍额定转矩（持续 3 秒）。适配 KD 系列伺服驱动器。"),
             Paragraph("1 型号与参数", H2)]
    rows = [["型号", "法兰", "额定功率", "额定转矩", "峰值转矩", "转子惯量", "编码器"]]
    for s in servos:
        w = int(s["power_w"])
        t = 9550 * w / 1000 / 3000
        enc = "2500 线增量式" if w <= 400 else "17 位绝对值"
        rows.append([s["code"], s["code"].split("-")[2], f"{w} W", f"{t:.2f} Nm", f"{3 * t:.2f} Nm",
                     f"{inertia.get(w, 1.0)} kg.cm2", enc])
    story += [table(rows), Spacer(1, 6),
              p("注：额定转矩 T = 9550 × P(kW) / n(rpm)。2026 年 2 月起（ECN2601120001），编码器插接由手工焊接改为压接端子组件，"
                "抗振性能提升，编码器通信类报警（AL.030）发生率显著下降。"),
              Paragraph("2 转矩-转速特性曲线", H2),
              p("下图为 80 法兰 750W 与 130 法兰 1.5kW 型号的转矩-转速特性。连续工作区内可长期运行，"
                "短时工作区仅允许加减速阶段使用。")]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    for ax, (w, name) in zip(axes, [(750, "KL-SV-80-750W"), (1500, "KL-SV-130-1500W")]):
        t = 9550 * w / 1000 / 3000
        n = [0, 1000, 2000, 3000, 4000, 5000]
        cont = [t, t, t, t, t * 0.75, t * 0.55]
        peak = [3 * t, 3 * t, 3 * t, 2.6 * t, 1.6 * t, 0.9 * t]
        ax.fill_between(n, 0, cont, alpha=0.35, label="连续工作区")
        ax.plot(n, peak, "r--", label="短时工作区上限")
        ax.set_title(f"{name} 转矩-转速曲线")
        ax.set_xlabel("转速 (rpm)")
        ax.set_ylabel("转矩 (N·m)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    story += [fig_image(fig), Paragraph("3 选型计算步骤", H2),
              p("第一步：计算负载转矩。丝杠传动 T<sub>L</sub> = F × P<sub>h</sub> / (2π × η)，同步带传动 T<sub>L</sub> = F × r / η。"),
              p("第二步：计算加速转矩 T<sub>a</sub> = (J<sub>M</sub> + J<sub>L</sub>) × ω / t<sub>a</sub>，峰值需求 T<sub>L</sub> + T<sub>a</sub> 应小于电机峰值转矩的 80%。"),
              p("第三步：校核惯量比 J<sub>L</sub> / J<sub>M</sub>：一般应用 ≤ 10 倍；高响应定位（如机器人关节）建议 ≤ 5 倍。"),
              p("第四步：有效转矩（均方根转矩）应小于额定转矩，并留 1.5 倍安全系数。"),
              Paragraph("4 安装尺寸", H2), p("80 法兰电机安装尺寸示意（单位 mm）：法兰边长 80，止口 Φ70，轴径 Φ19，轴伸 35，安装孔 4-Φ6 均布于 Φ90 圆周。")]
    fig, ax = plt.subplots(figsize=(4.2, 4.2))
    ax.add_patch(plt.Rectangle((-40, -40), 80, 80, fill=False, lw=2))
    ax.add_patch(plt.Circle((0, 0), 35, fill=False, lw=1.5))
    ax.add_patch(plt.Circle((0, 0), 9.5, fill=True, alpha=0.3))
    for a in range(4):
        x, y = 45 * math.cos(math.pi / 4 + a * math.pi / 2), 45 * math.sin(math.pi / 4 + a * math.pi / 2)
        ax.add_patch(plt.Circle((x * 0.78, y * 0.78), 3, fill=False))
    ax.annotate("80", (0, -46), ha="center")
    ax.annotate("Φ70 止口", (0, 28), ha="center")
    ax.annotate("Φ19 轴", (0, -16), ha="center")
    ax.annotate("4-Φ6 / PCD Φ90", (22, 42), ha="center", fontsize=8)
    ax.set_xlim(-55, 55)
    ax.set_ylim(-55, 55)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("KL-SV-80 系列法兰安装尺寸")
    story += [fig_image(fig, 90), Paragraph("5 接线与编码器线缆", H2),
              p("动力线 U/V/W/PE 与编码器线必须分开走线，间距不小于 30 cm。编码器线缆长度不超过 20 m，屏蔽层单端接驱动器侧 FG。"
                "若现场振动较大，请确认编码器插头为压接端子版本（2026 年 2 月后出厂产品）。")]
    build("KL系列交流伺服电机选型手册.pdf", story)


# ---------------- 2. 驱动器报警手册 ----------------

ALARMS = [
    ("AL.010", "过电流", "电机动力线 U/V/W 短路或对地短路；驱动器功率模块损坏；加速时间过短。",
     "断电后用万用表检查 U/V/W 相间及对地绝缘（应 > 10MΩ）；延长加速时间（参数 P1-34）；仍报警则更换驱动器。"),
    ("AL.020", "主回路过电压", "减速过快导致再生能量过大；未接制动电阻或制动电阻阻值过大；输入电压超过 AC 264V。",
     "延长减速时间（参数 P1-35）；按负载惯量外接制动电阻（750W 推荐 50Ω/200W）；检查供电电压。"),
    ("AL.021", "主回路欠电压", "输入电压低于 AC 170V；瞬时停电；接线松动。", "检查输入电源与接线端子紧固，必要时加装稳压电源。"),
    ("AL.030", "编码器通信异常", "编码器线缆断线、接触不良或屏蔽不良；编码器插头虚焊（2026 年 2 月前出厂批次）；干扰过大。",
     "检查编码器线缆与插头，重新插拔并紧固；确认屏蔽层单端接地；2026 年 2 月前批次电机若在振动工况反复报警，"
     "请联系售后更换为压接端子版本（ECN2601120001）。"),
    ("AL.031", "编码器数据校验错误", "编码器受强干扰；编码器损坏。", "动力线与编码器线分开走线；更换编码器线缆后仍报警则返厂检测编码器。"),
    ("AL.040", "过载", "负载超过额定转矩长时间运行；机械卡滞；增益设置过高引起振荡。",
     "检查机械传动是否卡滞；重新核算负载转矩与有效转矩；降低速度环增益（P2-00）后重新自整定。"),
    ("AL.050", "位置偏差过大", "负载过大或加速度过高导致跟随误差超限；位置指令频率过高；增益偏低。",
     "适当提高位置环增益（P2-02）；降低加速度；检查偏差阈值参数 P1-50（默认 3 圈）。"),
    ("AL.060", "驱动器过热", "环境温度超过 55℃；散热风扇故障；安装间距不足。", "改善通风，驱动器左右间距不小于 20 mm；检查风扇运转。"),
    ("AL.070", "超速", "电机转速超过最高转速 5000 rpm 的 1.2 倍；编码器零位错误；速度指令异常。", "检查速度指令与电子齿轮比设置；重新做编码器零位校准。"),
]


def alarm_manual():
    story = [Paragraph("KD 系列伺服驱动器报警代码手册", H1),
             p("KD 系列伺服驱动器与 KL 系列交流伺服电机配套使用。驱动器检测到异常时，面板数码管显示报警代码（如 AL.030），"
               "同时报警输出信号 ALM 断开，电机进入自由停车状态。排除故障后，通过面板 SET 键或外部 ARST 信号复位。"),
             Paragraph("1 面板说明", H2)]
    fig, ax = plt.subplots(figsize=(5, 2.6))
    ax.add_patch(plt.Rectangle((0, 0), 10, 5, fill=False, lw=2))
    ax.add_patch(plt.Rectangle((1, 2.6), 6, 1.6, color="black"))
    ax.text(4, 3.4, "AL.030", color="red", fontsize=20, ha="center", va="center", family="monospace")
    for i, k in enumerate(["MODE", "▲", "▼", "SET"]):
        ax.add_patch(plt.Circle((1.6 + i * 1.6, 1.2), 0.45, fill=False))
        ax.text(1.6 + i * 1.6, 1.2, k, ha="center", va="center", fontsize=7)
    ax.text(8.5, 3.9, "POWER", fontsize=7, ha="center")
    ax.add_patch(plt.Circle((8.5, 3.3), 0.25, color="green"))
    ax.text(8.5, 2.3, "ALM", fontsize=7, ha="center")
    ax.add_patch(plt.Circle((8.5, 1.7), 0.25, color="red"))
    ax.set_xlim(-0.5, 10.5)
    ax.set_ylim(-0.5, 5.5)
    ax.axis("off")
    ax.set_title("KD 驱动器操作面板（显示编码器通信异常报警）")
    story += [fig_image(fig, 110), Paragraph("2 报警代码一览", H2),
              table([["代码", "名称"]] + [[a[0], a[1]] for a in ALARMS], [30 * mm, 60 * mm])]
    story.append(PageBreak())
    story.append(Paragraph("3 报警原因与处理", H2))
    for code, name, cause, fix in ALARMS:
        story += [Paragraph(f"{code} {name}", H2), p(f"可能原因：{cause}"), p(f"处理方法：{fix}")]
    build("KD系列伺服驱动器报警代码手册.pdf", story)


# ---------------- 3. 步进应用指南 ----------------

def stepper_guide():
    steppers = erp_repository.products_by_series("STEPPER")
    story = [Paragraph("步进电机与驱动器应用指南", H1),
             p("本指南适用于 KL-ST 系列两相混合式步进电机（机座 28 / 42 / 57 / 86），步距角 1.8°，"
               "推荐配合细分驱动器使用。"), Paragraph("1 在售型号", H2)]
    story.append(table([["型号", "名称", "额定电压"]] + [[s["code"], s["name"], s["voltage"]] for s in steppers]))
    story += [Paragraph("2 接线", H2), p("两相四线接法：A+（红）、A-（蓝）、B+（绿）、B-（黑）。电机反转时，交换 A+ 与 A- 即可。")]
    fig, ax = plt.subplots(figsize=(5.5, 2.4))
    for i, (lbl, c) in enumerate([("A+", "red"), ("A-", "blue"), ("B+", "green"), ("B-", "black")]):
        ax.plot([0, 6], [3 - i, 3 - i], color=c, lw=2)
        ax.text(-0.3, 3 - i, lbl, ha="right", va="center")
        ax.text(6.3, 3 - i, lbl, ha="left", va="center")
    ax.text(-1.2, 4, "电机引线", ha="center")
    ax.text(7.3, 4, "驱动器端子", ha="center")
    ax.set_xlim(-2, 8)
    ax.set_ylim(-0.5, 4.5)
    ax.axis("off")
    ax.set_title("两相步进电机与驱动器接线图")
    story += [fig_image(fig, 110), Paragraph("3 矩频特性", H2),
              p("步进电机输出转矩随转速升高而下降。下图为 42×48 与 57×76 型号在 24V、细分 16 时的矩频曲线。"
                "建议工作转速控制在 600 rpm 以内以保证有足够的转矩裕量。")]
    fig, ax = plt.subplots(figsize=(6, 3.2))
    rpm = [0, 150, 300, 600, 900, 1200]
    ax.plot(rpm, [0.55, 0.52, 0.45, 0.30, 0.18, 0.10], marker="o", label="KL-ST-42x48")
    ax.plot(rpm, [1.9, 1.8, 1.5, 0.95, 0.55, 0.30], marker="s", label="KL-ST-57x76")
    ax.set_xlabel("转速 (rpm)")
    ax.set_ylabel("转矩 (N·m)")
    ax.set_title("步进电机矩频曲线（24V，16 细分）")
    ax.grid(alpha=0.3)
    ax.legend()
    story += [fig_image(fig, 130), Paragraph("4 常见问题排查", H2),
              Paragraph("4.1 电机失步", H2), p("原因：负载转矩超过当前转速下的输出转矩、加速过快、驱动电流设置偏低。"
                                              "处理：降低加速度或工作转速；按电机额定电流设置驱动器电流；必要时换用大一号机座或闭环步进。"),
              Paragraph("4.2 电机发热严重", H2), p("步进电机正常表面温度可达 70～80℃。若超过 90℃，检查驱动电流是否过大，"
                                                 "并开启驱动器的静止半流功能。"),
              Paragraph("4.3 低速振动与噪声", H2), p("低速共振区通常在 100～200 rpm。提高细分数（建议 ≥ 8）、"
                                                "适当降低电流或增加阻尼可明显改善。")]
    build("步进电机与驱动器应用指南.pdf", story)


def build(name: str, story):
    OUT.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(OUT / name), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm, title=name[:-4]).build(story)
    print("生成", OUT / name)


if __name__ == "__main__":
    servo_manual()
    alarm_manual()
    stepper_guide()
