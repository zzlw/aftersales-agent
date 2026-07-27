"""FastAPI 入口：lifespan 内初始化 PG 连接池、Checkpointer 与状态图。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import build_graph
from app.agent.nodes import set_pool
from app.api.chat import router as chat_router
from app.api.kb import router as kb_router
from app.api.ticket import router as ticket_router
from app.config import settings
from app.rag.ingest import ingest
from app.rag.store import init_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    # PostgresSaver 要求 autocommit + dict_row
    pool = AsyncConnectionPool(
        settings.database_url, open=False, max_size=10,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()
    await init_schema(pool)   # kb_chunks / tickets 表与索引
    set_pool(pool)            # 节点层检索用

    # 首次部署时知识库为空，自动摄取一次（云端无法手动进容器时的兼容路径）
    async with pool.connection() as conn:
        row = await (await conn.execute("SELECT count(*) AS n FROM kb_chunks")).fetchone()
    if row["n"] == 0:
        stats = await ingest(pool)
        print(f"[startup] knowledge base empty, auto-ingested: {stats}")

    app.state.pool = pool
    app.state.graph = build_graph(checkpointer)
    yield
    await pool.close()


app = FastAPI(title="Aftersales Agent POC", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3003"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(kb_router)
app.include_router(ticket_router)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
