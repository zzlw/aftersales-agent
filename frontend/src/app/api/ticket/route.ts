// Next.js Route Handler — 代理工单 API
const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  const body = await request.text();
  const upstream = await fetch(`${BACKEND}/api/ticket`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  const data = await upstream.json();
  return Response.json(data, { status: upstream.status });
}
