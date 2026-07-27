// Next.js Route Handler — 代理历史记录 API
const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ sid: string }> }
) {
  const { sid } = await params;
  const upstream = await fetch(`${BACKEND}/api/history/${sid}`);
  const data = await upstream.json();
  return Response.json(data);
}
