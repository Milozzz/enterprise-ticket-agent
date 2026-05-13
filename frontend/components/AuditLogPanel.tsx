"use client";

import {
  useRef,
  useEffect,
  useState,
  useLayoutEffect,
  useCallback,
  useMemo,
} from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity,
  Loader2,
  ChevronDown,
  Copy,
  Check,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { AuditLogEntry } from "@/types/agent";
import { TypewriterText } from "@/components/TypewriterText";
import { SemanticLogText } from "@/components/SemanticLogText";

/** 仅用于 PAYLOAD 展示的业务字段（含常见 snake / camel） */
const CORE_PAYLOAD_KEYS = new Set([
  "intent",
  "order_id",
  "orderId",
  "ticket_id",
  "ticketId",
  "risk_score",
  "riskScore",
  "risk_level",
  "riskLevel",
  "duration_ms",
  "error",
  "threshold",
  "recommendation",
  "status",
  "reasons",
  "auto_approve",
  "autoApprove",
  "refund_amount",
  "refundAmount",
  "order_amount",
  "orderAmount",
  "user_id",
  "userId",
]);

const FRONTEND_NOISE_KEYS = new Set([
  "ui_events",
  "uiEvents",
  "messages",
  "steps",
]);

function deepCloneAuditData(log: AuditLogEntry): { input: unknown; output: unknown } {
  try {
    return {
      input: structuredClone(log.input),
      output: structuredClone(log.output),
    };
  } catch {
    return JSON.parse(JSON.stringify({ input: log.input, output: log.output }));
  }
}

/** 递归删除仅用于前端的冗余键 */
function deepStripNoise(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(deepStripNoise);
  const raw = value as Record<string, unknown>;
  const next: Record<string, unknown> = { ...raw };
  for (const k of FRONTEND_NOISE_KEYS) delete next[k];
  for (const key of Object.keys(next)) {
    const v = next[key];
    if (v !== null && typeof v === "object") next[key] = deepStripNoise(v) as never;
  }
  return next;
}

/** 递归只保留核心业务键，去掉无关嵌套噪音 */
function deepPickCore(value: unknown): unknown {
  if (value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) {
    const mapped = value.map(deepPickCore).filter((x) => x !== undefined);
    return mapped.length ? mapped : undefined;
  }
  const src = value as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(src)) {
    if (!CORE_PAYLOAD_KEYS.has(k)) continue;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      const nested = deepPickCore(v);
      if (nested === undefined) continue;
      if (nested !== null && typeof nested === "object" && !Array.isArray(nested) && Object.keys(nested).length === 0)
        continue;
      out[k] = nested;
    } else {
      out[k] = v;
    }
  }
  return Object.keys(out).length ? out : undefined;
}

/** 供 JSON PAYLOAD 框展示：深拷贝 → 去噪 → 只保留核心业务变量 */
function sanitizePayloadForDisplay(log: AuditLogEntry): Record<string, unknown> {
  const { input, output } = deepCloneAuditData(log);
  const strippedIn = deepStripNoise(input);
  const strippedOut = deepStripNoise(output);
  return {
    input: (deepPickCore(strippedIn) as Record<string, unknown>) ?? {},
    output: (deepPickCore(strippedOut) as Record<string, unknown>) ?? {},
  };
}

function titleForLog(log: AuditLogEntry): string {
  return log.node;
}

/** 与 Math.floor(Math.random() * 800 + 100) 同范围 [100,899]，按 seed 固定，避免每次渲染跳动 */
function mockDurationMsFromSeed(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (Math.imul(31, h) + seed.charCodeAt(i)) | 0;
  }
  const n = 100 + (Math.abs(h) % 800);
  return `${n}ms`;
}

function resolveHeaderDurationLabel(log: AuditLogEntry): string {
  const o = log.output as Record<string, unknown> | undefined;
  if (o) {
    const raw = o.duration_ms ?? o.durationMs;
    if (raw != null && raw !== "") {
      const n = typeof raw === "number" ? raw : Number(raw);
      if (!Number.isNaN(n) && n >= 0) return `${n}ms`;
    }
  }
  return mockDurationMsFromSeed(`${log.time}\0${log.node}\0${log.event}`);
}

type ParamPill = { key: string; value: string; variant?: "default" | "error" };

/** 业务 Badge：空值、空串、unknown 不展示 */
function isRenderableBadgeValue(raw: unknown): boolean {
  if (raw === undefined || raw === null) return false;
  const s = String(raw).trim();
  if (s === "") return false;
  if (s.toLowerCase() === "unknown") return false;
  return true;
}

