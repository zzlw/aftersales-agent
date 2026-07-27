"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { parseSSE } from "@/lib/sse";
import Markdown from "react-markdown";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  ArrowUp,
  ChevronDown,
  FileText,
  Headset,
  ListTree,
  MessageCircleQuestion,
  SquarePen,
  Wrench,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

type Citation = { index: number; title: string; section?: string; snippet?: string };
type TimelineEvent = { kind: "thinking" | "tool"; text: string };

export type Msg = {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  timeline?: TimelineEvent[];
  suggests?: string[];
  suggestAction?: string;
  error?: boolean;
};

// session_id 存 cookie，供服务端 page.tsx 通过 cookies() 预取历史
const SESSION_COOKIE = "session_id";

const setSessionCookie = (sid: string) => {
  document.cookie = `${SESSION_COOKIE}=${sid}; path=/; max-age=86400`;
};

const clearSessionCookie = () => {
  document.cookie = `${SESSION_COOKIE}=; path=/; max-age=0`;
};

const QUICK_QUESTIONS = [
  "笔记本电池充不进电怎么办？",
  "屏幕出现坏点能保修吗？",
  "Is my battery covered under warranty?",
];

// 消息进场：轻量 spring，只动 transform/opacity
const springIn = { type: "spring", stiffness: 320, damping: 28 } as const;

