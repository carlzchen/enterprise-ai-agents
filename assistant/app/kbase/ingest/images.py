"""
文档里的图片：上传 MinIO 私有存储，再让视觉模型（线上是 vLLM 部署的 Qwen-VL，OpenAI 兼容接口）写一段摘要放在图片下面，
这样图里的参数表、曲线、接线图内容也能被检索到。摘要按图片内容哈希缓存，同一张图不会重复调用模型。

  Markdown 里图片写成 kbimg://<内容哈希前 16 位>.<扩展名>，不写存储地址；每张图在 kb_image 里按「文档 + 版本」登记。
  用户看回答时，服务端先按统一授权判断能不能看这份文档、这个版本是不是生效版本，再现签一个几分钟有效的临时链接。

另外两条视觉路线：
  扫描页  PDF 每页字数很少时，逐页渲染成图片交给视觉模型转写成 Markdown（没有配置视觉模型时退到 MinerU）
  图片文件  直接上传的 PNG / JPG（现场照片、铭牌、截图），视觉模型转写 + 摘要
"""
import base64
import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote

from langchain_core.messages import HumanMessage

from app.infra.config.settings import settings
from app.infra.llm.providers import llm_providers
from app.infra.object_storage.minio_gateway import minio_gateway
from app.shared.runtime.logger import PROJECT_ROOT, logger

CACHE_FILE = PROJECT_ROOT / "output" / "kbase" / "image_summaries.json"
IMG_REF = re.compile(r"!\[([^\]]*)\]\((?!https?://)([^)\s]+\.(?:jpe?g|png|webp|gif))\)", re.I)
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}
MIN_BYTES = 2_000            # 更小的多半是图标、分隔线，不值得调模型
MAX_BYTES = 10_000_000       # 模型接口的单图上限
MAX_IMAGES = 600             # 单个文档最多摘要多少张，其余只上传
WORKERS = 4
PROMPT = ("这是工业电机 / 伺服驱动器资料里的一张插图。图前面的文字是：{context}\n"
          "请用中文写一段 100 字以内的摘要，用于检索：先说图的类型（规格表、曲线图、接线图、尺寸图、面板图、时序图、流程图等），"
          "再写图里的关键内容，例如型号、参数名和数值、端子或信号名称、坐标轴含义；图里主要是文字或表格时，抄录最关键的几项。"
          "只写图里看得到的内容，不要猜测，不要说“不是什么类型”，不要使用 Markdown 符号。"
          "如果图是条形码、二维码、Logo、纯装饰图或空白，只回答：无技术信息")
NO_INFO = "无技术信息"

_lock = threading.Lock()


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def _describe(data: bytes, ext: str, context: str) -> str:
    uri = f"data:{MIME[ext]};base64,{base64.b64encode(data).decode()}"
    msg = HumanMessage(content=[{"type": "image_url", "image_url": {"url": uri}}, {"type": "text", "text": PROMPT.format(context=context or "（无）")}])
    text = str(llm_providers.vision_chat().invoke([msg]).content).strip().replace("\n", " ").replace("*", "")
    return "" if text.startswith(NO_INFO) else text


def _context(md: str, pos: int) -> str:
    before = IMG_REF.sub("", md[max(0, pos - 400):pos])
    before = re.sub(r"<!--.*?-->", "", before)
    return before.strip()[-200:]


REF_SCHEME = "kbimg://"
KBIMG = re.compile(r"kbimg://([0-9a-f]{16})(\.[a-z]+)")


def object_key(doc_key: str, sha16: str, ext: str) -> str:
    return f"images/{doc_key or 'misc'}/{sha16}{ext}"


