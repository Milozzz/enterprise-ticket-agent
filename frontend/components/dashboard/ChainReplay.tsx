"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CheckCircle2, XCircle, Clock, Loader2, Link2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface ReplayNode {
  node: string;
  refund_state: string | null;
  success: boolean | null;
  duration_ms: number | null;
  error: string | null;
  time: string;
  summary: Record<string, unknown>;
}

interface ReplayData {
  thread_id: string;
  trace_id: string | null;
  nodes: ReplayNode[];
  summary: {
    total_duration_ms: number;
    node_count: number;
    failed_nodes: string[];
    success: boolean;
  };
}

const NODE_LABELS: Record<string, string> = {
  classify_intent:    "意图识别",
  lookup_order:       "查询订单",
  check_risk:         "风控评估",
  human_review:       "人工审批",
  execute_refund:     "执行退款",
  send_notification:  "发送通知",
  answer_node:        "生成回复",
  answer_policy_node: "政策查询",
};

const STATE_COLORS: Record<string, string> = {
  CREATED:          "bg-slate-100 text-slate-600",
  CLASSIFIED:       "bg-blue-100 text-blue-700",
  ORDER_LOADED:     "bg-indigo-100 text-indigo-700",
  RISK_EVALUATED:   "bg-yellow-100 text-yellow-700",
  PENDING_APPROVAL: "bg-orange-100 text-orange-700",
  APPROVED:         "bg-emerald-100 text-emerald-700",
  REFUNDED:         "bg-teal-100 text-teal-700",
  COMPLETED:        "bg-green-100 text-green-700",
  REJECTED:         "bg-red-100 text-red-700",
  FAILED:           "bg-red-200 text-red-800",
};

function SummaryBadge({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([k, v]) => k !== "_refund_state" && v !== null && v !== undefined && v !== ""
  );
  if (!entries.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1">
      {entries.map(([k, v]) => (
        <span
          key={k}
          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600"
        >
          {k}: {String(v)}
        </span>
      ))}
    </div>
  );
}

export function ChainReplay({ threadId }: { threadId: string }) {
  const [data, setData] = useState<ReplayData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!threadId) return;
    setLoading(true);
    fetch(`/api/agent/replay/${threadId}`)
      .then((r) => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [threadId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-400">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        加载链路中…
      </div>
    );
  }

  if (!data || !data.nodes.length) {
    return (
      <div className="py-16 text-center text-sm text-slate-400">
        该 Thread 暂无节点执行记录
      </div>
    );
  }

  const { nodes, summary, trace_id } = data;
  const totalMs = summary.total_duration_ms;

  return (
    <div className="space-y-4">
      {/* 头部元信息 */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
        <span className="font-mono">thread: {threadId.slice(0, 24)}…</span>
        {trace_id && (
          <span className="flex items-center gap-1 font-mono">
            <Link2 className="h-3 w-3" />
            trace: {trace_id}
          </span>
        )}
        <Badge
          variant="outline"
          className={cn(
            "text-[10px]",
            summary.success
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-red-200 bg-red-50 text-red-700"
          )}
        >
          {summary.success ? "成功" : `失败：${summary.failed_nodes.join(", ")}`}
        </Badge>
        <span>{summary.node_count} 个节点 · {totalMs} ms 总耗时</span>
      </div>

      {/* 节点时间线 */}
      <div className="relative space-y-0">
        {nodes.map((node, i) => {
          const isLast = i === nodes.length - 1;
          const pct = totalMs > 0 ? Math.round(((node.duration_ms ?? 0) / totalMs) * 100) : 0;
          return (
            <div key={i} className="flex gap-3">
              {/* 竖线 + 节点圆点 */}
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2 bg-white",
                    node.success === false
                      ? "border-red-400"
                      : "border-emerald-400"
                  )}
                >
                  {node.success === false ? (
                    <XCircle className="h-3.5 w-3.5 text-red-500" />
                  ) : (
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
                  )}
                </div>
                {!isLast && (
                  <div className="w-px flex-1 bg-slate-200" style={{ minHeight: 16 }} />
                )}
              </div>

              {/* 节点内容 */}
              <div className={cn("pb-4 min-w-0 flex-1", isLast && "pb-0")}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold text-sm text-slate-800">
                    {NODE_LABELS[node.node] ?? node.node}
                  </span>
                  {node.refund_state && (
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-[10px] font-medium",
                        STATE_COLORS[node.refund_state] ?? "bg-slate-100 text-slate-600"
                      )}
                    >
                      {node.refund_state}
                    </span>
                  )}
                  {node.duration_ms != null && (
                    <span className="flex items-center gap-0.5 text-[10px] text-slate-400">
                      <Clock className="h-2.5 w-2.5" />
                      {node.duration_ms} ms ({pct}%)
                    </span>
                  )}
                  <span className="text-[10px] text-slate-400">
                    {new Date(node.time).toLocaleTimeString("zh-CN")}
                  </span>
                </div>
                {node.error && (
                  <p className="mt-1 text-xs text-red-600">{node.error}</p>
                )}
                <SummaryBadge data={node.summary} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
