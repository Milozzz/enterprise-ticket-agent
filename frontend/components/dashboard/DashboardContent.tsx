"use client";

import { useRouter } from "next/navigation";
import { useState, useEffect } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn, formatCurrency } from "@/lib/utils";
import type { DashboardStats, LlmCostReport, NodeLatencyStat } from "@/types";
import {
  Activity,
  AlertTriangle,
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Clock,
  DollarSign,
  GitBranch,
  RefreshCw,
  Search,
  ShieldAlert,
  Ticket,
  TrendingUp,
} from "lucide-react";
import Link from "next/link";
import { ChainReplay } from "@/components/dashboard/ChainReplay";

function StatCard({
  title,
  value,
  hint,
  icon: Icon,
  className,
}: {
  title: string;
  value: string;
  hint?: string;
  icon: React.ElementType;
  className?: string;
}) {
  return (
    <Card
      className={cn(
        "border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]",
        className
      )}
    >
      <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-slate-600">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold tabular-nums tracking-tight text-slate-900">
          {value}
        </div>
        {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
      </CardContent>
    </Card>
  );
}

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  processing: "处理中",
  awaiting_approval: "待审批",
  approved: "已批准",
  rejected: "已拒绝",
  completed: "已完成",
  escalated: "已升级",
};

interface FailedTrace {
  thread_id: string;
  trace_id: string | null;
  failed_node: string;
  error: string | null;
  created_at: string | null;
}

interface AgentSloReport {
  task_quality: {
    terminal_tasks: number;
    successful_tasks: number;
    blocked_tasks: number;
    in_progress_tasks: number;
    task_success_rate: number;
    plan_validity_rate: number;
    tool_selection_accuracy: number;
    evidence_coverage: number | null;
    replan_success_rate: number;
    human_override_rate: number;
    policy_violation_rate: number;
    compensation_success_rate: number;
    cost_per_successful_task_usd: number | null;
  };
  latency: {
    time_to_first_result_ms: MetricDistribution;
    end_to_end_completion_ms: MetricDistribution;
  };
}

interface MetricDistribution {
  samples: number;
  avg: number | null;
  p50: number | null;
  p95: number | null;
  max: number | null;
}

const NODE_LABELS: Record<string, string> = {
  classify_intent: "意图识别", lookup_order: "查询订单",
  check_risk: "风控评估", human_review: "人工审批",
  execute_refund: "执行退款", send_notification: "发送通知",
  answer_node: "生成回复",
};

