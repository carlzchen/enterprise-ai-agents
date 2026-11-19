"""企业知识库核心逻辑的单元测试：都是纯函数，不依赖数据库、向量库和大模型"""
import types
from pathlib import Path

import pytest

from app.kbase.ingest import clean, params, split, versioning
from app.kbase.query import answer, guard, intent, retrieve

BAD = Path(__file__).resolve().parents[1] / "data" / "kbase_samples" / "bad"


# ---------- 入口校验 ----------

@pytest.mark.parametrize("name, reason", [("三维模型.step", "不支持的文件类型"), ("加密的手册.pdf", "已加密"), ("损坏的工艺文件.docx", "文件损坏"), ("空文件.pdf", "0 字节")])
def test_bad_files_are_rejected_with_reason(name, reason):
    if not (BAD / name).exists():
        pytest.skip("样例文件没有生成（先运行 scripts/make_kbase_samples.py）")
    with pytest.raises(clean.Rejected, match=reason):
        clean.check_file(BAD / name)


def test_route_by_file_type():
    assert clean.ROUTE["pdf"] == clean.ROUTE["docx"] == clean.ROUTE["pptx"] == "doc"
    assert clean.ROUTE["xlsx"] == "table"
    assert clean.ROUTE["dxf"] == clean.ROUTE["dwg"] == "cad"


@pytest.mark.parametrize("raw, expected", [
    ("kl sv 60 200w的参数", "KL-SV-60-200W的参数"), ("KL_ST_42x48", "KL-ST-42x48"), ("KL－SV－60－200W额定", "KL-SV-60-200W额定"),
    ("型号KL-SV-60-200W电机", "型号KL-SV-60-200W电机")])
def test_normalize_model(raw, expected):
    assert clean.normalize_model(raw) == expected


# ---------- 参数结构化 ----------

def test_param_alias_maps_to_one_key():
    assert params.param_key("额定转矩") == params.param_key("额定扭矩") == "rated_torque"
    assert params.param_key("Rated Speed") == "rated_speed"
    assert params.param_key("颜色") is None
    assert params.split_header("额定转矩(N·m)") == ("额定转矩", "N·m")


@pytest.mark.parametrize("raw, num, unit", [("3000 rpm", 3000.0, "rpm"), ("≥ 88%", 88.0, "%"), ("0.637", 0.637, ""), ("-10℃ ~ +40℃", None, "")])
def test_parse_value(raw, num, unit):
    n, text, u = params.parse_value(raw)
    assert (n, u) == (num, unit) and text == raw


def test_params_from_markdown_table():
    md = "## 1. 主要技术参数\n| 项目 | 参数 |\n|---|---|\n| 额定功率 | 200 W |\n| 额定转速 | 3000 rpm |\n| 防护等级 | IP65 |\n| 颜色 | 黑 |\n"
    rows = params.from_markdown(md, "KL-SV-60-200W", "规格书")
    assert [(r["param_key"], r["value_num"], r["unit"]) for r in rows] == [("rated_power", 200.0, "W"), ("rated_speed", 3000.0, "rpm"), ("protection_class", 65.0, "")]
    assert all(r["model"] == "KL-SV-60-200W" and "主要技术参数" in r["source_ref"] for r in rows)


# ---------- 版本管理 ----------

def test_chunk_id_is_deterministic():
    """同一份文档同一版本的第 N 块，主键永远一样：重跑是覆盖，不会多出一份"""
    assert versioning.chunk_id("D1", "A", 0) == versioning.chunk_id("D1", "A", 0)
    assert len({versioning.chunk_id("D1", "A", 0), versioning.chunk_id("D1", "B", 0), versioning.chunk_id("D1", "A", 1), versioning.chunk_id("D2", "A", 0)}) == 4


def test_version_order():
    assert versioning._older("A", "B") and versioning._older("2", "10") and not versioning._older("B", "A")