def enrich(md: str, base_dir: Path, doc_key: str, sink: list | None = None) -> str:
    """上传 md 引用的本地图片并加上摘要，图片引用换成 kbimg://。sink 不为空时把每张图的登记信息追加进去。
    没有配置视觉模型时只上传，不写摘要"""
    refs = [(m, (base_dir / unquote(m.group(2))).resolve()) for m in IMG_REF.finditer(md)]
    refs = [(m, p) for m, p in refs if p.is_file() and str(p).startswith(str(base_dir.resolve()))]     # 路径必须在解析目录内
    if not refs:
        return md
    summarize = bool(settings.vl_api_key)
    if not summarize:
        logger.warning("[kbase] 没有配置 VL_API_KEY，图片只上传 MinIO，不生成摘要")
    cache = _load_cache()
    stats = {"summarized": 0, "cached": 0, "failed": 0}

    def work(i: int) -> tuple[str, str]:
        m, path = refs[i]
        data = path.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        ext = path.suffix.lower()
        key = object_key(doc_key, sha[:16], ext)
        minio_gateway.put_bytes(key, data)
        url = f"{REF_SCHEME}{sha[:16]}{ext}"
        rec = {"sha": sha[:16], "object_key": key, "mime": MIME.get(ext), "bytes": len(data), "summary": ""}
        if sink is not None:
            with _lock:
                sink.append(rec)
        if not summarize or not MIN_BYTES <= len(data) <= MAX_BYTES or i >= MAX_IMAGES:
            return url, ""
        if sha in cache:
            stats["cached"] += 1
            rec["summary"] = cache[sha]
            return url, cache[sha]
        try:
            text = _describe(data, path.suffix.lower(), _context(md, m.start()))
        except Exception as e:
            stats["failed"] += 1
            logger.warning(f"[kbase] 图片摘要失败 {path.name}：{type(e).__name__}: {str(e)[:150]}")
            return url, ""
        with _lock:
            cache[sha] = text
            stats["summarized"] += 1
        rec["summary"] = text
        return url, text

    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            results = list(pool.map(work, range(len(refs))))
    finally:
        with _lock:
            _save_cache(cache)
    attempted = stats["summarized"] + stats["failed"]
    logger.info(f"[kbase] 图片 {len(refs)} 张：新生成摘要 {stats['summarized']}，命中缓存 {stats['cached']}，失败 {stats['failed']}")
    if attempted >= 3 and stats["failed"] / attempted > 0.5:           # 多半是密钥、额度或模型名有问题：让任务按临时错误重试，已生成的摘要有缓存
        raise RuntimeError(f"图片摘要大面积失败（{stats['failed']}/{attempted}），请检查 VL_API_KEY、VL_MODEL 和额度")

    out, last = [], 0
    for (m, _), (url, text) in zip(refs, results):
        out.append(md[last:m.start()])
        out.append(f"![{m.group(1) or '图'}]({url})" + (f"\n\n【图片说明】{text}\n" if text else ""))
        last = m.end()
    out.append(md[last:])
    return "".join(out)


# ---------------------------------------------------------------- 扫描页、图片文件

OCR_PROMPT = ("这是电机 / 驱动器技术资料扫描件的一页。请把页面内容完整转写成 Markdown：标题用 #，表格用 Markdown 表格，"
              "数值、型号、单位照原样抄写，看不清的写「[看不清]」，不要补充页面上没有的内容。只输出转写结果。")


def vl_available() -> bool:
    return bool(settings.vl_api_key)


def _ocr(data: bytes, ext: str = ".png") -> str:
    uri = f"data:{MIME[ext]};base64,{base64.b64encode(data).decode()}"
    msg = HumanMessage(content=[{"type": "image_url", "image_url": {"url": uri}}, {"type": "text", "text": OCR_PROMPT}])
    return str(llm_providers.vision_chat().invoke([msg]).content).strip()


def scanned_pdf_to_markdown(path: Path, work_dir: Path, scale: float = 2.0) -> str:
    """扫描件：每页渲染成 PNG（pypdfium2），交给视觉模型转写，带页码标记。一页失败只影响那一页"""
    import pypdfium2
    from app.kbase.ingest.parsers import PAGE_MARK
    out_dir = Path(work_dir) / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pypdfium2.PdfDocument(str(path))
    pages = []

    def one(i: int) -> str:
        f = out_dir / f"p{i + 1}.png"
        if not f.exists():
            pdf[i].render(scale=scale).to_pil().save(f)
        try:
            return _ocr(f.read_bytes())
        except Exception as e:
            logger.warning(f"[kbase] 第 {i + 1} 页视觉转写失败：{type(e).__name__}: {str(e)[:120]}")
            return "[本页转写失败]"
    n = len(pdf)
    with ThreadPoolExecutor(WORKERS) as pool:
        texts = list(pool.map(one, range(n)))
    for i, t in enumerate(texts, start=1):
        pages.append(f"{PAGE_MARK.format(i)}\n{t}")
    failed = sum(1 for t in texts if t == "[本页转写失败]")
    if n and failed / n > 0.5:
        raise RuntimeError(f"扫描件视觉转写大面积失败（{failed}/{n} 页），请检查 VL 服务")
    return "\n\n".join(pages)


def image_file_to_markdown(path: Path, title: str) -> str:
    """单独上传的图片文件：放一个本地引用（随后由 enrich 上传并摘要），再附上视觉模型的转写"""
    text = _ocr(path.read_bytes(), path.suffix.lower()) if vl_available() else ""
    return f"# {title}\n\n![{title}]({path.name})\n\n" + (f"{text}\n" if text else "")