export function DashboardContent({ stats }: { stats: DashboardStats | null }) {
  const router = useRouter();
  const [replayInput, setReplayInput] = useState("");
  const [replayThreadId, setReplayThreadId] = useState<string | null>(null);
  const [failedTraces, setFailedTraces] = useState<FailedTrace[]>([]);
  const [nodeLatency, setNodeLatency] = useState<NodeLatencyStat[]>([]);
  const [llmCosts, setLlmCosts] = useState<LlmCostReport | null>(null);
  const [agentSlo, setAgentSlo] = useState<AgentSloReport | null>(null);

  useEffect(() => {
    fetch("/api/dashboard/failed-traces")
      .then((r) => r.json())
      .then(setFailedTraces)
      .catch(() => {});

    fetch("/api/dashboard/node-latency")
      .then((r) => r.json())
      .then(setNodeLatency)
      .catch(() => {});

    fetch("/api/dashboard/llm-costs?days=7")
      .then((r) => r.json())
      .then(setLlmCosts)
      .catch(() => {});

    fetch("/api/dashboard/agent-slo?hours=168")
      .then((r) => r.json())
      .then(setAgentSlo)
      .catch(() => {});
  }, []);

  if (!stats) {
    return (
      <div className="mx-auto max-w-5xl flex-1 px-4 py-16 text-center">
        <AlertTriangle className="mx-auto h-10 w-10 text-amber-500" />
        <h1 className="mt-4 text-lg font-semibold text-slate-900">无法加载统计数据</h1>
        <p className="mt-2 text-sm text-slate-500">请确认后端已启动且 BACKEND_URL 配置正确。</p>
        <div className="mt-6 flex justify-center gap-3">
          <Button variant="outline" onClick={() => router.refresh()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            重试
          </Button>
          <Button asChild variant="default">
            <Link href="/">返回对话</Link>
          </Button>
        </div>
      </div>
    );
  }

  const pct = (stats.autoResolvedRate * 100).toFixed(1);
  const avg =
    stats.avgProcessingTimeMinutes > 0
      ? `${stats.avgProcessingTimeMinutes.toFixed(1)} 分钟`
      : "暂无样本";

  return (
    <div className="min-h-0 flex-1 bg-slate-50">
      <div className="border-b border-slate-200 bg-white/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="icon" asChild className="shrink-0">
              <Link href="/" aria-label="返回">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-slate-900">
                运营监控
              </h1>
              <p className="text-xs text-slate-500">工单与审计聚合（近 7 日趋势）</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {stats.source === "fallback" || stats.error ? (
              <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-900">
                降级数据
              </Badge>
            ) : (
              <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-800">
                实时聚合
              </Badge>
            )}
            <Button variant="outline" size="sm" onClick={() => router.refresh()}>
              <RefreshCw className="mr-2 h-3.5 w-3.5" />
              刷新
            </Button>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-6xl space-y-8 px-4 py-8 sm:px-6">
        {stats.error ? (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            后端提示：{stats.error}
          </div>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            title="工单总数"
            value={String(stats.totalTickets)}
            hint="历史累计"
            icon={Ticket}
          />
          <StatCard
            title="自动完结率"
            value={`${pct}%`}
            hint="已完成 / 总数"
            icon={TrendingUp}
          />
          <StatCard
            title="平均处理时长"
            value={avg}
            hint="含退款日志的样本"
            icon={Clock}
          />
          <StatCard
            title="风控会话（30 天）"
            value={String(stats.riskInterceptedCount)}
            hint="曾触发 check_risk 的去重 thread"
            icon={ShieldAlert}
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04] lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">近 7 日新建工单</CardTitle>
              <CardDescription>按创建时间聚合（本地时区显示为 UTC 存储日）</CardDescription>
            </CardHeader>
            <CardContent className="h-72 pl-0">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={stats.dailyTrend} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" className="stroke-slate-200" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} className="text-slate-500" />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11 }} className="text-slate-500" />
                  <Tooltip
                    contentStyle={{
                      borderRadius: 8,
                      border: "1px solid #e2e8f0",
                      fontSize: 12,
                    }}
                  />
                  <Legend />
                  <Bar dataKey="count" name="新建" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="autoResolved" name="估算完结" fill="#94a3b8" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]">
            <CardHeader>
              <CardTitle className="text-base">状态分布</CardTitle>
              <CardDescription>当前库内各阶段数量</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {Object.entries(stats.ticketsByStatus).map(([k, v]) => (
                <div
                  key={k}
                  className="flex items-center justify-between rounded-lg border border-slate-100 bg-slate-50/50 px-3 py-2 text-sm"
                >
                  <span className="text-slate-600">{STATUS_LABELS[k] ?? k}</span>
                  <span className="font-mono font-semibold tabular-nums text-slate-900">{v}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-base">累计退款金额（已完成工单）</CardTitle>
              <CheckCircle2 className="h-4 w-4 text-emerald-600" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold tabular-nums text-primary">
                {formatCurrency(stats.costSavedAmount)}
              </p>
              <p className="mt-1 text-xs text-slate-500">关联订单金额汇总</p>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-base">近 24h 审计事件</CardTitle>
              <Activity className="h-4 w-4 text-slate-500" />
            </CardHeader>
            <CardContent>
              <p className="text-2xl font-bold tabular-nums text-slate-900">
                {stats.auditEvents24h ?? "—"}
              </p>
              <p className="mt-1 text-xs text-slate-500">AuditLog 表写入条数</p>
            </CardContent>
          </Card>
          {(stats as any).approvalTimeoutCount > 0 && (
            <Card className="border-amber-200 shadow-sm ring-1 ring-amber-500/10 sm:col-span-2 lg:col-span-2">
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-base text-amber-800">超时待审批工单</CardTitle>
                <AlertCircle className="h-4 w-4 text-amber-500" />
              </CardHeader>
              <CardContent>
                <p className="text-2xl font-bold tabular-nums text-amber-700">
                  {(stats as any).approvalTimeoutCount}
                </p>
                <p className="mt-1 text-xs text-amber-600">PENDING 状态超过 24h，需人工介入</p>
              </CardContent>
            </Card>
          )}
        </div>

        {/* 节点耗时分布（近 7 天成功执行） */}
        {agentSlo ? <AgentQualityPanel report={agentSlo} /> : null}

        {nodeLatency.length > 0 && (
          <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]">
            <CardHeader>
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-slate-500" />
                <CardTitle className="text-base">节点耗时（近 7 天 · 成功执行）</CardTitle>
              </div>
              <CardDescription>
                各 Agent 节点平均 / P95 耗时（ms）— 识别性能瓶颈
              </CardDescription>
            </CardHeader>
            <CardContent className="h-72 pl-0">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={nodeLatency.map((n) => ({
                    name: NODE_LABELS[n.node] ?? n.node,
                    avg: n.avg_ms,
                    p95: n.p95_ms,
                    count: n.count,
                    failureRate: n.failure_rate ?? 0,
                    failures: n.failure_count ?? 0,
                    tokens: n.total_tokens ?? 0,
                  }))}
                  margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" className="stroke-slate-200" />
                  <XAxis dataKey="name" tick={{ fontSize: 10 }} className="text-slate-500" />
                  <YAxis
                    unit="ms"
                    allowDecimals={false}
                    tick={{ fontSize: 11 }}
                    className="text-slate-500"
                  />
                  <Tooltip
                    formatter={(value: number, name: string) => [
                      `${value} ms`,
                      name === "avg" ? "平均耗时" : "P95 耗时",
                    ]}
                    contentStyle={{
                      borderRadius: 8,
                      border: "1px solid #e2e8f0",
                      fontSize: 12,
                    }}
                  />
                  <Legend
                    formatter={(value) => (value === "avg" ? "平均耗时" : "P95 耗时")}
                  />
                  <Bar dataKey="avg" name="avg" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]}>
                    {nodeLatency.map((n, i) => (
                      <Cell
                        key={i}
                        fill={n.p95_ms > 3000 ? "#f87171" : n.p95_ms > 1000 ? "#fb923c" : "hsl(var(--primary))"}
                      />
                    ))}
                  </Bar>
                  <Bar dataKey="p95" name="p95" fill="#94a3b8" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
              <div className="mx-4 mt-3 grid gap-2 text-[11px] text-slate-500 sm:grid-cols-3">
                {nodeLatency.slice(0, 3).map((n) => (
                  <div key={n.node} className="rounded-md border border-slate-100 bg-slate-50 px-2 py-1">
                    <span className="font-medium text-slate-700">{NODE_LABELS[n.node] ?? n.node}</span>
                    <span className="ml-2">fail {(((n.failure_rate ?? 0) * 100).toFixed(1))}%</span>
                    <span className="ml-2">tokens {n.total_tokens ?? 0}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {llmCosts && (
          <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]">
            <CardHeader>
              <div className="flex items-center gap-2">
                <DollarSign className="h-4 w-4 text-slate-500" />
                <CardTitle className="text-base">模型用量与成本（近 7 天）</CardTitle>
                <Badge variant="outline" className="ml-auto text-[10px]">可配置估算</Badge>
              </div>
              <CardDescription>
                {llmCosts.totals.calls ?? 0} 次调用 · {(llmCosts.totals.total_tokens ?? 0).toLocaleString()} tokens ·
                估算 ${(llmCosts.totals.estimated_cost_usd ?? 0).toFixed(6)}
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(260px,1fr)]">
              <div className="h-64 min-w-0">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={llmCosts.daily} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-slate-200" />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip
                      formatter={(value: number, name: string) => [
                        name === "estimated_cost_usd" ? `$${Number(value).toFixed(6)}` : Number(value).toLocaleString(),
                        name === "estimated_cost_usd" ? "估算成本" : "Token",
                      ]}
                    />
                    <Bar dataKey="total_tokens" name="total_tokens" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="space-y-2">
                <p className="text-xs font-semibold text-slate-700">高成本会话</p>
                {llmCosts.sessions.length === 0 ? (
                  <p className="text-sm text-slate-500">暂无模型调用记录</p>
                ) : llmCosts.sessions.slice(0, 6).map((session) => (
                  <div key={session.thread_id} className="flex items-center justify-between gap-3 border-b border-slate-100 py-2 text-xs">
                    <span className="min-w-0 truncate font-mono text-slate-600" title={session.thread_id}>
                      {session.thread_id}
                    </span>
                    <span className="shrink-0 text-right tabular-nums text-slate-900">
                      ${session.estimated_cost_usd.toFixed(6)}
                      <span className="ml-2 text-slate-400">{session.total_tokens.toLocaleString()} tok</span>
                    </span>
                  </div>
                ))}
                <p className="pt-1 text-[11px] text-slate-500">
                  Failover {llmCosts.totals.fallback_calls ?? 0} 次 · 失败 {llmCosts.totals.failed_calls ?? 0} 次
                </p>
              </div>
            </CardContent>
          </Card>
        )}

        {/* 失败链路（近 7 天） */}
        {failedTraces.length > 0 && (
          <Card className="border-red-100 shadow-sm ring-1 ring-red-500/10">
            <CardHeader>
              <div className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4 text-red-500" />
                <CardTitle className="text-base">失败链路（近 7 天）</CardTitle>
                <Badge variant="outline" className="ml-auto border-red-200 bg-red-50 text-red-700 text-[10px]">
                  {failedTraces.length} 条
                </Badge>
              </div>
              <CardDescription>点击任意行一键加载到链路回放</CardDescription>
            </CardHeader>
            <CardContent className="space-y-1.5">
              {failedTraces.map((t) => (
                <button
                  key={t.thread_id}
                  className="w-full rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2 text-left text-sm hover:bg-red-50/60 hover:border-red-200 transition-colors"
                  onClick={() => {
                    setReplayInput(t.thread_id);
                    setReplayThreadId(t.thread_id);
                  }}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-[11px] text-slate-500">
                      {t.thread_id.slice(0, 28)}…
                    </span>
                    <Badge variant="outline" className="border-red-200 bg-red-50 text-red-700 text-[10px]">
                      {NODE_LABELS[t.failed_node] ?? t.failed_node}
                    </Badge>
                    {t.created_at && (
                      <span className="ml-auto text-[10px] text-slate-400">
                        {new Date(t.created_at).toLocaleString("zh-CN")}
                      </span>
                    )}
                  </div>
                  {t.error && (
                    <p className="mt-0.5 text-[11px] text-red-600 truncate">{t.error}</p>
                  )}
                </button>
              ))}
            </CardContent>
          </Card>
        )}

        {/* 链路回放 */}
        <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]">
          <CardHeader>
            <div className="flex items-center gap-2">
              <GitBranch className="h-4 w-4 text-slate-500" />
              <CardTitle className="text-base">链路回放</CardTitle>
            </div>
            <CardDescription>输入 Thread ID 查看完整节点执行链路</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const v = replayInput.trim();
                if (v) setReplayThreadId(v);
              }}
            >
              <input
                type="text"
                value={replayInput}
                onChange={(e) => setReplayInput(e.target.value)}
                placeholder="e.g. thread_abc123…"
                className="h-9 flex-1 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-primary/40"
              />
              <Button type="submit" size="sm" variant="outline">
                <Search className="mr-1.5 h-3.5 w-3.5" />
                查询
              </Button>
              {replayThreadId && (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => { setReplayThreadId(null); setReplayInput(""); }}
                >
                  清除
                </Button>
              )}
            </form>

            {replayThreadId && (
              <div className="rounded-lg border border-slate-100 bg-slate-50/60 p-4">
                <ChainReplay threadId={replayThreadId} />
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function AgentQualityPanel({ report }: { report: AgentSloReport }) {
  const quality = report.task_quality;
  const percent = (value: number | null) =>
    value == null ? "--" : `${(value * 100).toFixed(1)}%`;
  const duration = (value: number | null) =>
    value == null ? "--" : `${Math.round(value)} ms`;
  const metrics = [
    ["任务成功率", percent(quality.task_success_rate)],
    ["计划有效率", percent(quality.plan_validity_rate)],
    ["工具选择准确率", percent(quality.tool_selection_accuracy)],
    ["证据覆盖率", percent(quality.evidence_coverage)],
    ["重规划成功率", percent(quality.replan_success_rate)],
    ["补偿成功率", percent(quality.compensation_success_rate)],
  ];

  return (
    <Card className="border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-blue-600" />
          <CardTitle className="text-base">Agent 任务质量（近 7 天）</CardTitle>
          <Badge variant="outline" className="ml-auto">
            {quality.terminal_tasks} 个终态任务
          </Badge>
        </div>
        <CardDescription>
          计划、证据、工具授权、重规划、补偿、成本和端到端延迟的统一口径
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-px overflow-hidden rounded-md border border-slate-200 bg-slate-200 sm:grid-cols-2 lg:grid-cols-3">
          {metrics.map(([label, value]) => (
            <div key={label} className="bg-white px-4 py-3">
              <p className="text-xs text-slate-500">{label}</p>
              <p className="mt-1 text-lg font-semibold tabular-nums text-slate-950">{value}</p>
            </div>
          ))}
        </div>
        <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-xs text-slate-500">首结果 P50 / P95</p>
            <p className="mt-1 font-medium text-slate-900">
              {duration(report.latency.time_to_first_result_ms.p50)} / {duration(report.latency.time_to_first_result_ms.p95)}
            </p>
          </div>
          <div>
            <p className="text-xs text-slate-500">完成耗时 P50 / P95</p>
            <p className="mt-1 font-medium text-slate-900">
              {duration(report.latency.end_to_end_completion_ms.p50)} / {duration(report.latency.end_to_end_completion_ms.p95)}
            </p>
          </div>
          <div>
            <p className="text-xs text-slate-500">人工覆盖率</p>
            <p className="mt-1 font-medium text-slate-900">{percent(quality.human_override_rate)}</p>
          </div>
          <div>
            <p className="text-xs text-slate-500">成功任务平均模型成本</p>
            <p className="mt-1 font-medium text-slate-900">
              {quality.cost_per_successful_task_usd == null
                ? "--"
                : `$${quality.cost_per_successful_task_usd.toFixed(6)}`}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge variant="outline">阻断 {quality.blocked_tasks}</Badge>
          <Badge variant="outline">处理中 {quality.in_progress_tasks}</Badge>
          <Badge
            variant="outline"
            className={
              quality.policy_violation_rate === 0
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-red-200 bg-red-50 text-red-700"
            }
          >
            策略违规率 {percent(quality.policy_violation_rate)}
          </Badge>
        </div>
      </CardContent>
    </Card>
  );
}