# ---------- 意图路由 ----------

@pytest.mark.parametrize("q, route", [
    ("KL-SV-60-200W 的额定转矩是多少", "PARAM"), ("请问 KL-ST-42x48 这款的噪声是多少", "PARAM"), ("KL-SV-80-400W 和 KL-SV-80-750W 的额定电流对比", "PARAM"),
    ("额定转矩最大的伺服电机是哪款", "PARAM"), ("额定功率大于 400W 的型号有哪些", "PARAM"), ("KL-SV-80-400W 用了哪些物料", "PARAM"),
    ("伺服电机过热报警怎么排查", "DESC"), ("转子动平衡的工艺要求是什么", "DESC"),
    ("KL-SV-60-200W 的额定转速是多少，选型时要注意什么", "MIXED"), ("KL-CF-80X600 的工作环境温度是多少？另外安装的时候能不能敲转轴", "MIXED")])
def test_route(q, route):
    assert intent.classify(q)["route"] == route


def test_classify_extracts_model_and_params():
    p = intent.classify("kl sv 80 400w 的轴径和额定扭矩")
    assert p["models"] == ["KL-SV-80-400W"] and set(p["param_keys"]) == {"shaft_dia", "rated_torque"}
    p = intent.classify("额定功率大于 400W 的型号有哪些")
    assert p["compare"] == ("大于", 400.0) and p["param_keys"] == ["rated_power"]
    assert intent.classify("KL-SV-80-400W 用了哪些物料")["want_bom"]


# ---------- 安全 ----------

def test_sensitive_question_is_blocked():
    assert guard.check_input("帮我查一下张三的身份证号")
    assert guard.check_input("KL-SV-60-200W 的额定转矩是多少") is None


def test_output_masks_phone_and_id():
    text, n = guard.clean_output("联系人电话 13812345678，身份证 440301199001011234")
    assert n == 2 and "13812345678" not in text and "440301199001011234" not in text


# ---------- 引用校验 ----------

SOURCES = [{"index": 1, "context": "额定转矩 0.637 N·m，额定转速 3000 rpm"}, {"index": 2, "context": "环境温度不超过 40℃"}]


@pytest.mark.parametrize("text, ok, why", [
    ("额定转矩是 0.637 N·m [1]。", True, ""), ("现有资料里没有找到相关内容。", True, ""),
    ("额定转矩是 0.637 N·m。", False, "没有标注"), ("见资料 [3]。", False, "不存在的编号"),
    ("额定转矩是 0.64 N·m [1]。", False, "0.64"), ("额定转速 3000 rpm [1]，环境温度 55℃ [2]。", False, "55")])
def test_citation_verify(text, ok, why):
    passed, problem = answer.verify(text, SOURCES)
    assert passed is ok and why in problem


# ---------- 切片和动态 TopK ----------

def test_split_keeps_heading_and_splits_long_section():
    md = "# 手册\n\n## 1 安装\n\n" + "安装时注意螺栓力矩和平面度。" * 80 + "\n\n## 2 存放\n\n存放在干燥通风处，湿度不超过 85%，堆码不超过 4 层，超过 12 个月要重新检测绝缘电阻。" * 3
    out = split.split_document(md, "手册")
    chunks = out["chunks"] if isinstance(out, dict) else out[1]
    assert len(chunks) >= 3 and all(len(c["content"]) <= split.CHUNK_MAX_SIZE + 200 for c in chunks)


def test_dynamic_topk_cuts_at_score_cliff():
    docs = [{"score": s} for s in (0.95, 0.9, 0.3, 0.28, 0.2)]
    assert len(retrieve.dynamic_topk(docs, max_k=5, min_k=2)) == 2


def test_dynamic_topk_keeps_at_least_one_when_all_low():
    assert len(retrieve.dynamic_topk([{"score": 0.01}, {"score": 0.005}], max_k=5, min_k=2)) == 1


