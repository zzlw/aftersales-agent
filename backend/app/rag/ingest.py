"""知识库摄取管线：Markdown（front-matter 元数据）→ 按 H2 章节切分 → 向量化 → 入库。

切分策略（方案 4.1）：按 `## ` 二级标题切分，天然对齐"一个问题一个 chunk"，
超长章节再按 ~500 token（近似 800 字符）二次切分，10% 重叠。
"""
import re
from pathlib import Path

from app.llm.client import embed_texts
from app.rag.store import replace_chunks

KNOWLEDGE_DIR = Path("knowledge")
MAX_CHARS = 800
OVERLAP = 80


def parse_front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


def split_sections(body: str) -> list[tuple[str, str]]:
    """按 H2 切分，返回 (章节名, 内容)。"""
    parts = re.split(r"^## ", body, flags=re.MULTILINE)
    sections = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.split("\n", 1)
        title = lines[0].strip()
        content = lines[1].strip() if len(lines) > 1 else ""
        if content:
            sections.append((title, content))
    return sections


def chunk_text(text: str) -> list[str]:
    """超长章节二次切分（~10% 重叠）。"""
    if len(text) <= MAX_CHARS:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + MAX_CHARS])
        start += MAX_CHARS - OVERLAP
    return chunks


def load_chunks() -> list[dict]:
    chunks = []
    for path in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        for section, content in split_sections(body):
            for piece in chunk_text(content):
                chunks.append({
                    "doc_title": meta.get("title", path.stem),
                    "section": section,
                    "product_line": meta.get("product_line", "unknown"),
                    "lang": meta.get("lang", "zh"),
                    "category": meta.get("category", "general"),
                    # 检索内容带上标题上下文，提升向量语义完整性
                    "content": f"# {meta.get('title', '')} · {section}\n{piece}",
                })
    return chunks


async def ingest(pool) -> dict:
    """全量摄取：加载 → 向量化 → 重建索引。返回统计信息。"""
    chunks = load_chunks()
    if not chunks:
        return {"chunks": 0, "message": "no knowledge files found"}
    embeddings = await embed_texts([c["content"] for c in chunks])
    await replace_chunks(pool, chunks, embeddings)
    langs = {c["lang"] for c in chunks}
    return {"chunks": len(chunks), "docs": len({c["doc_title"] for c in chunks}),
            "langs": sorted(langs)}
