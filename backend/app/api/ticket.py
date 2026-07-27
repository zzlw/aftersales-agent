"""转人工工单端点（P0：MVP 场景 4 的必要环节）。"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()


class TicketRequest(BaseModel):
    session_id: str
    contact: str = Field(min_length=1, description="联系方式（手机号/邮箱）")
    product_model: str = Field(min_length=1, description="产品型号")
    description: str = Field(min_length=1, description="问题描述")


@router.post("/api/ticket")
async def create_ticket(req: TicketRequest, request: Request):
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO tickets (session_id, contact, product_model, description)
                   VALUES (%s, %s, %s, %s) RETURNING id, created_at""",
                (req.session_id, req.contact, req.product_model, req.description))
            row = await cur.fetchone()
    return {"ok": True, "ticket_id": f"TK{row['id']:06d}",
            "created_at": row["created_at"].isoformat()}


@router.get("/api/tickets")
async def list_tickets(request: Request):
    """演示用：查看已建工单。"""
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM tickets ORDER BY id DESC LIMIT 20")
            rows = await cur.fetchall()
    return {"tickets": rows}
