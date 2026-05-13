/**
 * evals/helpers.ts
 * SSE 解析 + Agent API 客户端工具
 */

import * as http from "http";
import * as https from "https";

export const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

// ── 类型定义 ──────────────────────────────────────────────────────────────

export interface SseEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface AgentResult {
  /** 所有 SSE 事件（原始） */
  events: SseEvent[];
  /** 所有 text 事件的 content 拼接 */
  fullText: string;
  /** 所有 ui 事件的 type 列表 */
  uiTypes: string[];
  /** 所有 AgentThinkingStream steps 的 label 列表 */
  thinkingStepLabels: string[];
  /** HTTP 状态码 */
  status: number;
}

// ── SSE 解析 ──────────────────────────────────────────────────────────────

export function parseSseText(raw: string): SseEvent[] {
  const events: SseEvent[] = [];
  const blocks = raw.split(/\n\n+/);

  for (const block of blocks) {
    if (!block.trim()) continue;
    let eventType = "message";
    let dataStr = "";

    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) eventType = line.slice(7).trim();
      if (line.startsWith("data: ")) dataStr = line.slice(6).trim();
    }

    if (dataStr) {
      try {
        events.push({ type: eventType, data: JSON.parse(dataStr) });
      } catch {
        events.push({ type: eventType, data: { raw: dataStr } });
      }
    }
  }

  return events;
}

// ── HTTP 请求（使用 Node 内置 http/https，不需要 node-fetch）────────────────

function httpPost(url: string, body: object): Promise<{ status: number; text: string }> {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const parsed = new URL(url);
    const lib = parsed.protocol === "https:" ? https : http;

    const req = lib.request(
      {
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
        path: parsed.pathname + parsed.search,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
        },
        timeout: 55000,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (c: Buffer) => chunks.push(c));
        res.on("end", () =>
          resolve({ status: res.statusCode ?? 0, text: Buffer.concat(chunks).toString("utf-8") })
        );
      }
    );

    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("Request timeout")); });
    req.write(payload);
    req.end();
  });
}

// ── Agent 调用入口 ─────────────────────────────────────────────────────────

let _testCounter = 0;

export async function callAgent(
  message: string,
  options: { userId?: string; userRole?: string; threadId?: string } = {}
): Promise<AgentResult> {
  _testCounter++;
  const threadId = options.threadId ?? `eval-thread-${Date.now()}-${_testCounter}`;
  const userId = options.userId ?? "eval_user";
  const userRole = options.userRole ?? "user";

  const { status, text } = await httpPost(`${BACKEND_URL}/api/agent/chat`, {
    messages: [{ role: "user", content: message }],
    thread_id: threadId,
    user_id: userId,
    user_role: userRole,
  });

  const events = parseSseText(text);

  const fullText = events
    .filter((e) => e.type === "text")
    .map((e) => (e.data as { content?: string }).content ?? "")
    .join("");

  const uiTypes = events
    .filter((e) => e.type === "ui")
    .map((e) => (e.data as { type?: string }).type ?? "");

  const thinkingStepLabels: string[] = [];
  for (const e of events) {
    if (e.type !== "ui") continue;
    const props = (e.data as { props?: { steps?: { label?: string }[] } }).props;
    for (const step of props?.steps ?? []) {
      if (step.label) thinkingStepLabels.push(step.label);
    }
  }

  return { events, fullText, uiTypes, thinkingStepLabels, status };
}

// ── 健康检查 ──────────────────────────────────────────────────────────────

export async function isBackendAvailable(): Promise<boolean> {
  try {
    const { status } = await httpPost(`${BACKEND_URL}/health`, {});
    return status < 500;
  } catch {
    return false;
  }
}

// ── 断言辅助 ──────────────────────────────────────────────────────────────

export function assertResult(
  result: AgentResult,
  caseName: string,
  assertions: {
    textContains?: string[];
    textNotContains?: string[];
    hasUiType?: string[];
    noUiType?: string[];
    hasStep?: string[];
  }
): void {
  const { fullText, uiTypes, thinkingStepLabels } = result;

  if (assertions.textContains) {
    const matched = assertions.textContains.some((s) =>
      fullText.toLowerCase().includes(s.toLowerCase())
    );
    if (!matched) {
      throw new Error(
        `[${caseName}] textContains 断言失败\n` +
        `  期望包含（任一）: ${assertions.textContains.join(" | ")}\n` +
        `  实际文本: ${fullText.slice(0, 300)}`
      );
    }
  }

  if (assertions.textNotContains) {
    for (const s of assertions.textNotContains) {
      if (fullText.toLowerCase().includes(s.toLowerCase())) {
        throw new Error(
          `[${caseName}] textNotContains 断言失败\n` +
          `  文本中不应包含: "${s}"\n` +
          `  实际文本: ${fullText.slice(0, 300)}`
        );
      }
    }
  }

  if (assertions.hasUiType) {
    for (const t of assertions.hasUiType) {
      if (!uiTypes.includes(t)) {
        throw new Error(
          `[${caseName}] hasUiType 断言失败\n` +
          `  期望出现 UI 事件类型: "${t}"\n` +
          `  实际 UI 类型: [${uiTypes.join(", ")}]`
        );
      }
    }
  }

  if (assertions.noUiType) {
    for (const t of assertions.noUiType) {
      if (uiTypes.includes(t)) {
        throw new Error(
          `[${caseName}] noUiType 断言失败\n` +
          `  不应出现 UI 事件类型: "${t}"\n` +
          `  实际 UI 类型: [${uiTypes.join(", ")}]`
        );
      }
    }
  }

  if (assertions.hasStep) {
    for (const label of assertions.hasStep) {
      const matched = thinkingStepLabels.some((l) =>
        l.toLowerCase().includes(label.toLowerCase())
      );
      if (!matched) {
        throw new Error(
          `[${caseName}] hasStep 断言失败\n` +
          `  期望出现 thinking step: "${label}"\n` +
          `  实际 steps: [${thinkingStepLabels.join(", ")}]`
        );
      }
    }
  }
}
