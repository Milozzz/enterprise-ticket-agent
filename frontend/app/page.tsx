"use client";

import {
  useRef,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
  type ComponentType,
} from "react";
import { useChat } from "ai/react";
import { Header } from "@/components/Header";
import { useAuthStore } from "@/store/authStore";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Send, BotMessageSquare, User as UserIcon, Loader2, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { UiEvent, AuditLogEntry } from "@/types/agent";
import AuditLogPanel from "@/components/AuditLogPanel";
import ErrorBoundary from "@/components/ErrorBoundary";

import dynamic from "next/dynamic";
// Generative UI 仅在客户端挂载，否则 SSR 阶段子组件抛错时 Error Boundary 接不住，会触发 Next 全屏红屏
const dyn = (loader: () => Promise<{ default: ComponentType<any> }>) =>
  dynamic(loader, { ssr: false });
const OrderCard = dyn(() => import("@/components/generative/OrderCard"));
const ApprovalPanel = dyn(() => import("@/components/generative/ApprovalPanel"));
const RiskAlert = dyn(() => import("@/components/generative/RiskAlert"));
const RefundTimeline = dyn(() => import("@/components/generative/RefundTimeline"));
const EmailPreview = dyn(() => import("@/components/generative/EmailPreview"));
const AgentThinkingStream = dyn(() =>
  import("@/components/generative/AgentThinkingStream")
);

