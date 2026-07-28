import { cookies } from "next/headers";
import Chat, { type Msg } from "@/components/Chat";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

// 服务端预取会话历史：有 cookie 则直连后端读取 Checkpointer，SSR 直出历史消息
// generating：该会话是否有进行中的生成（刷新时命中则客户端重连续播，ChatGPT 同款流恢复）
async function fetchHistory(sid: string): Promise<{ messages: Msg[]; generating: boolean }> {
  try {
    const res = await fetch(`${BACKEND}/api/history/${sid}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return { messages: [], generating: false };
    const data = await res.json();
    if (!Array.isArray(data.messages)) return { messages: [], generating: false };
    // 除正文外，带回随消息持久化的执行过程、引用溯源与建议问题，刷新后完整回放
    const messages = data.messages.map(
      (m: {
        role: string;
        content: string;
        timeline?: Msg["timeline"];
        citations?: Msg["citations"];
        suggests?: string[];
        suggest_action?: string;
      }) => ({
        role: m.role as Msg["role"],
        content: m.content,
        timeline: m.timeline,
        citations: m.citations,
        suggests: m.suggests,
        suggestAction: m.suggest_action,
      }),
    );
    return { messages, generating: Boolean(data.generating) };
  } catch {
    // 后端不可用时降级为空历史，不阻塞首屏
    return { messages: [], generating: false };
  }
}

export default async function Home() {
  const cookieStore = await cookies();
  const sid = cookieStore.get("session_id")?.value ?? null;
  const history = sid ? await fetchHistory(sid) : { messages: [], generating: false };

  return (
    <Chat
      sessionId={sid}
      initialMessages={history.messages}
      resumeGenerating={history.generating}
    />
  );
}
