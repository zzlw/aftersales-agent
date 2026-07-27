"""知识库管理端点：重建索引 / 统计（热更新入口，加分项的后端部分）。"""
from fastapi import APIRouter, Request

from app.rag.ingest import ingest

router = APIRouter()


@router.post("/api/kb/reindex")
async def reindex(request: Request):
    """全量重建知识库索引（knowledge/ 目录下 md 文件变更后调用）。"""
    stats = await ingest(request.app.state.pool)
    return {"ok": True, **stats}


@router.get("/api/kb/stats")
async def stats(request: Request):
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT lang, count(*) AS chunks FROM kb_chunks GROUP BY lang ORDER BY lang")
            rows = await cur.fetchall()
    return {"by_lang": rows}
