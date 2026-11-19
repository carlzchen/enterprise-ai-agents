"""
文档切分（父子两级）：
1. 按 Markdown 标题做语义粗切，代码块内的 # 不算标题 → 父章节（完整原文存 MySQL）
2. 章节超过 CHUNK_SIZE 用递归切割器细切（保留标题前缀与重叠）；过短的相邻块在同一父章节内合并 → 子块（存 Milvus）
3. 每个子块内容前加"【文档标题】章节标题"上下文头：同类文档（如 31 份规格书）结构相同，
   不加文档标题会导致向量高度相似、检索串型号
"""
import re
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.shared.runtime.logger import logger, step_log

CHUNK_SIZE = 600          # 超过则递归细切
CHUNK_OVERLAP = 60
CHUNK_MIN = 200           # 小于则尝试与同章节相邻块合并
CHUNK_MAX_SIZE = 900      # 合并后上限

TITLE_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _clean_title(line: str) -> str:
    return re.sub(r"^\s*#{1,6}\s+", "", line).strip()


@step_log("split_by_title")
def split_by_title(md: str) -> list[dict[str, Any]]:
    """按标题切出章节；标题之前的内容归入"概述"章节"""
    sections: list[dict[str, Any]] = []
    title, lines, in_code = "概述", [], False
    for raw in md.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if FENCE_RE.match(raw):
            in_code = not in_code
            lines.append(raw)
            continue
        if not in_code and TITLE_RE.match(raw):
            if any(l.strip() for l in lines):
                sections.append({"title": title, "content": "\n".join(lines).strip()})
            title, lines = _clean_title(raw), []
            continue
        lines.append(raw)
    if any(l.strip() for l in lines):
        sections.append({"title": title, "content": "\n".join(lines).strip()})
    return [s for s in sections if s["content"]]


def _split_long(text: str) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
                                              separators=["\n\n", "\n", "。", "；", "！", "？", ". ", "; ", " "])
    return [t.strip() for t in splitter.split_text(text) if t.strip()]


def _merge_short(pieces: list[str]) -> list[str]:
    merged: list[str] = []
    for p in pieces:
        if merged and len(merged[-1]) < CHUNK_MIN and len(merged[-1]) + len(p) + 1 <= CHUNK_MAX_SIZE:
            merged[-1] = merged[-1] + "\n" + p
        else:
            merged.append(p)
    return merged


@step_log("build_chunks")
def build_chunks(sections: list[dict[str, Any]], file_title: str) -> list[dict[str, Any]]:
    chunks = []
    for idx, sec in enumerate(sections):
        body = sec["content"]
        pieces = _merge_short(_split_long(body)) if len(body) > CHUNK_SIZE else [body]
        for part, piece in enumerate(pieces, start=1):
            title = sec["title"] if len(pieces) == 1 else f"{sec['title']}（第{part}部分）"
            chunks.append({"title": title, "parent_title": sec["title"], "part": part, "parent_index": idx,
                           "content": f"【{file_title}】{sec['title']}\n{piece}"})
    return chunks


def split_document(md: str, file_title: str) -> tuple[list[dict], list[dict]]:
    """返回 (父章节, 子块)"""
    sections = split_by_title(md)
    if not sections:
        raise ValueError("文档内容为空，无法切分")
    parents = [{"title": s["title"], "content": f"【{file_title}】{s['title']}\n{s['content']}"}
               for s in sections]
    chunks = build_chunks(sections, file_title)
    logger.info(f"切分完成：父章节 {len(parents)} 个，子块 {len(chunks)} 个")
    return parents, chunks
