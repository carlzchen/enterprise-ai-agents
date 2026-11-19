"""
MinerU 云端解析 PDF：申请上传地址 → PUT 上传 → 轮询解析结果 → 下载 zip → 解压得到 Markdown 与 images/
MinerU 单文件上限 200 页：超过 MAX_PAGES 的 PDF 先拆分成多段分别解析，再按顺序合并 Markdown 与图片。
"""
import io
import time
import zipfile
from pathlib import Path

import requests

from app.infra.config.settings import settings
from pypdf import PdfReader, PdfWriter

from app.shared.runtime.logger import logger, step_log

MINERU_MODEL_VERSION = "vlm"
MINERU_POLL_INTERVAL_SECONDS = 3
MINERU_POLL_TIMEOUT_SECONDS = 600


def _headers() -> dict:
    if not settings.mineru_api_token:
        raise RuntimeError("未配置 MINERU_API_TOKEN，无法解析 PDF")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {settings.mineru_api_token}"}


@step_log("mineru_upload")
def upload(pdf: Path) -> str:
    r = requests.post(f"{settings.mineru_base_url}/file-urls/batch", headers=_headers(), timeout=30,
                      json={"files": [{"name": pdf.name}], "model_version": MINERU_MODEL_VERSION})
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise RuntimeError(f"MinerU 申请上传失败：{body.get('msg')}")
    batch_id, urls = body["data"]["batch_id"], body["data"]["file_urls"]
    with requests.Session() as s:
        s.trust_env = False  # 预签名地址直连，不走系统代理
        put = s.put(urls[0], data=pdf.read_bytes(), timeout=120)
        if put.status_code != 200:
            raise RuntimeError(f"MinerU 文件上传失败，HTTP {put.status_code}")
    logger.info(f"MinerU 已接收文件 {pdf.name}，batch_id={batch_id}")
    return batch_id


@step_log("mineru_poll")
def poll(batch_id: str) -> str:
    url, start = f"{settings.mineru_base_url}/extract-results/batch/{batch_id}", time.time()
    while time.time() - start < MINERU_POLL_TIMEOUT_SECONDS:
        try:
            r = requests.get(url, headers=_headers(), timeout=30)
        except requests.RequestException as e:
            logger.warning(f"轮询 MinerU 网络异常，稍后重试：{e}")
            time.sleep(MINERU_POLL_INTERVAL_SECONDS)
            continue
        if r.status_code >= 500:
            time.sleep(MINERU_POLL_INTERVAL_SECONDS)
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"MinerU 查询结果失败：{body.get('msg')}")
        results = body["data"].get("extract_result") or []
        if results:
            state = results[0].get("state")
            if state == "done":
                return results[0]["full_zip_url"]
            if state == "failed":
                raise RuntimeError(f"MinerU 解析失败：{results[0].get('err_msg')}")
            logger.info(f"MinerU 解析中：{state}")
        time.sleep(MINERU_POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"MinerU 解析超时（{MINERU_POLL_TIMEOUT_SECONDS}s）")


@step_log("mineru_download")
def download_and_extract(zip_url: str, out_dir: Path, stem: str) -> Path:
    with requests.Session() as s:
        s.trust_env = False
        r = s.get(zip_url, timeout=120)
        r.raise_for_status()
    target = out_dir / stem
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for member in z.infolist():
            dest = (target / member.filename).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise RuntimeError(f"压缩包包含非法路径：{member.filename}")
        z.extractall(target)
    mds = sorted(target.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
    if not mds:
        raise RuntimeError("MinerU 结果中没有 Markdown 文件")
    md = mds[0]
    final = md.with_name(f"{stem}.md")
    if md != final:
        md.rename(final)
    return final


MAX_PAGES = 180


@step_log("split_pdf")
def split_pdf(pdf: Path, out_dir: Path) -> list[Path]:
    reader = PdfReader(str(pdf))
    total = len(reader.pages)
    if total <= MAX_PAGES:
        return [pdf]
    parts = []
    for i, start in enumerate(range(0, total, MAX_PAGES), start=1):
        writer = PdfWriter()
        for page in reader.pages[start:start + MAX_PAGES]:
            writer.add_page(page)
        part = out_dir / f"{pdf.stem}_part{i}.pdf"
        with part.open("wb") as f:
            writer.write(f)
        parts.append(part)
    logger.info(f"PDF 共 {total} 页，超过 MinerU 单文件上限，拆分为 {len(parts)} 段")
    return parts


def pdf_to_markdown(pdf_path: str, local_dir: str) -> str:
    pdf, out = Path(pdf_path), Path(local_dir)
    parts = split_pdf(pdf, out)
    if len(parts) == 1:
        return str(download_and_extract(poll(upload(pdf)), out, pdf.stem))
    # 分段解析后合并：每段的 images/ 相对路径改写为 分段目录/images/，合并后的 md 放在 out 根目录
    merged = []
    for part in parts:
        md = download_and_extract(poll(upload(part)), out, part.stem)
        rel = md.parent.relative_to(out).as_posix()
        merged.append(md.read_text(encoding="utf-8").replace("](images/", f"]({rel}/images/"))
    final = out / f"{pdf.stem}.md"
    final.write_text("\n\n".join(merged), encoding="utf-8")
    return str(final)
