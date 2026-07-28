import { cookies } from "next/headers";
import Chat, { type Msg } from "@/components/Chat";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

// 服务端预取会话历史：有 cookie 则直连后端读取 Checkpointer，SSR 直出历史消息
async function fetchHistory(sid: string): Promise<Msg[]> {
  try {
    const res = await fetch(`${BACKEND}/api/history/${sid}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return [];
    const data = await res.json();
    if (!Array.isArray(data.messages)) return [];
    // 除正文外，带回随消息持久化的引用溯源与建议问题（执行过程为实时信息，不持久化）
    return data.messages.map(
      (m: {
        role: string;
        content: string;
        citations?: Msg["citations"];
        suggests?: string[];
        suggest_action?: string;
      }) => ({
        role: m.role as Msg["role"],
        content: m.content,
        citations: m.citations,
        suggests: m.suggests,
        suggestAction: m.suggest_action,
      }),
    );
  } catch {
    // 后端不可用时降级为空历史，不阻塞首屏
    return [];
  }
}

export default async function Home() {
  const cookieStore = await cookies();
  const sid = cookieStore.get("session_id")?.value ?? null;
  const initialMessages = sid ? await fetchHistory(sid) : [];

  return <Chat sessionId={sid} initialMessages={initialMessages} />;
}
