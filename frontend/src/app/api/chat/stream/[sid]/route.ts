// SSE 流恢复代理：GET /api/chat/stream/[sid] → 后端重放 + 续播
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ sid: string }> }
) {
  const { sid } = await params;

  const upstream = await fetch(`${BACKEND}/api/chat/stream/${encodeURIComponent(sid)}`, {
    cache: "no-store",
  });

  if (!upstream.body) {
    return new Response("upstream body is null", { status: 502 });
  }

  // 与 POST /api/chat 相同的手动中继模式，确保数据即时 flush
  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const reader = upstream.body.getReader();

  (async () => {
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        await writer.write(value);
      }
    } catch {
      // upstream closed
    } finally {
      writer.close();
    }
  })();

  return new Response(readable, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-store, must-revalidate",
      "X-Accel-Buffering": "no",
    },
  });
}
