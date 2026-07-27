"""向量存储与混合检索（PGVector + tsvector + RRF 融合）。

中文分词决策（方案 4.2）：PG 默认 tsvector 不支持中文分词，且 pgvector 镜像
不带 zhparser/pg_jieba 扩展 —— 采用 Python 侧 jieba 预分词、存分词后文本、
统一用 'simple' 配置建 tsvector，不依赖任何 PG 扩展。
"""
import jieba

from app.config import settings

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS kb_chunks (
    id            serial PRIMARY KEY,
    doc_title     text NOT NULL,
    section       text NOT NULL,
    product_line  text NOT NULL,
    lang          text NOT NULL,
    category      text NOT NULL,
    content       text NOT NULL,
    content_tokens text NOT NULL,          -- jieba 预分词后的文本（英文为小写原文）
    embedding     vector({settings.embedding_dim}),
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('simple', content_tokens)) STORED,
    updated_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx
    ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx
    ON kb_chunks USING gin (tsv);

CREATE TABLE IF NOT EXISTS tickets (
    id            serial PRIMARY KEY,
    session_id    text NOT NULL,
    contact       text NOT NULL,
    product_model text NOT NULL,
    description   text NOT NULL,
    status        text NOT NULL DEFAULT 'open',
    created_at    timestamptz DEFAULT now()
);
"""

# RRF 融合：向量召回与关键词召回各取 Top-20，按 1/(60+rank) 加权合并
HYBRID_SQL = """
WITH vec AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> %(qvec)s::vector) AS rnk
    FROM kb_chunks
    ORDER BY embedding <=> %(qvec)s::vector
    LIMIT 20
),
kw AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY ts_rank(tsv, query) DESC) AS rnk
    FROM kb_chunks, to_tsquery('simple', %(tsquery)s) AS query
    WHERE tsv @@ query
    LIMIT 20
),
fused AS (
    SELECT COALESCE(vec.id, kw.id) AS id,
           COALESCE(1.0 / (60 + vec.rnk), 0) + COALESCE(1.0 / (60 + kw.rnk), 0) AS score
    FROM vec FULL OUTER JOIN kw ON vec.id = kw.id
)
SELECT c.id, c.doc_title, c.section, c.product_line, c.lang, c.category,
       c.content, fused.score
FROM fused JOIN kb_chunks c ON c.id = fused.id
ORDER BY fused.score DESC
LIMIT %(top_k)s;
"""


def tokenize(text: str) -> str:
    """jieba 分词（中英混排通吃：英文 token 原样保留并转小写）。"""
    return " ".join(t.strip().lower() for t in jieba.cut(text) if t.strip())


def to_or_tsquery(text: str) -> str:
    """查询词转 OR 连接的 tsquery（召回优先，精排交给 RRF）。"""
    tokens = [t for t in tokenize(text).split() if len(t) > 1]
    return " | ".join(tokens[:20]) if tokens else "__none__"


async def init_schema(pool) -> None:
    async with pool.connection() as conn:
        await conn.execute(SCHEMA_SQL)


async def replace_chunks(pool, chunks: list[dict], embeddings: list[list[float]]) -> None:
    """全量重建（POC 语料小；生产走增量 upsert + 软删除）。"""
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE kb_chunks;")
        async with conn.cursor() as cur:
            for chunk, emb in zip(chunks, embeddings):
                await cur.execute(
                    """INSERT INTO kb_chunks
                       (doc_title, section, product_line, lang, category, content, content_tokens, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)""",
                    (chunk["doc_title"], chunk["section"], chunk["product_line"],
                     chunk["lang"], chunk["category"], chunk["content"],
                     tokenize(chunk["content"]), str(emb)),
                )


async def hybrid_search(pool, query: str, query_embedding: list[float],
                        top_k: int = 5) -> list[dict]:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(HYBRID_SQL, {
                "qvec": str(query_embedding),
                "tsquery": to_or_tsquery(query),
                "top_k": top_k,
            })
            rows = await cur.fetchall()
    # dict_row 已在连接池配置，直接返回
    return list(rows)
