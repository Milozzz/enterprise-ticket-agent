"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  History,
  Inbox,
  Loader2,
  RefreshCw,
  Send,
  UserRound,
  XCircle,
} from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/store/authStore";

type ApprovalView = "approver" | "requester" | "history";
type ApprovalAction = "approve" | "reject";

interface ApprovalTask {
  taskId: number;
  taskKey: string;
  requestId: string;
  scenarioId: string;
  approvalType: string;
  stageId: string;
  stageName: string;
  requesterId: string;
  requesterRole: string;
  assignedRoles: string[];
  status: string;
  priority: string;
  businessPayload: Record<string, unknown>;
  threadId?: string;
  escalationLevel: number;
  escalationReason?: string;
  reviewerComment?: string;
  createdAt?: string;
  dueAt?: string;
  remainingSeconds?: number;
  completedAt?: string;
}

interface ApprovalHistory {
  decisionId: number;
  requestId: string;
  scenarioId: string;
  approvalType: string;
  stageId?: string;
  stageName?: string;
  action: string;
  status: string;
  reviewerId: string;
  reviewerRole: string;
  comment?: string;
  threadId?: string;
  createdAt?: string;
}

const VIEW_OPTIONS: Array<{ value: ApprovalView; label: string; icon: typeof Inbox }> = [
  { value: "approver", label: "待我审批", icon: Inbox },
  { value: "requester", label: "我的申请", icon: UserRound },
  { value: "history", label: "审批历史", icon: History },
];

function formatSla(dueAt?: string, now = Date.now()) {
  if (!dueAt) return { label: "未设置", overdue: false };
  const seconds = Math.floor((new Date(dueAt).getTime() - now) / 1000);
  const overdue = seconds < 0;
  const absolute = Math.abs(seconds);
  const hours = Math.floor(absolute / 3600);
  const minutes = Math.floor((absolute % 3600) / 60);
  return {
    label: `${overdue ? "已超时" : "剩余"} ${hours}h ${minutes}m`,
    overdue,
  };
}

function statusClass(status: string) {
  if (status === "approved") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "rejected") return "border-red-200 bg-red-50 text-red-700";
  if (status === "escalated") return "border-amber-200 bg-amber-50 text-amber-800";
  return "border-blue-200 bg-blue-50 text-blue-700";
}