export default function Chat({
  initialMessages,
  sessionId,
}: {
  initialMessages?: Msg[];
  sessionId?: string | null;
}) {
  // 历史消息已由 Server Component 预取（SSR 直出，无需客户端二次拉取）
  const [messages, setMessages] = useState<Msg[]>(initialMessages ?? []);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showTicket, setShowTicket] = useState(false);
  const sessionRef = useRef<string | null>(sessionId ?? null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // SSR 预取的历史消息不播进场动画，只有新增消息才动
  const prefetchedCount = useRef(initialMessages?.length ?? 0);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  // 更新最后一条 assistant 消息的辅助函数
  const patchLast = useCallback((patch: (m: Msg) => Msg) => {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last?.role === "assistant") next[next.length - 1] = patch(last);
      return next;
    });
  }, []);

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || busy) return;
      setBusy(true);
      setInput("");
      setMessages((prev) => [...prev, { role: "user", content }]);
      setStatus("已接收，正在处理…");

      // 本轮 pending 的执行事件（delta 到达前先积累，随后挂到 assistant 消息上）
      let pendingTimeline: TimelineEvent[] = [];
      let assistantStarted = false;

      const ensureAssistant = () => {
        if (assistantStarted) return;
        assistantStarted = true;
        setStatus(null);
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: "", timeline: pendingTimeline },
        ]);
      };

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionRef.current, message: content }),
        });

        for await (const frame of parseSSE(res)) {
          const d = frame.data as Record<string, any>;
          switch (frame.event) {
            case "status":
              if (d.session_id && !sessionRef.current) {
                sessionRef.current = String(d.session_id);
                setSessionCookie(sessionRef.current);
              }
              setStatus(stageText(String(d.stage ?? "")));
              break;
            case "thinking":
              pendingTimeline = [...pendingTimeline, { kind: "thinking", text: String(d.text ?? "") }];
              if (assistantStarted) patchLast((m) => ({ ...m, timeline: pendingTimeline }));
              break;
            case "tool": {
              const hits = Array.isArray(d.hits)
                ? d.hits.map((h: any) => `${h.title}·${h.section}(${h.score})`).join("；")
                : "";
              pendingTimeline = [
                ...pendingTimeline,
                { kind: "tool", text: `${d.name}("${d.query}") → ${hits || "无命中"}` },
              ];
              if (assistantStarted) patchLast((m) => ({ ...m, timeline: pendingTimeline }));
              break;
            }
            case "delta":
              ensureAssistant();
              patchLast((m) => ({ ...m, content: m.content + String(d.text ?? "") }));
              break;
            case "citation":
              patchLast((m) => ({ ...m, citations: (d.items ?? []) as Citation[] }));
              break;
            case "suggest":
              ensureAssistant();
              patchLast((m) => ({
                ...m,
                suggests: (d.items ?? []) as string[],
                suggestAction: d.action ? String(d.action) : undefined,
              }));
              break;
            case "error":
              setStatus(null);
              setMessages((prev) => [
                ...prev,
                { role: "assistant", content: String(d.message ?? "服务异常"), error: true },
              ]);
              break;
            case "done":
              setStatus(null);
              break;
          }
        }
      } catch (err) {
        console.error("[Chat] SSE error:", err);
        setStatus(null);
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `网络异常，请检查后端服务后重试。(${err})`, error: true },
        ]);
      } finally {
        setBusy(false);
        setStatus(null);
      }
    },
    [busy, patchLast]
  );

  const onSuggest = (label: string, action?: string) => {
    if (action === "ticket") setShowTicket(true);
    else send(label);
  };

  const newSession = () => {
    clearSessionCookie();
    sessionRef.current = null;
    setMessages([]);
  };

  return (
    <div className="flex h-dvh w-full flex-col">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-card px-6">
        <div className="flex items-center gap-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-primary">
            <span className="text-sm font-bold text-primary-foreground">L</span>
          </div>
          <div>
            <h1 className="text-base font-semibold tracking-tight">售后智能客服</h1>
            <p className="text-xs text-muted-foreground">Lenovo Aftersales Agent · LangGraph + RAG</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          {/* 主次层级：转人工为关键出口用 outline，新会话为轻量操作用 ghost */}
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground active:scale-[0.98]"
            onClick={newSession}
          >
            <SquarePen />
            新会话
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="bg-card active:scale-[0.98]"
            onClick={() => setShowTicket(true)}
          >
            <Headset />
            转人工工单
          </Button>
        </div>
      </header>

      <ScrollArea className="min-h-0 flex-1">
        <main className="mx-auto w-full max-w-4xl space-y-4 px-6 py-6">
          {messages.length === 0 && (
            <motion.div
              className="mt-16 text-center"
              initial={reduceMotion ? false : { opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={springIn}
            >
              <div className="mx-auto mb-5 flex size-16 items-center justify-center rounded-2xl bg-accent">
                <MessageCircleQuestion className="size-8 text-primary" strokeWidth={1.5} />
              </div>
              <p className="mb-1 text-base font-medium">您好，我是联想售后智能助手</p>
              <p className="mb-6 text-sm text-muted-foreground">
                请描述您遇到的问题，或点击下方快捷提问
              </p>
              <div className="flex flex-wrap justify-center gap-2">
                {QUICK_QUESTIONS.map((q, i) => (
                  <motion.span
                    key={q}
                    initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ ...springIn, delay: 0.08 + i * 0.06 }}
                  >
                    <Button
                      variant="outline"
                      size="sm"
                      className="rounded-full font-normal text-accent-foreground hover:bg-accent active:scale-[0.98]"
                      onClick={() => send(q)}
                    >
                      {q}
                    </Button>
                  </motion.span>
                ))}
              </div>
            </motion.div>
          )}

          {messages.map((m, i) => (
            <motion.div
              key={i}
              className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}
              initial={
                reduceMotion || i < prefetchedCount.current
                  ? false
                  : { opacity: 0, y: 14, scale: 0.98 }
              }
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={springIn}
            >
            <div className="max-w-[85%] space-y-1.5">
              {/* 执行时间线（P1：Agent 过程可视化） */}
              {m.role === "assistant" && m.timeline && m.timeline.length > 0 && (
                <Collapsible className="rounded-lg border border-border bg-muted/50 px-3 py-1.5">
                  <CollapsibleTrigger className="group flex w-full items-center gap-1.5 text-xs text-muted-foreground">
                    <ListTree className="size-3.5" />
                    执行过程（{m.timeline.length} 步）
                    <ChevronDown className="ml-auto size-3.5 transition-transform group-data-[state=open]:rotate-180" />
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <ol className="mt-1.5 space-y-1 border-t border-border pt-1.5">
                      {m.timeline.map((e, j) => (
                        <li key={j} className="flex gap-1.5 text-xs text-muted-foreground">
                          {e.kind === "tool" ? (
                            <Wrench className="mt-0.5 size-3 shrink-0" />
                          ) : (
                            <Sparkles className="mt-0.5 size-3 shrink-0" />
                          )}
                          <span className="break-all">{e.text}</span>
                        </li>
                      ))}
                    </ol>
                  </CollapsibleContent>
                </Collapsible>
              )}

              <div
                className={cn(
                  "px-4 py-2.5 text-sm",
                  m.role === "user"
                    ? "rounded-xl rounded-br-sm bg-primary text-primary-foreground"
                    : m.error
                      ? "rounded-xl rounded-bl-sm border border-destructive/20 bg-destructive/5 text-destructive"
                      : "rounded-xl rounded-bl-sm border border-border bg-card shadow-xs"
                )}
              >
                {m.role === "assistant" && !m.error ? (
                  <div className="prose prose-sm prose-zinc max-w-none prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5">
                    <Markdown>{m.content || "…"}</Markdown>
                  </div>
                ) : (
                  <div className="whitespace-pre-wrap">{m.content || "…"}</div>
                )}

                {/* 引用溯源卡片（P0） */}
                {m.citations && m.citations.length > 0 && (
                  <>
                    <Separator className="my-2" />
                    <div className="space-y-1.5">
                      {m.citations.map((c) => (
                        <Collapsible key={c.index}>
                          <CollapsibleTrigger className="group flex w-full items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-accent-foreground">
                            <FileText className="size-3.5 shrink-0" />
                            <span className="truncate text-left">
                              [{c.index}] {c.title}
                              {c.section ? ` · ${c.section}` : ""}
                            </span>
                            <ChevronDown className="ml-auto size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-180" />
                          </CollapsibleTrigger>
                          {c.snippet && (
                            <CollapsibleContent>
                              <div className="mt-1 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                                <Markdown
                                  components={{
                                    h1: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
                                    h2: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
                                    h3: ({ children }) => <p className="mb-1 font-medium">{children}</p>,
                                    p: ({ children }) => <p className="mb-1 last:mb-0">{children}</p>,
                                    ul: ({ children }) => <ul className="mb-1 list-disc pl-4">{children}</ul>,
                                    ol: ({ children }) => <ol className="mb-1 list-decimal pl-4">{children}</ol>,
                                    li: ({ children }) => <li className="mb-0.5">{children}</li>,
                                  }}
                                >
                                  {c.snippet}
                                </Markdown>
                              </div>
                            </CollapsibleContent>
                          )}
                        </Collapsible>
                      ))}
                    </div>
                  </>
                )}
              </div>

              {/* 快捷建议按钮（suggest 帧） */}
              {m.role === "assistant" && m.suggests && m.suggests.length > 0 && (
                <motion.div
                  className="flex flex-wrap gap-1.5"
                  initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={springIn}
                >
                  {m.suggests.map((s) => (
                    <Button
                      key={s}
                      variant="secondary"
                      size="xs"
                      className="rounded-full font-normal text-accent-foreground hover:bg-accent active:scale-[0.98]"
                      onClick={() => onSuggest(s, m.suggestAction)}
                    >
                      {s}
                    </Button>
                  ))}
                </motion.div>
              )}
            </div>
          </motion.div>
          ))}

          <AnimatePresence>
            {status && (
              <motion.div
                className="flex max-w-[85%] flex-col gap-2"
                initial={reduceMotion ? false : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, transition: { duration: 0.15 } }}
                transition={springIn}
              >
                <div className="flex items-center gap-2 text-xs">
                  {/* 处理中的实时状态：shimmer 流光扫过文字（reduced-motion 自动静态降级） */}
                  <span className="inline-block size-2 animate-pulse rounded-full bg-primary" />
                  <span className="shimmer text-muted-foreground">{status}</span>
                </div>
                <div className="space-y-2 rounded-xl rounded-bl-sm border border-border bg-card px-4 py-3 shadow-xs">
                  <Skeleton className="h-3.5 w-3/4" />
                  <Skeleton className="h-3.5 w-1/2" />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <div ref={bottomRef} />
        </main>
      </ScrollArea>

      <footer className="shrink-0 border-t border-border bg-card px-6 py-3">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          className="mx-auto flex w-full max-w-4xl gap-2"
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入您的问题…（支持中文 / English）"
            className="h-10 rounded-lg"
            aria-label="问题输入框"
          />
          <Button
            type="submit"
            size="icon-lg"
            className="rounded-lg active:scale-[0.98]"
            disabled={busy || !input.trim()}
            aria-label="发送"
          >
            <ArrowUp className="size-5" />
          </Button>
        </form>
      </footer>

      <TicketDialog
        open={showTicket}
        onOpenChange={setShowTicket}
        sessionId={sessionRef.current ?? "no-session"}
        onCreated={(id) =>
          setMessages((prev) => [
            ...prev,
            {
              role: "assistant",
              content: `工单已创建成功，编号 ${id}。人工客服将尽快与您联系，请保持联系方式畅通。`,
            },
          ])
        }
      />
    </div>
  );
}