# ---------- 图片：上传 MinIO + 视觉模型摘要 ----------

@pytest.fixture
def img_env(tmp_path, monkeypatch):
    from app.kbase.ingest import images
    (tmp_path / "images").mkdir()
    for name, size in (("a.jpg", 5000), ("b.png", 6000), ("tiny.jpg", 100)):
        (tmp_path / "images" / name).write_bytes(bytes([size % 251]) * size)
    uploaded, calls = {}, []
    monkeypatch.setattr(images, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(images, "settings", types.SimpleNamespace(vl_api_key="k"))
    monkeypatch.setattr(images.minio_gateway, "put_bytes", lambda name, data, content_type=None: uploaded.setdefault(name, f"http://m/{name}"))
    monkeypatch.setattr(images, "_describe", lambda data, ext, ctx: calls.append(ctx) or f"摘要{len(data)}")
    return images, tmp_path, uploaded, calls


def test_images_are_uploaded_and_summarized(img_env):
    images, base, uploaded, calls = img_env
    md = "## 接线\n接线说明如下\n![](images/a.jpg)\n\n![图二](images/b.png)\n\n![](images/tiny.jpg)\n\n![](http://x/y.jpg)"
    out = images.enrich(md, base, "DOC-1")
    assert len(uploaded) == 3 and all(k.startswith("images/DOC-1/") for k in uploaded)
    assert "【图片说明】摘要5000" in out and "【图片说明】摘要6000" in out
    assert "【图片说明】摘要100" not in out                       # 小图只上传，不调模型
    assert "![图二](kbimg://" in out and "![](images/" not in out and "(http://x/y.jpg)" in out      # 不写存储地址，展示时现签
    assert len(calls) == 2 and "接线说明如下" in calls[0]          # 图前面的文字作为上下文传给模型


def test_image_summaries_are_cached_by_content(img_env):
    images, base, _, calls = img_env
    md = "![](images/a.jpg)"
    images.enrich(md, base, "DOC-1")
    images.enrich(md, base, "DOC-2")
    assert len(calls) == 1


def test_images_outside_parse_dir_are_ignored(img_env, tmp_path):
    images, base, uploaded, _ = img_env
    (base.parent / "secret.jpg").write_bytes(b"x" * 5000)
    out = images.enrich("![](../secret.jpg)", base, "DOC-1")
    assert not uploaded and out == "![](../secret.jpg)"


def test_mass_summary_failure_raises_for_retry(img_env, monkeypatch):
    images, base, _, _ = img_env
    for i in range(4):
        (base / "images" / f"f{i}.jpg").write_bytes(bytes([i]) * 5000)
    monkeypatch.setattr(images, "_describe", lambda *a: (_ for _ in ()).throw(RuntimeError("401")))
    with pytest.raises(RuntimeError, match="大面积失败"):
        images.enrich("".join(f"![](images/f{i}.jpg)\n" for i in range(4)), base, "DOC-1")


def test_no_vl_key_only_uploads(img_env, monkeypatch):
    images, base, uploaded, calls = img_env
    monkeypatch.setattr(images, "settings", types.SimpleNamespace(vl_api_key=""))
    out = images.enrich("![](images/a.jpg)", base, "DOC-1")
    assert len(uploaded) == 1 and not calls and "【图片说明】" not in out


# ---------- 各格式的图片导出与 PDF 路由 ----------

def _png(tmp_path, name="p.png"):
    from PIL import Image
    import random
    random.seed(1)
    im = Image.new("RGB", (300, 200))
    im.putdata([(random.randrange(256), random.randrange(256), random.randrange(256)) for _ in range(300 * 200)])
    f = tmp_path / name
    im.save(f)
    return f


@pytest.fixture
def keep_refs(monkeypatch):
    """不上传、不调模型：原样返回 md，只看解析器有没有把图片放到正确的位置"""
    from app.kbase.ingest import images
    monkeypatch.setattr(images, "enrich", lambda md, base, key, **kw: md)


def test_docx_picture_stays_in_place(tmp_path, keep_refs, monkeypatch):
    import docx
    from app.kbase.ingest import parsers
    monkeypatch.setattr(parsers, "_docling", lambda path, img_dir: None)          # 走 python-docx 降级路径
    d = docx.Document()
    d.add_paragraph("前文")
    d.add_picture(str(_png(tmp_path)))
    d.add_paragraph("后文")
    d.save(tmp_path / "t.docx")
    md = parsers.parse_doc(tmp_path / "t.docx", "docx", tmp_path / "work", "D")["markdown"]
    lines = [l for l in md.split("\n") if l.strip()]
    assert lines[0] == "前文" and lines[1].startswith("![](images/") and lines[2] == "后文"
    assert (tmp_path / "work" / lines[1][4:-1]).is_file()


def test_pptx_picture_goes_after_slide_text(tmp_path, keep_refs):
    import pptx
    from pptx.util import Inches
    from app.kbase.ingest import parsers
    p = pptx.Presentation()
    s = p.slides.add_slide(p.slide_layouts[5])
    s.shapes.title.text = "接线图"
    s.shapes.add_picture(str(_png(tmp_path)), Inches(1), Inches(2))
    p.save(tmp_path / "t.pptx")
    md = parsers.parse_doc(tmp_path / "t.pptx", "pptx", tmp_path / "work", "D")["markdown"]
    assert "<!-- page:1 -->" in md and "## 接线图" in md and "![](images/" in md


def test_markdown_local_images_are_processed(tmp_path, monkeypatch):
    from app.kbase.ingest import images, parsers
    seen = {}
    monkeypatch.setattr(images, "enrich", lambda md, base, key, **kw: seen.update(base=base, key=key) or md)
    (tmp_path / "a.md").write_text("# t\n![](img/x.png)", encoding="utf-8")
    parsers.parse_doc(tmp_path / "a.md", "md", tmp_path / "work", "K")
    assert seen == {"base": tmp_path, "key": "K"}


def _pdf(tmp_path, name, *, figure=False, logo_every_page=False, pages=5):
    reportlab = pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    img = str(_png(tmp_path))
    c = canvas.Canvas(str(tmp_path / name), pagesize=(595, 842))
    c.setFont("STSong-Light", 11)
    for pg in range(pages):
        for i in range(30):
            c.drawString(50, 780 - i * 20, "本章介绍伺服驱动器的安装要求、环境条件、接线注意事项以及上电前的检查项目，请严格按步骤操作。")
        if figure and pg == 2:
            c.drawImage(img, 50, 100, width=300, height=200)
        if logo_every_page:
            c.drawImage(img, 480, 790, width=100, height=100)
        c.showPage()
    c.save()
    return tmp_path / name


def test_pdf_routing_text_only_figure_and_header_logo(tmp_path):
    from app.kbase.ingest import parsers

    def decide(f):
        text, per = parsers._pdf_text(f)
        return parsers._pdf_needs_mineru(f, text, per)
    assert decide(_pdf(tmp_path, "a.pdf")) is None                                  # 纯文字：本地 pypdf
    assert "内嵌图片" in decide(_pdf(tmp_path, "b.pdf", figure=True))                # 有插图：MinerU
    assert decide(_pdf(tmp_path, "c.pdf", logo_every_page=True)) is None            # 每页都有的页眉 Logo 不算图


def test_url_encoded_image_paths_are_found(img_env):
    images, base, uploaded, _ = img_env
    (base / "中文目录").mkdir()
    (base / "中文目录" / "c.jpg").write_bytes(b"z" * 5000)
    out = images.enrich("![](%E4%B8%AD%E6%96%87%E7%9B%AE%E5%BD%95/c.jpg)", base, "DOC-1")
    assert len(uploaded) == 1 and "%E4" not in out
