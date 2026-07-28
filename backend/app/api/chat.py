"""SSE 聊天接口：POST /api/chat 发起生成，GET /api/chat/stream/{sid} 流恢复。

帧类型全集：status / thinking / tool / delta / citation / suggest / done / error

流恢复（ChatGPT 同款）：
- 生成与 SSE 连接解耦：graph 在后台 asyncio 任务中执行，帧写入内存缓冲（RunBuffer），
  客户端断连/刷新不会取消生成，跑完照常写 Checkpointer；
- 重连端点从缓冲第 0 帧重放并续播新帧直到 done，前端得以无损恢复流式动画；
- 单进程部署（Railway 单 worker）下进程内 dict 足够，多实例需换 Redis Pub/Sub。
"""
import asyncio
import json
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

# done 之后缓冲保留时长（秒）：容忍「刚完成即刷新」的重连竞态
BUFFER_TTL = 120


class RunBuffer:
    """单次生成的帧缓冲：支持任意多个订阅者从头重放 + 续播。"""

    def __init__(self):
        self.frames: list[dict] = []
        self.done = False
        self.cond = asyncio.Condition()

    async def put(self, frame: dict):
        async with self.cond:
            self.frames.append(frame)
            self.cond.notify_all()

    async def finish(self):
        async with self.cond:
            self.done = True
            self.cond.notify_all()

    async def subscribe(self):
        """从第 0 帧重放已缓冲内容，并阻塞等待新帧直到 done。"""
        idx = 0
        while True:
            async with self.cond:
                while idx >= len(self.frames) and not self.done:
                    await self.cond.wait()
                new = self.frames[idx:]
                idx = len(self.frames)
                finished = self.done and idx >= len(self.frames)
            for f in new:
                yield f
            if finished:
                return


# session_id → 进行中（或刚结束、TTL 内）的生成缓冲
_active_runs: dict[str, RunBuffer] = {}
# 持有后台任务强引用，防止被事件循环 GC
_bg_tasks: set[asyncio.Task] = set()


async def _run_graph(graph, session_id: str, message: str, buf: RunBuffer):
    """后台执行 graph：与 HTTP 连接生命周期完全解耦。"""
    try:
        config = {"configurable": {"thread_id": session_id}}
        payload = {"messages": [{"role": "user", "content": message}]}
        async for frame in graph.astream(payload, config, stream_mode="custom"):
            ftype = frame.pop("type", "delta")
            await buf.put({"event": ftype, "data": json.dumps(frame, ensure_ascii=False)})
        await buf.put({"event": "done", "data": json.dumps({"session_id": session_id})})
    except Exception as e:  # 模型/网络异常 → error 帧，前端友好降级
        await buf.put({"event": "error", "data": json.dumps(
            {"message": f"服务暂时不可用，请稍后重试（{type(e).__name__}）"}, ensure_ascii=False)})
    finally:
        await buf.finish()
        # 延迟清理：done 后短暂保留缓冲，供刷新后的重连完整重放
        await asyncio.sleep(BUFFER_TTL)
        if _active_runs.get(session_id) is buf:
            _active_runs.pop(session_id, None)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


@router.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    graph = request.app.state.graph
    session_id = req.session_id or uuid.uuid4().hex

    buf = RunBuffer()
    _active_runs[session_id] = buf
    # 建连后 500ms 内首帧：用过程感知掩盖串行 LLM 延迟（方案 3.3 延迟控制③）
    await buf.put({"event": "status", "data": json.dumps(
        {"stage": "accepted", "session_id": session_id}, ensure_ascii=False)})

    task = asyncio.create_task(_run_graph(graph, session_id, req.message, buf))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    return EventSourceResponse(buf.subscribe())


@router.get("/api/chat/stream/{session_id}")
async def chat_stream(session_id: str):
    """流恢复：刷新/断连后重连，从头重放本次生成的全部帧并续播。"""
    buf = _active_runs.get(session_id)
    if buf is None:
        # 无进行中的生成（已过 TTL 或从未发起）：直接收尾，前端走 history 兜底
        async def empty():
            yield {"event": "done", "data": json.dumps({"session_id": session_id})}
        return EventSourceResponse(empty())
    return EventSourceResponse(buf.subscribe())


@router.get("/api/history/{session_id}")
async def history(session_id: str, request: Request):
    """会话恢复：从 Checkpointer 读回历史消息（刷新页面/服务重启后继续聊）。"""
    graph = request.app.state.graph
    config = {"configurable": {"thread_id": session_id}}
    snapshot = await graph.aget_state(config)
    messages = (snapshot.values or {}).get("messages", []) if snapshot else []
    # 只回传 user/assistant 可见消息
    visible = [m for m in messages if m.get("role") in ("user", "assistant")]
    # generating：该会话是否有未完成的生成（供前端决定是否重连续播）
    buf = _active_runs.get(session_id)
    return {
        "session_id": session_id,
        "messages": visible,
        "generating": bool(buf and not buf.done),
    }