function TicketDialog({
  open,
  onOpenChange,
  sessionId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  sessionId: string;
  onCreated: (id: string) => void;
}) {
  const [contact, setContact] = useState("");
  const [model, setModel] = useState("");
  const [desc, setDesc] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!contact.trim() || !model.trim() || !desc.trim()) {
      setErr("请填写完整信息");
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      const res = await fetch("/api/ticket", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          contact,
          product_model: model,
          description: desc,
        }),
      });
      const d = await res.json();
      if (d.ok) {
        onCreated(String(d.ticket_id));
        onOpenChange(false);
      } else {
        setErr("提交失败，请重试");
      }
    } catch {
      setErr("网络异常，请重试");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>转人工 · 创建工单</DialogTitle>
          <DialogDescription>
            提交后人工客服将跟进您的问题，对话记录会一并转交。
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="space-y-4"
        >
          <div className="grid gap-2">
            <Label htmlFor="ticket-contact">联系方式</Label>
            <Input
              id="ticket-contact"
              value={contact}
              onChange={(e) => setContact(e.target.value)}
              placeholder="手机号或邮箱"
            />
            <p className="text-xs text-muted-foreground">用于客服回访，请确保可以联系到您</p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ticket-model">产品型号</Label>
            <Input
              id="ticket-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="如 ThinkPad X1 Carbon"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="ticket-desc">问题描述</Label>
            <Textarea
              id="ticket-desc"
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={3}
              className="resize-none"
              placeholder="请简述遇到的问题"
            />
            {err && <p className="text-xs text-destructive">{err}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={submitting} className="active:scale-[0.98]">
              {submitting ? "提交中…" : "提交工单"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function stageText(stage: string): string {
  const map: Record<string, string> = {
    accepted: "已接收，正在处理…",
    routing: "正在理解您的问题…",
    retrieving: "正在检索知识库…",
    grading: "正在评估检索结果…",
    generating: "正在生成回答…",
  };
  return map[stage] ?? "处理中…";
}