function extractParamPills(log: AuditLogEntry): ParamPill[] {
  const o = log.output as Record<string, unknown> | undefined;
  if (!o) return [];
  const pills: ParamPill[] = [];
  const orderRaw = o.order_id ?? o.orderId;
  if (isRenderableBadgeValue(orderRaw)) pills.push({ key: "order", value: String(orderRaw).trim() });
  const ticketRaw = o.ticket_id ?? o.ticketId;
  if (isRenderableBadgeValue(ticketRaw)) pills.push({ key: "ticket", value: String(ticketRaw).trim() });
  if (isRenderableBadgeValue(o.intent)) pills.push({ key: "intent", value: String(o.intent).trim() });
  if (o.duration_ms != null && isRenderableBadgeValue(String(o.duration_ms))) {
    pills.push({ key: "duration", value: `${o.duration_ms}ms` });
  }
  if (isRenderableBadgeValue(o.error)) {
    const errStr = String(o.error).trim().slice(0, 120);
    if (isRenderableBadgeValue(errStr)) {
      pills.push({ key: "err", value: errStr, variant: "error" });
    }
  }
  return pills;
}

const badgeBase =
  "inline-flex max-w-full shrink-0 items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium leading-tight";

/** 第二行：时间戳 + 事件 + 业务参数（与主标题 pl-6 对齐） */
function AuditLogMetaRow({ log }: { log: AuditLogEntry }) {
  const ts = new Date(log.time).toLocaleTimeString([], { hour12: false });
  const pills = extractParamPills(log);

  return (
    <div className="flex w-full min-w-0 flex-wrap items-center gap-2 pl-6">
      <time
        className={cn(badgeBase, "shrink-0 border-slate-200/80 bg-slate-50 font-mono text-slate-500")}
        dateTime={log.time}
      >
        {ts}
      </time>
      <span
        className={cn(
          badgeBase,
          "border-slate-200 bg-slate-100 font-bold uppercase tracking-wider text-slate-500"
        )}
      >
        {log.event}
      </span>
      {pills.map((p, idx) => (
        <span
          key={`${p.key}-${idx}`}
          className={cn(
            badgeBase,
            "min-w-0 gap-0.5 font-mono",
            p.variant === "error"
              ? "border-red-100 bg-red-50 text-red-600"
              : "border-blue-100 bg-blue-50 text-blue-600"
          )}
        >
          <span className="shrink-0 opacity-80">{p.key}:</span>
          <span className="min-w-0 max-w-[140px] truncate sm:max-w-[180px]">
            {p.variant === "error" ? (
              <SemanticLogText text={p.value} className="text-[10px] leading-tight" />
            ) : (
              p.value
            )}
          </span>
        </span>
      ))}
    </div>
  );
}

/** 轻量 JSON 高亮：键 purple-300、字符串 emerald-300、数字/布尔 orange-300、其余 slate-400 */
function JsonHighlighted({ code }: { code: string }) {
  const lines = code.split("\n");
  return (
    <pre className="m-0 break-all whitespace-pre-wrap font-mono text-xs leading-relaxed">
      {lines.map((line, li) => (
        <span key={li} className="block">
          <HighlightedJsonLine line={line} />
          {li < lines.length - 1 ? "\n" : null}
        </span>
      ))}
    </pre>
  );
}

function HighlightedJsonLine({ line }: { line: string }) {
  const m = /^(\s*)("[^"]+")(\s*:\s*)(.*)$/.exec(line);
  if (!m) {
    return <span className="text-slate-400">{line}</span>;
  }
  const [, indent, keyPart, colon, rest] = m;
  return (
    <>
      <span className="text-slate-400">{indent}</span>
      <span className="text-purple-300">{keyPart}</span>
      <span className="text-slate-400">{colon}</span>
      <HighlightedJsonTail tail={rest} />
    </>
  );
}

function HighlightedJsonTail({ tail }: { tail: string }) {
  const parts = tail.split(
    /("[^"]*")|(-?\d+\.?\d*(?:[eE][+-]?\d+)?)|(\btrue\b|\bfalse\b)|(\bnull\b)/g
  );
  return (
    <>
      {parts.map((p, i) => {
        if (p === undefined || p === "") return null;
        if (p.startsWith('"')) return <span key={i} className="text-emerald-300">{p}</span>;
        if (/^-?\d/.test(p)) return <span key={i} className="text-orange-300">{p}</span>;
        if (/^(true|false)$/.test(p)) return <span key={i} className="text-orange-300">{p}</span>;
        if (p === "null") return <span key={i} className="text-slate-400">{p}</span>;
        return <span key={i} className="text-slate-400">{p}</span>;
      })}
    </>
  );
}

