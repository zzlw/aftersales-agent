"""对话 SSE 端点：POST /api/chat，按方案分帧协议输出。

帧类型全集：status / thinking / tool / delta / citation / suggest / done / error
（D1 先启用 status / delta / done / error，其余随 D2/D3 节点接入）
"""
import json
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


@router.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    graph = request.app.state.graph
    session_id = req.session_id or uuid.uuid4().hex

    async def event_stream():
        # 建连后 500ms 内首帧：用过程感知掩盖串行 LLM 延迟（方案 3.3 延迟控制③）
        yield {"event": "status", "data": json.dumps(
            {"stage": "accepted", "session_id": session_id}, ensure_ascii=False)}
        try:
            config = {"configurable": {"thread_id": session_id}}
            payload = {"messages": [{"role": "user", "content": req.message}]}
            async for frame in graph.astream(payload, config, stream_mode="custom"):
                ftype = frame.pop("type", "delta")
                yield {"event": ftype, "data": json.dumps(frame, ensure_ascii=False)}
            yield {"event": "done", "data": json.dumps({"session_id": session_id})}
        except Exception as e:  # 模型/网络异常 → error 帧，前端友好降级
            yield {"event": "error", "data": json.dumps(
                {"message": f"服务暂时不可用，请稍后重试（{type(e).__name__}）"}, ensure_ascii=False)}

    return EventSourceResponse(event_stream())


@router.get("/api/history/{session_id}")
async def history(session_id: str, request: Request):
    """会话恢复：从 Checkpointer 读回历史消息（刷新页面/服务重启后继续聊）。"""
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    messages = (snapshot.values or {}).get("messages", []) if snapshot else []
    # 只回传 user/assistant 可见消息
    visible = [m for m in messages if m.get("role") in ("user", "assistant")]
    return {"session_id": session_id, "messages": visible}
