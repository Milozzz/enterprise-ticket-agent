import { type NextRequest, NextResponse } from "next/server";
import { StreamData, StreamingTextResponse, formatStreamPart } from "ai";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

export const maxDuration = 60;

// Langfuse JS SDK —— 仅在 server-side route handler 中使用
// 如果 key 未配置，getLangfuse() 返回 null，追踪静默跳过
function getLangfuse() {
  const publicKey = process.env.LANGFUSE_PUBLIC_KEY ?? "";
  const secretKey = process.env.LANGFUSE_SECRET_KEY ?? "";
  const host = process.env.LANGFUSE_HOST ?? "https://cloud.langfuse.com";

  if (!publicKey || !secretKey || publicKey.startsWith("pk-lf-...")) {
    return null;
  }

  try {
    // 动态 require 避免在 key 未配置时报错
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { Langfuse } = require("langfuse");
    return new Langfuse({ publicKey, secretKey, host });
  } catch {
    return null;
  }
}

/**
 * POST /api/chat
 * 适配器：将 Python FastAPI/LangGraph 的自定义 SSE 协议
 * 转换为 Vercel AI SDK v3 的数据流协议（供 useChat 消费）。
 *
 * Python SSE 协议：
 *   event: text     data: {"content":"..."}
 *   event: ui       data: {"type":"OrderCard","props":{...}}
 *   event: done     data: {}
 *
 * AI SDK v3 数据流协议：
 *   0:"text chunk"\n        → 文本片段
 *   2:[{...}]\n             → 自定义数据（UI 事件）
 *   d:{finishReason}\n      → 结束标记
 *
 * Langfuse 追踪：
 *   - 每次 POST 创建一个 trace（thread_id 作为 sessionId）
 *   - span 记录请求耗时、用户消息、最终文本输出
 *   - 与后端 CallbackHandler trace 通过同一 sessionId 关联
 */