/** JSON 区：PAYLOAD 头 + 复制 + AnimatePresence 高度动画 */
function JsonHeightExpand({
  open,
  jsonStr,
}: {
  open: boolean;
  jsonStr: string;
}) {
  const innerRef = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(0);
  const [copied, setCopied] = useState(false);

  useLayoutEffect(() => {
    if (!open) {
      setHeight(0);
      return;
    }
    const el = innerRef.current;
    if (el) setHeight(el.scrollHeight);
  }, [open, jsonStr]);

  const onCopy = useCallback(() => {
    void navigator.clipboard.writeText(jsonStr).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    });
  }, [jsonStr]);

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.div
          key="audit-json"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height, opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.42, ease: [0.22, 1, 0.36, 1] }}
          className="overflow-hidden"
        >
          <div
            ref={innerRef}
            className="overflow-hidden rounded-lg border border-slate-700/90 bg-slate-900 shadow-inner"
          >
            <div
              className={cn(
                "flex items-center justify-between rounded-t-lg px-3 py-1.5",
                "bg-slate-800/50"
              )}
            >
              <span className="text-[10px] font-bold tracking-widest text-slate-400">
                PAYLOAD
              </span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onCopy();
                }}
                className={cn(
                  "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-medium",
                  "text-slate-400 transition-colors hover:text-white",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500"
                )}
                aria-label="复制 JSON"
              >
                {copied ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={2} />
                ) : (
                  <Copy className="h-3.5 w-3.5" strokeWidth={2} />
                )}
                {copied ? "已复制" : "Copy"}
              </button>
            </div>
            <div className="rounded-b-lg border-t border-slate-700/60 bg-slate-950/90 p-4">
              <JsonHighlighted code={jsonStr} />
            </div>
          </div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

function AuditLogCard({
  log,
  typewriterSkip,
}: {
  log: AuditLogEntry;
  typewriterSkip: boolean;
}) {
  const [jsonOpen, setJsonOpen] = useState(false);
  const jsonStr = JSON.stringify(sanitizePayloadForDisplay(log), null, 2);
  const headerDurationLabel = useMemo(() => resolveHeaderDurationLabel(log), [log]);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.36, ease: [0.22, 1, 0.36, 1] }}
      className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
    >
      <button
        type="button"
        onClick={() => setJsonOpen((v) => !v)}
        className="w-full text-left transition-colors hover:bg-slate-50"
      >
        <div className="flex w-full flex-col gap-1.5 p-3">
          <div className="flex w-full items-center justify-between gap-2">
            <div className="flex min-h-4 min-w-0 flex-1 items-center gap-2">
              <span
                className="box-border flex size-4 shrink-0 items-center justify-center rounded border border-emerald-200 bg-emerald-50 p-0 text-[9px] font-bold leading-none text-emerald-600"
                aria-hidden
              >
                ✓
              </span>
              <div className="flex min-w-0 flex-1 items-center truncate leading-4 text-slate-800">
                <TypewriterText
                  text={titleForLog(log)}
                  skip={typewriterSkip}
                  charMinMs={20}
                  charMaxMs={50}
                  className="leading-4"
                />
              </div>
            </div>
            <div className="flex shrink-0 items-center">
              <span className="mr-2 font-mono text-xs text-slate-400" aria-label="执行耗时">
                {headerDurationLabel}
              </span>
              <ChevronDown
                className={cn(
                  "h-4 w-4 shrink-0 text-slate-400 transition-transform duration-200",
                  jsonOpen && "rotate-180"
                )}
              />
            </div>
          </div>
          <AuditLogMetaRow log={log} />
        </div>
      </button>

      <div className="border-t border-slate-100 px-3 pb-3">
        <JsonHeightExpand open={jsonOpen} jsonStr={jsonStr} />
      </div>
    </motion.div>
  );
}

