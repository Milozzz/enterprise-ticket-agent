"use client";

import { useState, useRef } from "react";
import dynamic from "next/dynamic";
import { formatCurrency, getRiskLevel, cn } from "@/lib/utils";
import {
  AlertTriangle, CheckCircle, XCircle, Loader2, User,
  ShieldCheck, FileCheck, Banknote, Mail, Pause, Play, Timer, Zap, Clapperboard,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardFooter } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useAuthStore } from "@/store/authStore";

const RefundTimeline = dynamic(() => import("./RefundTimeline").then(m => m.default));

// ── 类型定义 ──────────────────────────────────────────────────────────
type StepStatus = "pending" | "running" | "success" | "failed";
type ApprovalState = "idle" | "loading" | "streaming" | "approved" | "rejected";

interface ExecutionStep {
  id: string;
  label: string;
  detail: string;
  icon: React.ComponentType<{ className?: string }>;
  status: StepStatus;
  duration: number;        // 实际耗时 ms
  startedAt: number | null;
  completedAt: number | null;
  dependsOn: string[];
}

interface StreamEvent {
  type: string;
  props: Record<string, unknown>;
}

interface ApprovalPanelProps {
  ticketId: string;
  threadId: string;
  orderAmount: number;
  riskScore: number;
}

// ── 每步基础延迟（ms）——有意设计成不同节奏 ─────────────────────────
const STEP_DELAYS: Record<string, number> = {
  auth:          480,
  update_ticket: 860,
  refund:        1380,
  notify:        760,
};

// ── 步骤定义（approve / reject 各一套）──────────────────────────────
const APPROVE_DEFS = [
  { id: "auth",          icon: ShieldCheck, label: "权限验证",     detail: "主管身份验证通过，具备审批权限",   dependsOn: [] },
  { id: "update_ticket", icon: FileCheck,   label: "更新工单状态", detail: "工单状态已更新为「已批准」",        dependsOn: ["auth"] },
  { id: "refund",        icon: Banknote,    label: "执行退款操作", detail: "向支付网关发起退款请求",            dependsOn: ["update_ticket"] },
  { id: "notify",        icon: Mail,        label: "发送通知邮件", detail: "财务团队已收到退款确认邮件",        dependsOn: ["refund"] },
];

const REJECT_DEFS = [
  { id: "auth",          icon: ShieldCheck, label: "权限验证",     detail: "主管身份验证通过",                  dependsOn: [] },
  { id: "update_ticket", icon: XCircle,     label: "更新工单状态", detail: "工单状态已更新为「已拒绝」",        dependsOn: ["auth"] },
];

function makeSteps(defs: typeof APPROVE_DEFS): ExecutionStep[] {
  return defs.map(d => ({ ...d, status: "pending", duration: 0, startedAt: null, completedAt: null }));
}