export async function POST(req: NextRequest) {
  const startTime = Date.now();
  const lf = getLangfuse();

  try {
    const body = await req.json();
    const { message, messages: msgsFromClient, user_id, user_role, thread_id, trace_id } = body;

    const messages =
      msgsFromClient || (message ? [{ role: "user", content: message }] : []);

    const userMessage = messages[messages.length - 1]?.content ?? "";

    // ── Langfuse: 创建 trace（前端侧 API 请求）──
    const trace = lf?.trace({
      name: "frontend_chat_request",
      sessionId: thread_id,
      userId: user_id ? String(user_id) : undefined,
      input: { message: userMessage, role: user_role },
      metadata: { thread_id, trace_id, user_role, source: "nextjs_route_handler" },
    });

    const span = trace?.span({
      name: "proxy_to_backend",
      input: { url: `${BACKEND_URL}/api/agent/chat`, thread_id },
    });

    const backendRes = await fetch(`${BACKEND_URL}/api/agent/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Role": req.headers.get("X-User-Role") ?? user_role ?? "",
        "X-User-Id": req.headers.get("X-User-Id") ?? user_id ?? "",
      },
      body: JSON.stringify({
        messages,
        thread_id: thread_id || `thread_${Date.now()}`,
        trace_id: trace_id || thread_id,
        user_role,
        user_id,
      }),
    });

    if (!backendRes.ok) {
      const error = await backendRes.text();
      span?.end({ output: { error }, level: "ERROR" });
      trace?.update({ output: { error }, metadata: { status: backendRes.status } });
      await lf?.shutdownAsync();
      return NextResponse.json(
        { error: `Backend error: ${error}` },
        { status: backendRes.status }
      );
    }

    // StreamData 用于将 UI 事件等自定义数据附加到消息上
    const streamData = new StreamData();

    const encoder = new TextEncoder();
    let accumulatedText = "";

    const readable = new ReadableStream({
      async start(controller) {
        const reader = backendRes.body!.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        /** 处理一个完整的 SSE 块（event+data 对） */
        function processBlock(block: string) {
          let evtType = "";
          let evtData = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event: ")) evtType = line.slice(7).trim();
            else if (line.startsWith("data: ")) evtData = line.slice(6).trim();
          }
          if (!evtData) return;
          try {
            const payload = JSON.parse(evtData);
            if (evtType === "text") {
              const text = payload.content ?? "";
              if (text) {
                accumulatedText += text;
                controller.enqueue(encoder.encode(formatStreamPart("text", text)));
              }
            } else if (evtType === "ui") {
              controller.enqueue(
                encoder.encode(
                  formatStreamPart("message_annotations", [
                    { type: "ui", uiType: payload.type, props: payload.props },
                  ])
                )
              );
            } else if (evtType === "error") {
              streamData.append({ type: "error", message: payload.error });
            }
          } catch { /* 解析失败跳过 */ }
        }

        try {
          while (true) {
            const { done, value } = await reader.read();

            // 即使 done=true，value 里也可能有最后一批数据（HTTP 分块结束帧）
            if (value) {
              buffer += decoder.decode(value, { stream: !done });
            }
            if (done) break;

            // 按完整 SSE 块（双换行）分割
            const blocks = buffer.split("\n\n");
            buffer = blocks.pop() ?? "";
            for (const block of blocks) processBlock(block);
          }

          // 流结束后，处理缓冲区中可能残留的最后一个块
          if (buffer.trim()) processBlock(buffer.trim());
        } finally {
          reader.releaseLock();

          const durationMs = Date.now() - startTime;

          // ── Langfuse: 结束 span + trace，记录输出和耗时 ──
          span?.end({
            output: { text_length: accumulatedText.length, duration_ms: durationMs },
          });
          trace?.update({
            output: { response: accumulatedText.slice(0, 500) },
            metadata: { duration_ms: durationMs, status: "success" },
          });
          // 异步 flush（不阻塞流关闭）
          lf?.shutdownAsync().catch(() => {});

          // 发送结束标记
          controller.enqueue(
            encoder.encode(
              formatStreamPart("finish_message", {
                finishReason: "stop",
                usage: { promptTokens: 0, completionTokens: 0 },
              })
            )
          );
          controller.close();
          streamData.close();
        }
      },
    });

    return new StreamingTextResponse(readable, {}, streamData);
  } catch (error) {
    console.error("[Chat API Adapter] Error:", error);
    await lf?.shutdownAsync().catch(() => {});
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}

/**
 * PUT /api/chat
 * 人工审批操作 — 转发到 Python 后端 /api/agent/resume
 * Day 9: 后端现在返回 SSE 流，此处直接透传给前端 ApprovalPanel
 */
export async function PUT(req: NextRequest) {
  try {
    const { threadId, action, reviewerId, reviewerRole } = await req.json();

    const lf = getLangfuse();
    const trace = lf?.trace({
      name: "frontend_approval_request",
      sessionId: threadId,
      userId: reviewerId ? String(reviewerId) : undefined,
      input: { action, reviewerRole },
      metadata: { thread_id: threadId, source: "nextjs_route_handler" },
    });

    const backendRes = await fetch(`${BACKEND_URL}/api/agent/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        thread_id: threadId,
        action,
        reviewer_id: reviewerId,
        reviewer_role: reviewerRole ?? "AGENT",
      }),
    });

    if (!backendRes.ok) {
      const err = await backendRes.json().catch(() => ({ detail: "审批请求失败" }));
      trace?.update({ output: { error: err }, metadata: { status: backendRes.status } });
      await lf?.shutdownAsync().catch(() => {});
      return NextResponse.json(err, { status: backendRes.status });
    }

    trace?.update({ output: { status: "proxied" }, metadata: { status: 200 } });
    lf?.shutdownAsync().catch(() => {});

    // 直接将后端 SSE 流透传给前端（ApprovalPanel 会消费这个流）
    return new Response(backendRes.body, {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
      },
    });
  } catch (error) {
    console.error("[Approve API] Error:", error);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