export default function ChatPage() {
  const { currentRole, currentUserId } = useAuthStore();
  const [backendOk, setBackendOk]     = useState<boolean | null>(null);
  const [auditLogs, setAuditLogs]     = useState<AuditLogEntry[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [traceCollapsed, setTraceCollapsed] = useState(false);
  const scrollRef     = useRef<HTMLDivElement>(null);
  const nextThreadIdRef = useRef(`thread_${Date.now()}`);
  const nextTraceIdRef  = useRef(`trace_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`);
  const pollTimerRef  = useRef<NodeJS.Timeout | null>(null);

  // ── Vercel AI SDK useChat ──────────────────────────────────────
  const { messages, input, setInput, handleSubmit, isLoading } = useChat({
    api: "/api/chat",
    body: {
      user_id:   currentUserId,
      user_role: currentRole,
      thread_id: nextThreadIdRef.current,
    },
    headers: { "X-User-Role": currentRole, "X-User-Id": currentUserId },
    initialMessages: [{
      id: "welcome", role: "assistant",
      content: "您好！我是企业智能工单助手。请告诉我您的问题，例如：\n\n- 「订单号 123456 申请退款，商品破损」\n- 「查询订单 789012 的最新状态」",
    }],
  });

  // ── 审计日志轮询 ───────────────────────────────────────────────
  useEffect(() => {
    if (!activeThreadId) return;
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);

    const poll = async () => {
      try {
        const res = await fetch(`/api/agent/audit/${activeThreadId}`);
        if (res.ok) setAuditLogs(await res.json());
      } catch {}
      pollTimerRef.current = setTimeout(poll, 2000);
    };
    poll();
    return () => { if (pollTimerRef.current) clearTimeout(pollTimerRef.current); };
  }, [activeThreadId]);

  // ── 自动滚到底部 ───────────────────────────────────────────────
  useEffect(() => {
    const el = scrollRef.current?.querySelector("[data-radix-scroll-area-viewport]");
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, isLoading]);

  // ── 后端健康检查 ───────────────────────────────────────────────
  useEffect(() => {
    fetch("/api/health").then(r => setBackendOk(r.ok)).catch(() => setBackendOk(false));
  }, []);

  // ── 提交（每次生成新 thread_id） ──────────────────────────────
  const submitWithFreshThread = useCallback((e: React.FormEvent) => {
    const freshId = `thread_${Date.now()}`;
    const freshTraceId = `trace_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    nextThreadIdRef.current = freshId;
    nextTraceIdRef.current = freshTraceId;
    setActiveThreadId(freshId);
    setAuditLogs([]);
    handleSubmit(e, { body: { user_id: currentUserId, user_role: currentRole, thread_id: freshId, trace_id: freshTraceId } });
  }, [handleSubmit, currentUserId, currentRole]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (input.trim() && !isLoading) submitWithFreshThread(e as any);
    }
  };

  // ── 渲染单个 UI 事件 ──────────────────────────────────────────
  const renderUiEvent = useCallback((ui: UiEvent, key: number) => {
    if (!ui?.type) return null;
    const p = ui.props as any;
    let inner: ReactNode = null;
    switch (ui.type) {
      case "OrderCard":           inner = <OrderCard order={p} />; break;
      case "ApprovalPanel":       inner = <ApprovalPanel {...p} />; break;
      case "RiskAlert":           inner = <RiskAlert data={p} />; break;
      case "RefundTimeline":      inner = <RefundTimeline data={p} />; break;
      case "EmailPreview":        inner = <EmailPreview data={p} />; break;
      case "AgentThinkingStream": inner = <AgentThinkingStream {...p} />; break;
      default:                    inner = null;
    }
    if (inner == null) return null;
    return (
      <ErrorBoundary key={`${key}-${currentRole}`}>
        {inner}
      </ErrorBoundary>
    );
  }, [currentRole, currentUserId]);

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-slate-50">
      <Header />

      {backendOk === false && (
        <div className="bg-destructive/10 text-destructive text-[11px] py-1.5 px-4 flex items-center justify-center gap-2 border-b border-destructive/20 font-medium shrink-0">
          <AlertTriangle className="h-3 w-3" />
          后端服务未连接，请检查服务状态
        </div>
      )}

      {/* ── 主内容区：左聊天 + 右审计面板 ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── 左侧：聊天区域 ── */}
        <main className="flex-1 relative flex flex-col overflow-hidden">
          <ScrollArea className="flex-1 px-4" ref={scrollRef}>
            <div className="max-w-3xl mx-auto space-y-8 py-10 pb-40">
              {messages.map((msg) => {
                const uiEvents: UiEvent[] = msg.role === "assistant"
                  ? ((msg.annotations ?? []) as any[])
                      .filter(a => a?.type === "ui")
                      .map(a => ({ type: a.uiType as string, props: a.props }))
                  : [];

                return (
                  <div
                    key={msg.id}
                    className={cn(
                      "flex w-full gap-4 animate-in fade-in slide-in-from-bottom-2 duration-300",
                      msg.role === "user" ? "flex-row-reverse" : "flex-row"
                    )}
                  >
                    <div className={cn(
                      "flex h-9 w-9 shrink-0 select-none items-center justify-center rounded-xl border shadow-sm",
                      msg.role === "assistant"
                        ? "bg-primary text-primary-foreground shadow-primary/20"
                        : "bg-background"
                    )}>
                      {msg.role === "assistant"
                        ? <BotMessageSquare className="h-5 w-5" />
                        : <UserIcon className="h-5 w-5 text-muted-foreground" />
                      }
                    </div>

                    <div className={cn(
                      "flex flex-col gap-3 max-w-[85%]",
                      msg.role === "user" ? "items-end" : "items-start"
                    )}>
                      {msg.content && (
                        <div className={cn(
                          "rounded-2xl px-4 py-2.5 text-[14px] shadow-sm leading-relaxed whitespace-pre-wrap",
                          msg.role === "user"
                            ? "bg-primary text-primary-foreground rounded-tr-none"
                            : "bg-background border rounded-tl-none text-foreground"
                        )}>
                          {msg.content}
                        </div>
                      )}
                      {uiEvents.length > 0 && (
                        <div className="w-full min-w-[320px] md:min-w-[480px] space-y-3 animate-in zoom-in-95 duration-500">
                          {uiEvents.map((evt, i) => renderUiEvent(evt, i))}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}

              {isLoading && (() => {
                const last = messages[messages.length - 1];
                const ann  = (last?.annotations ?? []) as any[];
                return !last?.content && !ann.some(a => a?.type === "ui");
              })() && (
                <div className="flex gap-4">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl border bg-primary text-primary-foreground shadow-lg shadow-primary/20">
                    <BotMessageSquare className="h-5 w-5 animate-pulse" />
                  </div>
                  <div className="bg-background border rounded-2xl rounded-tl-none px-4 py-3 shadow-sm">
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  </div>
                </div>
              )}
            </div>
          </ScrollArea>

          {/* ── 输入框 ── */}
          <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-slate-50 via-slate-50 to-transparent pt-12 pb-8 px-4 flex justify-center">
            <Card className="w-full max-w-3xl shadow-2xl border-none bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80 ring-1 ring-black/5">
              <div className="relative flex items-center p-2">
                <Textarea
                  placeholder="输入工单内容，例如：订单号 789012 申请退款..."
                  className="min-h-[56px] w-full resize-none border-0 bg-transparent py-3 pr-14 focus-visible:ring-0 focus-visible:ring-offset-0 text-[14px]"
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={onKeyDown}
                />
                <Button
                  size="icon"
                  className="absolute right-3 h-10 w-10 rounded-xl transition-all hover:scale-105 active:scale-95 shadow-lg shadow-primary/20"
                  onClick={e => submitWithFreshThread(e as any)}
                  disabled={!input.trim() || isLoading}
                >
                  <Send className="h-5 w-5" />
                </Button>
              </div>
              <div className="px-4 py-2 border-t flex items-center justify-between bg-muted/20 rounded-b-xl">
                <span className="text-[10px] text-muted-foreground font-semibold uppercase tracking-widest">
                  AI Powered Agent System
                </span>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-muted-foreground font-medium uppercase tracking-wider">
                    Press Enter to send
                  </span>
                  <Badge variant="outline" className="text-[10px] font-mono bg-background border-none shadow-sm px-2">
                    ID: {currentUserId}
                  </Badge>
                  {activeThreadId && (
                    <Badge
                      variant="outline"
                      className="text-[10px] font-mono bg-background border-none shadow-sm px-2 cursor-pointer hover:bg-muted"
                      title="点击复制 Thread ID（用于链路回放）"
                      onClick={() => navigator.clipboard.writeText(activeThreadId)}
                    >
                      thread: {activeThreadId.slice(7, 20)}… 📋
                    </Badge>
                  )}
                </div>
              </div>
            </Card>
          </div>
        </main>

        {/* ── 右侧：审计面板（宽度动画收起 / 展开） ── */}
        <AuditLogPanel
          logs={auditLogs}
          isLoading={isLoading}
          activeThreadId={activeThreadId}
          isCollapsed={traceCollapsed}
          onCollapsedChange={setTraceCollapsed}
        />
      </div>
    </div>
  );
}