function formatMs(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

// ── 组件 ─────────────────────────────────────────────────────────────
export default function ApprovalPanel({
  ticketId, threadId, orderAmount, riskScore,
}: ApprovalPanelProps) {
  const [appState, setAppState]             = useState<ApprovalState>("idle");
  const [error, setError]                   = useState<string | null>(null);
  const [executionSteps, setExecutionSteps] = useState<ExecutionStep[]>([]);
  const [streamEvents, setStreamEvents]     = useState<StreamEvent[]>([]);
  const [isPaused, setIsPaused]             = useState(false);
  const [playbackSpeed, setPlaybackSpeed]   = useState<0.5 | 1 | 2>(1);
  // demo：逐步动画展示；realtime：立即完成，无等待
  const [displayMode, setDisplayMode]       = useState<"demo" | "realtime">("demo");

  // Refs：保证异步 runner 读到最新值（不产生 stale closure）
  const isPausedRef       = useRef(false);
  const playbackSpeedRef  = useRef<number>(1);
  const displayModeRef    = useRef<"demo" | "realtime">("demo");
  const cancelledRef      = useRef(false);   // 用于 unmount / 错误时中断动画

  // 始终从 Zustand store 读取最新角色，保证切换角色后立即响应
  // props 中的 currentRole/currentUserId 是消息渲染时的快照，会 stale，不能作为权限依据
  const { currentUserId: storeUserId, currentRole: storeRole } = useAuthStore();
  const currentRole   = storeRole;
  const currentUserId = storeUserId;

  const risk       = getRiskLevel(riskScore ?? 0);
  const canApprove = currentRole === "MANAGER";

  // ── 控制函数 ─────────────────────────────────────────────────────
  function togglePause() {
    const next = !isPausedRef.current;
    isPausedRef.current = next;
    setIsPaused(next);
  }

  function changeSpeed(s: 0.5 | 1 | 2) {
    playbackSpeedRef.current = s;
    setPlaybackSpeed(s);
  }

  function toggleDisplayMode() {
    const next = displayModeRef.current === "demo" ? "realtime" : "demo";
    displayModeRef.current = next;
    setDisplayMode(next);
  }

  // ── 虚拟时间 sleep（每 50ms poll，支持暂停+变速）────────────────
  // 实时模式：只等一个渲染帧（30ms），让步骤状态可见但不阻塞
  function adaptiveSleep(baseMs: number): Promise<void> {
    if (displayModeRef.current === "realtime") {
      return new Promise(resolve => setTimeout(resolve, 30));
    }
    return new Promise(resolve => {
      let virtual = 0;
      const tick = () => {
        if (cancelledRef.current) { resolve(); return; }
        if (!isPausedRef.current) virtual += 50 * playbackSpeedRef.current;
        if (virtual >= baseMs) { resolve(); return; }
        setTimeout(tick, 50);
      };
      setTimeout(tick, 50);
    });
  }

  // ── 步骤状态机 runner ────────────────────────────────────────────
  async function runAnimation(defs: typeof APPROVE_DEFS) {
    for (const def of defs) {
      if (cancelledRef.current) break;

      const startedAt = Date.now();
      setExecutionSteps(prev => prev.map(s =>
        s.id === def.id ? { ...s, status: "running", startedAt } : s
      ));

      await adaptiveSleep(STEP_DELAYS[def.id] ?? 800);

      if (cancelledRef.current) break;

      const completedAt = Date.now();
      setExecutionSteps(prev => prev.map(s =>
        s.id === def.id
          ? { ...s, status: "success", completedAt, duration: completedAt - (s.startedAt ?? startedAt) }
          : s
      ));
    }
  }

  // ── SSE 流读取 ───────────────────────────────────────────────────
  async function readSSE(resProm: Promise<Response>): Promise<StreamEvent[]> {
    const res = await resProm;
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error((err as { detail?: string }).detail ?? "审批请求失败");
    }

    const reader  = res.body!.getReader();
    const decoder = new TextDecoder();
    let buffer    = "";
    const events: StreamEvent[] = [];

    function parseBlock(block: string) {
      let evtType = "", evtData = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: "))    evtType = line.slice(7).trim();
        else if (line.startsWith("data: ")) evtData = line.slice(6).trim();
      }
      if (!evtData) return;
      try {
        const p = JSON.parse(evtData);
        if (evtType === "ui")    events.push({ type: p.type, props: p.props ?? {} });
        if (evtType === "error") throw new Error(p.error ?? "审批流程出错");
      } catch (e) {
        if (evtType === "error") throw e;
      }
    }

    while (true) {
      const { done, value } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: !done });
      if (done)  break;
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      blocks.forEach(parseBlock);
    }
    buffer.split("\n\n").filter(Boolean).forEach(parseBlock);

    return events;
  }

  // ── 主操作函数 ───────────────────────────────────────────────────
  async function handleAction(action: "approve" | "reject") {
    setAppState("loading");
    setError(null);
    setStreamEvents([]);
    cancelledRef.current     = false;
    isPausedRef.current      = false;
    playbackSpeedRef.current = playbackSpeed;
    displayModeRef.current   = displayMode;   // 快照当前模式，避免中途切换影响本次执行
    setIsPaused(false);

    const defs = action === "approve" ? APPROVE_DEFS : REJECT_DEFS;
    setExecutionSteps(makeSteps(defs));

    // 立即发起 fetch（与动画并行）
    const resProm = fetch("/api/chat", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        threadId, action,
        reviewerId:   currentUserId,
        reviewerRole: currentRole,
      }),
    });

    setAppState("streaming");

    try {
      // 动画 & SSE 并行运行，等两者都完成
      const [, sseEvents] = await Promise.all([
        runAnimation(defs),
        readSSE(resProm),
      ]);

      // 兜底：approve 时确保有退款时间线
      let finalEvents = sseEvents;
      if (action === "approve" && !finalEvents.some(e => e.type === "RefundTimeline")) {
        finalEvents = [...finalEvents, {
          type: "RefundTimeline",
          props: {
            steps: [
              { label: "提交退款申请", status: "completed", description: "" },
              { label: "审批通过",     status: "completed", description: `审批人：${currentUserId}` },
              { label: "退款完成",     status: "completed", description: "已退至原支付账户" },
            ],
          },
        }];
      }

      setStreamEvents(finalEvents);
      setAppState(action === "approve" ? "approved" : "rejected");
    } catch (err) {
      cancelledRef.current = true;
      setError(err instanceof Error ? err.message : "未知错误");
      setAppState("idle");
    }
  }

  // ── 进度计算 ─────────────────────────────────────────────────────
  const total     = executionSteps.length || 1;
  const completed = executionSteps.filter(s => s.status === "success").length;
  const running   = executionSteps.filter(s => s.status === "running").length;
  const progress  = Math.round((completed + running * 0.5) / total * 100);

  // 预计剩余时间（根据当前变速）
  const remainingMs = appState === "streaming" && !isPaused
    ? executionSteps
        .filter(s => s.status === "pending" || s.status === "running")
        .reduce((sum, s) => sum + (STEP_DELAYS[s.id] ?? 800) / playbackSpeedRef.current, 0)
    : null;

  // ── streaming 视图 ───────────────────────────────────────────────
  if (appState === "streaming") {
    return (
      <Card className="border-green-200 overflow-hidden w-full shadow-sm ring-1 ring-black/[0.04]">

        {/* Header：标题 + 控制栏 */}
        <CardHeader className="px-4 py-3 bg-green-50 border-b border-green-200 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {isPaused
                ? <Pause className="w-4 h-4 text-orange-500 flex-shrink-0" />
                : <Loader2 className="w-4 h-4 animate-spin text-green-600 flex-shrink-0" />
              }
              <span className="text-sm font-semibold text-green-800">
                {isPaused ? "已暂停" : "退款流程执行中..."}
              </span>
            </div>

            {/* 演示模式才显示速度/暂停控件 */}
            {displayMode === "demo" && (
              <div className="flex items-center gap-1.5">
                {/* 速度切换 */}
                <div className="flex items-center gap-0.5 bg-white border border-green-200 rounded-md px-1.5 py-0.5">
                  {([0.5, 1, 2] as const).map(s => (
                    <button
                      key={s}
                      onClick={() => changeSpeed(s)}
                      className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded transition-colors font-medium",
                        playbackSpeed === s
                          ? "bg-green-600 text-white"
                          : "text-muted-foreground hover:text-green-700"
                      )}
                    >
                      {s}x
                    </button>
                  ))}
                </div>

                {/* 暂停 / 继续 */}
                <Button
                  variant="ghost" size="sm"
                  className="h-7 w-7 p-0 text-green-700 hover:bg-green-100 flex-shrink-0"
                  onClick={togglePause}
                  title={isPaused ? "继续执行" : "暂停"}
                >
                  {isPaused
                    ? <Play className="w-3.5 h-3.5" />
                    : <Pause className="w-3.5 h-3.5" />
                  }
                </Button>
              </div>
            )}

            {/* 实时模式标识 */}
            {displayMode === "realtime" && (
              <span className="flex items-center gap-1 text-[10px] text-amber-600 font-medium">
                <Zap className="w-3 h-3" /> 实时处理
              </span>
            )}
          </div>

          {/* 进度条 */}
          <div className="space-y-1">
            <div className="w-full bg-green-200/50 h-1.5 rounded-full overflow-hidden">
              <div
                className="h-full bg-green-500 rounded-full transition-all duration-500 ease-out"
                style={{ width: `${progress}%` }}
              />
            </div>
            <div className="flex items-center justify-between text-[10px] text-green-600">
              <span>{progress}% 完成</span>
              {remainingMs !== null && remainingMs > 200 && (
                <span className="flex items-center gap-1">
                  <Timer className="w-3 h-3" />
                  预计剩余 {Math.ceil(remainingMs / 1000)}s
                </span>
              )}
            </div>
          </div>
        </CardHeader>

        {/* 步骤列表 */}
        <CardContent className="px-4 py-4 space-y-3">
          {executionSteps.map(step => {
            const Icon = step.icon;
            return (
              <div
                key={step.id}
                className={cn(
                  "flex items-start gap-3 transition-all duration-300",
                  step.status === "pending" ? "opacity-30" : "opacity-100 animate-in slide-in-from-left-2"
                )}
              >
                {/* 状态图标 */}
                <div className={cn(
                  "w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 transition-all duration-300",
                  step.status === "pending" ? "bg-muted" : "",
                  step.status === "running"  ? "bg-green-100 ring-2 ring-green-300 ring-offset-1" : "",
                  step.status === "success"  ? "bg-green-100" : "",
                  step.status === "failed"   ? "bg-red-100" : "",
                )}>
                  {step.status === "pending" && <Icon className="w-3.5 h-3.5 text-muted-foreground" />}
                  {step.status === "running"  && <Loader2 className="w-3.5 h-3.5 text-green-600 animate-spin" />}
                  {step.status === "success"  && <CheckCircle className="w-3.5 h-3.5 text-green-600" />}
                  {step.status === "failed"   && <XCircle className="w-3.5 h-3.5 text-red-600" />}
                </div>

                {/* 文字内容 */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className={cn(
                      "text-sm font-medium",
                      step.status === "pending" ? "text-muted-foreground" : "text-foreground"
                    )}>
                      {step.label}
                    </p>
                    {step.status === "running" && (
                      <Badge variant="outline" className="text-[10px] text-green-600 border-green-300 bg-green-50 py-0 px-1.5 h-4">
                        执行中
                      </Badge>
                    )}
                  </div>
                  {step.status !== "pending" && (
                    <p className="text-xs text-muted-foreground mt-0.5">{step.detail}</p>
                  )}
                </div>

                {/* 耗时 */}
                {step.status === "success" && step.duration > 0 && (
                  <span className="text-[10px] text-muted-foreground flex-shrink-0 mt-1 tabular-nums">
                    {formatMs(step.duration)}
                  </span>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>
    );
  }

  // ── approved 视图（全部步骤 + 时间线）──────────────────────────
  if (appState === "approved") {
    const totalDuration = executionSteps.reduce((s, step) => s + step.duration, 0);

    return (
      <Card className="border-green-200 overflow-hidden w-full shadow-sm ring-1 ring-black/[0.04]">
        <CardHeader className="px-4 py-3 bg-green-50 border-b border-green-200 space-y-0">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CheckCircle className="w-4 h-4 text-green-600 flex-shrink-0" />
              <span className="text-sm font-semibold text-green-800">退款已批准并处理完成</span>
            </div>
            {totalDuration > 0 && (
              <span className="text-[10px] text-green-600 flex items-center gap-1">
                <Timer className="w-3 h-3" />
                总耗时 {formatMs(totalDuration)}
              </span>
            )}
          </div>
        </CardHeader>

        <CardContent className="px-4 py-4 space-y-3">
          {/* 已完成的步骤 */}
          {executionSteps.map(step => {
            const Icon = step.icon;
            return (
              <div key={step.id} className="flex items-start gap-3">
                <div className="w-7 h-7 rounded-full bg-green-100 flex items-center justify-center flex-shrink-0">
                  <Icon className="w-3.5 h-3.5 text-green-600" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium">{step.label}</p>
                  <p className="text-xs text-muted-foreground">{step.detail}</p>
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0 mt-0.5">
                  {step.duration > 0 && (
                    <span className="text-[10px] text-muted-foreground tabular-nums">
                      {formatMs(step.duration)}
                    </span>
                  )}
                  <CheckCircle className="w-4 h-4 text-green-500" />
                </div>
              </div>
            );
          })}

          {/* 退款时间线 */}
          {streamEvents.filter(e => e.type === "RefundTimeline").map((evt, i) => (
            <RefundTimeline key={i} data={evt.props as { steps: never[] }} />
          ))}
        </CardContent>
      </Card>
    );
  }

  // ── rejected 视图 ────────────────────────────────────────────────
  if (appState === "rejected") {
    return (
      <Card className="border-red-200 overflow-hidden w-full shadow-sm ring-1 ring-black/[0.04]">
        <CardHeader className="px-4 py-3 bg-red-50 border-b border-red-200 flex flex-row items-center gap-2 space-y-0">
          <XCircle className="w-4 h-4 text-red-600 flex-shrink-0" />
          <span className="text-sm font-semibold text-red-800">退款申请已拒绝</span>
        </CardHeader>
        <CardContent className="px-4 py-4 space-y-3">
          {executionSteps.map(step => {
            const Icon = step.icon;
            return (
              <div key={step.id} className="flex items-start gap-3">
                <div className="w-7 h-7 rounded-full bg-red-100 flex items-center justify-center flex-shrink-0">
                  <Icon className="w-3.5 h-3.5 text-red-600" />
                </div>
                <div className="flex-1">
                  <p className="text-sm font-medium">{step.label}</p>
                  <p className="text-xs text-muted-foreground">{step.detail}</p>
                </div>
                {step.duration > 0 && (
                  <span className="text-[10px] text-muted-foreground flex-shrink-0 mt-1 tabular-nums">
                    {formatMs(step.duration)}
                  </span>
                )}
              </div>
            );
          })}
          <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 border border-red-100">
            工单 #{ticketId} 已标记为拒绝，如有疑问请联系客服
          </p>
        </CardContent>
      </Card>
    );
  }

  // ── idle / loading 视图 ──────────────────────────────────────────
  return (
    <Card className="border-orange-200 overflow-hidden w-full shadow-sm ring-1 ring-black/[0.04]">
      <CardHeader className="px-4 py-3 bg-orange-100 border-b border-orange-200 flex flex-row items-center justify-between space-y-0">
        <div className="flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-orange-600" />
          <span className="text-sm font-semibold text-orange-800">需要人工审批</span>
        </div>

        <div className="flex items-center gap-1.5">
          {/* 演示 / 实时 模式切换 */}
          <button
            onClick={toggleDisplayMode}
            title={displayMode === "demo" ? "当前：演示模式（逐步展示）\n点击切换到实时模式" : "当前：实时模式（即时完成）\n点击切换到演示模式"}
            className={cn(
              "flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full border transition-all",
              displayMode === "demo"
                ? "bg-violet-50 border-violet-300 text-violet-700 hover:bg-violet-100"
                : "bg-amber-50 border-amber-300 text-amber-700 hover:bg-amber-100"
            )}
          >
            {displayMode === "demo"
              ? <><Clapperboard className="w-3 h-3" /> 演示</>
              : <><Zap className="w-3 h-3" /> 实时</>
            }
          </button>

          <Badge variant="outline" className="text-[10px] text-orange-700 border-orange-300 bg-white/70 gap-1">
            <User className="w-3 h-3" />
            {currentRole === "MANAGER" ? "主管权限" : currentRole}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="px-4 py-4 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Card className="rounded-lg px-3 py-2.5 border-orange-100 shadow-none bg-white">
            <p className="text-xs text-muted-foreground mb-1">退款金额</p>
            <p className="text-lg font-bold">{formatCurrency(orderAmount)}</p>
          </Card>
          <Card className="rounded-lg px-3 py-2.5 border-orange-100 shadow-none bg-white">
            <p className="text-xs text-muted-foreground mb-1">风险评分</p>
            <p className={cn("text-lg font-bold", risk.color)}>
              {riskScore} <span className="text-sm font-normal">{risk.label}</span>
            </p>
          </Card>
        </div>

        <p className="text-xs text-orange-700 bg-orange-100/50 rounded-lg px-3 py-2 border border-orange-100">
          ⚠️ 退款金额超过风控阈值 ¥500，需要主管审批后方可执行
        </p>

        {error && (
          <p className="text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2 border border-red-100">
            {error}
          </p>
        )}

      </CardContent>

      <CardFooter className="px-4 pb-4 flex flex-col gap-3">
        {!canApprove && (
          <div className="flex items-center gap-2 bg-muted/60 rounded-lg px-3 py-2.5 border border-muted w-full">
            <XCircle className="w-4 h-4 text-muted-foreground flex-shrink-0" />
            <p className="text-xs text-muted-foreground">
              当前身份（{currentRole}）无审批权限，请切换为主管账户
            </p>
          </div>
        )}
        <div className="flex gap-3 w-full">
          <Button
            variant="outline"
            onClick={() => handleAction("reject")}
            disabled={!canApprove || appState === "loading"}
            className="flex-1 border-red-300 text-red-700 hover:bg-red-50 hover:text-red-800 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <XCircle className="w-4 h-4 mr-2" />
            拒绝
          </Button>
          <Button
            onClick={() => handleAction("approve")}
            disabled={!canApprove || appState === "loading"}
            className="flex-1 bg-green-600 hover:bg-green-700 text-white disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {appState === "loading"
              ? <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              : <CheckCircle className="w-4 h-4 mr-2" />
            }
            批准退款
          </Button>
        </div>
      </CardFooter>
    </Card>
  );
}