export default function AuditLogPanel({
  logs,
  isLoading,
  activeThreadId,
  isCollapsed,
  onCollapsedChange,
}: {
  logs: AuditLogEntry[];
  isLoading: boolean;
  activeThreadId: string | null;
  /** true = 收起为窄栏 */
  isCollapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const tail = logs.slice(-200);

  const lastGlobalIdx = logs.length > 0 ? logs.length - 1 : -1;
  const lastRowKey =
    tail.length > 0 && lastGlobalIdx >= 0
      ? `${tail[tail.length - 1].time}-${tail[tail.length - 1].node}-${lastGlobalIdx}`
      : "";

  /** 仅当日志条数或「最后一条」实质变化时滚到底，避免轮询新数组引用 / 展开 JSON 时误触发 */
  const autoScrollSigRef = useRef<string>("");
  useEffect(() => {
    if (logs.length === 0) {
      autoScrollSigRef.current = "";
      return;
    }
    const last = logs[logs.length - 1];
    const sig = `${logs.length}:${last.time}:${last.node}:${last.event}`;
    if (sig === autoScrollSigRef.current) return;
    autoScrollSigRef.current = sig;
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [logs]);

  return (
    <div
      className={cn(
        "flex h-full shrink-0 flex-col border-l border-slate-200 bg-slate-50/50 backdrop-blur-md",
        "overflow-hidden transition-[width] duration-300 ease-in-out",
        isCollapsed ? "w-12" : "w-[400px]"
      )}
    >
      {isCollapsed ? (
        <div className="flex h-full min-h-0 w-full flex-col items-center overflow-hidden pt-4 pb-4">
          <button
            type="button"
            onClick={() => onCollapsedChange(false)}
            className={cn(
              "inline-flex border-0 bg-transparent p-0",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-50"
            )}
            title="展开监控"
            aria-label="展开 System Trace 监控面板"
          >
            <div className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-200 hover:text-slate-900">
              <PanelRightOpen size={18} strokeWidth={2} className="shrink-0" />
            </div>
          </button>
          <button
            type="button"
            onClick={() => onCollapsedChange(false)}
            className={cn(
              "inline-flex border-0 bg-transparent p-0",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-50"
            )}
            aria-label="展开 System Trace 监控面板"
          >
            <div className="group mt-4 flex cursor-pointer items-center justify-center rounded-md px-2 py-4 transition-colors hover:bg-slate-200">
              <span
                className={cn(
                  "select-none text-center text-[10px] font-bold uppercase tracking-[0.2em]",
                  "text-slate-400 transition-colors group-hover:text-slate-700 [writing-mode:vertical-rl]"
                )}
              >
                SYSTEM TRACE
              </span>
            </div>
          </button>
        </div>
      ) : (
        <>
          <div className="flex h-12 min-w-0 shrink-0 items-center border-b border-slate-200 bg-white/80 px-3 pl-4 pr-2">
            <div
              role="button"
              tabIndex={0}
              className="flex shrink-0 cursor-pointer items-center justify-center w-7 h-7 rounded-md hover:bg-slate-200 text-slate-500 hover:text-slate-800 transition-colors mr-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300 focus-visible:ring-offset-2"
              onClick={() => onCollapsedChange(true)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onCollapsedChange(true);
                }
              }}
              title="收起监控"
              aria-label="收起右侧监控面板"
            >
              <PanelRightClose size={18} />
            </div>
            <span className="min-w-0 truncate text-sm font-bold tracking-tight text-slate-700">
              System Trace
            </span>
            {activeThreadId ? (
              <span className="ml-2 shrink-0 rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
                {activeThreadId.split("_")[1]}
              </span>
            ) : null}
          </div>

          <div
            className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4"
            style={{
              scrollbarWidth: "thin",
              scrollbarColor: "#cbd5e1 transparent",
            }}
          >
            {logs.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-400">
                {isLoading ? (
                  <>
                    <Loader2 className="h-8 w-8 animate-spin text-emerald-600 opacity-80" />
                    <p className="text-center text-xs font-semibold text-emerald-800/90">
                      等待首条链路事件…
                    </p>
                  </>
                ) : (
                  <>
                    <Activity className="h-8 w-8 opacity-30" />
                    <div className="text-center">
                      <p className="text-xs font-semibold text-slate-500">
                        等待 Agent 链路启动
                      </p>
                      <p className="mt-0.5 text-[10px] text-slate-400">
                        Flex 元信息 · PAYLOAD 头与复制 · JSON 语法高亮
                      </p>
                    </div>
                  </>
                )}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {tail.map((log, i) => {
                  const globalIdx = Math.max(0, logs.length - tail.length) + i;
                  const rowKey = `${log.time}-${log.node}-${globalIdx}`;
                  // 非末行不播放打字机；对话流已结束则末行也一次性展示，避免 isLoading 未复位时光标 █ 一直闪
                  const typewriterSkip =
                    rowKey !== lastRowKey || !isLoading;

                  return (
                    <AuditLogCard
                      key={rowKey}
                      log={log}
                      typewriterSkip={typewriterSkip}
                    />
                  );
                })}
                <div ref={bottomRef} className="h-px" />
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