export default function ApprovalCenterPage() {
  const { currentRole, currentUserId } = useAuthStore();
  const [view, setView] = useState<ApprovalView>("approver");
  const [tasks, setTasks] = useState<ApprovalTask[]>([]);
  const [history, setHistory] = useState<ApprovalHistory[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [comment, setComment] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({
        limit: "100",
        view,
        user_id: currentUserId,
        user_role: currentRole,
      });
      const res = await fetch(`/api/agent/approval-center?${query}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "加载审批中心失败");
      setTasks(data.tasks ?? []);
      setHistory(data.history ?? []);
      setSelected(new Set());
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载审批中心失败");
    } finally {
      setLoading(false);
    }
  }, [currentRole, currentUserId, view]);

  useEffect(() => {
    loadApprovals();
  }, [loadApprovals]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const selectedTasks = useMemo(
    () => tasks.filter((task) => selected.has(task.taskId)),
    [selected, tasks]
  );

  function toggleTask(taskId: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }

  async function submitBatch(action: ApprovalAction) {
    if (!selectedTasks.length) return;
    const missingThread = selectedTasks.find((task) => !task.threadId);
    if (missingThread) {
      setError(`${missingThread.requestId} 缺少 threadId，无法恢复工作流`);
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      for (const task of selectedTasks) {
        const res = await fetch("/api/chat", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            threadId: task.threadId,
            action,
            reviewerId: currentUserId,
            reviewerRole: currentRole,
            comment,
          }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.detail || `${task.requestId} 审批失败`);
        }
        await res.text();
      }
      setComment("");
      await loadApprovals();
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量审批失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function sweepSla() {
    setSubmitting(true);
    try {
      await fetch("/api/agent/approval-center", { method: "POST" });
      await loadApprovals();
    } finally {
      setSubmitting(false);
    }
  }

  const visibleItems = view === "history" ? history : tasks;

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="icon">
              <Link href="/admin/scenarios" aria-label="返回场景配置">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-bold text-slate-950">Approval Center</h1>
              <p className="text-sm text-slate-500">多场景审批队列、SLA 与决策历史</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={sweepSla} disabled={submitting}>
              <Clock3 className="mr-2 h-4 w-4" />
              扫描超时
            </Button>
            <Button variant="outline" size="sm" onClick={loadApprovals} disabled={loading}>
              {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
              刷新
            </Button>
          </div>
        </div>

        <div className="mb-5 inline-flex max-w-full gap-1 overflow-x-auto rounded-md border border-slate-200 bg-white p-1">
          {VIEW_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => setView(option.value)}
              className={cn(
                "inline-flex h-8 shrink-0 items-center gap-2 rounded px-3 text-sm font-medium transition-colors",
                view === option.value ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"
              )}
            >
              <option.icon className="h-3.5 w-3.5" />
              {option.label}
            </button>
          ))}
        </div>

        {error ? (
          <div className="mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        ) : null}

        {view === "approver" && (
          <div className="mb-4 grid gap-3 border-y border-slate-200 bg-white px-4 py-4 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-end">
            <label className="grid gap-1.5 text-xs font-medium text-slate-600">
              审批意见
              <textarea
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                maxLength={500}
                rows={2}
                placeholder="填写本次审批依据或补充说明"
                className="min-h-16 resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-slate-300"
              />
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <span className="mr-1 text-xs text-slate-500">已选 {selected.size} 项</span>
              <Button size="sm" onClick={() => submitBatch("approve")} disabled={!selected.size || submitting}>
                <CheckCircle2 className="mr-2 h-4 w-4" />
                批量批准
              </Button>
              <Button size="sm" variant="destructive" onClick={() => submitBatch("reject")} disabled={!selected.size || submitting}>
                <XCircle className="mr-2 h-4 w-4" />
                批量拒绝
              </Button>
            </div>
          </div>
        )}

        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="border-b border-slate-100 py-4">
            <CardTitle className="flex items-center gap-2 text-base">
              <Send className="h-4 w-4 text-blue-600" />
              {VIEW_OPTIONS.find((option) => option.value === view)?.label}
              <Badge variant="outline" className="ml-auto">{visibleItems.length}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {loading ? (
              <div className="flex items-center justify-center py-16 text-sm text-slate-500">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                加载中
              </div>
            ) : visibleItems.length ? (
              <div className="overflow-x-auto">
                {view === "history" ? (
                  <table className="w-full min-w-[920px] text-left text-sm">
                    <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
                      <tr><th className="px-4 py-3">Request</th><th className="px-4 py-3">Scenario / Stage</th><th className="px-4 py-3">Decision</th><th className="px-4 py-3">Reviewer</th><th className="px-4 py-3">Comment</th><th className="px-4 py-3">Created</th></tr>
                    </thead>
                    <tbody>
                      {history.map((item) => (
                        <tr key={item.decisionId} className="border-b border-slate-100">
                          <td className="px-4 py-3 font-mono text-xs">{item.requestId}</td>
                          <td className="px-4 py-3"><div className="font-medium">{item.scenarioId}</div><div className="text-xs text-slate-500">{item.stageName || item.stageId || "scenario-level"}</div></td>
                          <td className="px-4 py-3"><Badge variant="outline" className={statusClass(item.status)}>{item.action}</Badge></td>
                          <td className="px-4 py-3"><div>{item.reviewerRole}</div><div className="text-xs text-slate-500">{item.reviewerId}</div></td>
                          <td className="max-w-[240px] truncate px-4 py-3 text-xs text-slate-600" title={item.comment}>{item.comment || "-"}</td>
                          <td className="px-4 py-3 text-xs text-slate-500">{item.createdAt ? new Date(item.createdAt).toLocaleString("zh-CN") : "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <table className="w-full min-w-[1100px] text-left text-sm">
                    <thead className="border-b border-slate-200 bg-slate-50 text-xs text-slate-500">
                      <tr>
                        {view === "approver" && <th className="w-12 px-4 py-3"><span className="sr-only">选择</span></th>}
                        <th className="px-4 py-3">Request</th><th className="px-4 py-3">Scenario / Stage</th><th className="px-4 py-3">Requester</th><th className="px-4 py-3">Priority</th><th className="px-4 py-3">SLA</th><th className="px-4 py-3">Status</th><th className="px-4 py-3">Assigned roles</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tasks.map((task) => {
                        const sla = formatSla(task.dueAt, now);
                        return (
                          <tr key={task.taskId} className={cn("border-b border-slate-100", selected.has(task.taskId) && "bg-blue-50/50")}>
                            {view === "approver" && <td className="px-4 py-3"><input type="checkbox" checked={selected.has(task.taskId)} onChange={() => toggleTask(task.taskId)} aria-label={`选择 ${task.requestId}`} className="h-4 w-4 accent-slate-900" /></td>}
                            <td className="px-4 py-3"><div className="font-mono text-xs">{task.requestId}</div><div className="mt-1 max-w-[260px] truncate text-xs text-slate-500" title={JSON.stringify(task.businessPayload)}>{Object.keys(task.businessPayload).length} 个业务字段</div></td>
                            <td className="px-4 py-3"><div className="font-medium text-slate-900">{task.scenarioId}</div><div className="text-xs text-slate-500">{task.stageName}</div></td>
                            <td className="px-4 py-3"><div>{task.requesterId}</div><div className="text-xs text-slate-500">{task.requesterRole}</div></td>
                            <td className="px-4 py-3"><Badge variant="outline" className={task.priority === "high" ? "border-red-200 bg-red-50 text-red-700" : "border-slate-200"}>{task.priority}</Badge></td>
                            <td className={cn("px-4 py-3 text-xs font-medium", sla.overdue ? "text-red-700" : "text-slate-600")}><Clock3 className="mr-1.5 inline h-3.5 w-3.5" />{sla.label}</td>
                            <td className="px-4 py-3"><Badge variant="outline" className={statusClass(task.status)}>{task.status}</Badge></td>
                            <td className="px-4 py-3 text-xs text-slate-600">{task.assignedRoles.join(" / ")}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            ) : (
              <div className="px-4 py-14 text-center text-sm text-slate-500">
                当前视图没有审批任务。
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
