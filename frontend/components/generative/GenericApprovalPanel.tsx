"use client";

import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDot,
  ClipboardCheck,
  Loader2,
  LockKeyhole,
  ShieldCheck,
  UserCheck,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/store/authStore";

type ApprovalAction = "approve" | "reject";

interface GenericApprovalAction {
  label: string;
  action: ApprovalAction;
  variant?: "approve" | "reject";
}

interface ApprovalStage {
  id: string;
  name: string;
  roles: string[];
  required?: boolean;
}

interface DecisionRecord {
  ok: boolean;
  idempotent?: boolean;
  decisionId?: number;
  requestId: string;
  stageId?: string;
  stageName?: string;
  action: ApprovalAction;
  status: string;
  reviewerId: string;
  reviewerRole: string;
  message?: string;
}

interface GenericApprovalPanelProps {
  scenarioId: string;
  approvalType: string;
  requestId: string;
  threadId?: string;
  title: string;
  requesterId?: string;
  reviewRoles: string[];
  approvalChain?: ApprovalStage[];
  currentStatus: string;
  policyReason: string;
  matchedRules: string[];
  actions?: GenericApprovalAction[];
}

function statusLabel(status: string) {
  const normalized = status?.toLowerCase();
  if (normalized === "pending_review") return "等待审批";
  if (normalized === "approved") return "已批准";
  if (normalized === "rejected") return "已拒绝";
  return status || "待处理";
}

function canReview(role: string | undefined, reviewRoles: string[]) {
  const normalizedRole = role?.toUpperCase();
  if (!normalizedRole) return false;
  return reviewRoles.map((item) => item.toUpperCase()).includes(normalizedRole);
}

function normalizeRoles(roles: string[]) {
  return roles.map((role) => role.toUpperCase());
}

function errorMessage(data: unknown, fallback: string) {
  if (data && typeof data === "object") {
    const detail = (data as { detail?: unknown }).detail;
    const error = (data as { error?: unknown }).error;
    if (typeof detail === "string") return detail;
    if (typeof error === "string") return error;
  }
  return fallback;
}

export default function GenericApprovalPanel({
  scenarioId,
  approvalType,
  requestId,
  threadId,
  title,
  requesterId,
  reviewRoles,
  approvalChain = [],
  currentStatus,
  policyReason,
  matchedRules,
  actions = [
    { label: "批准", action: "approve", variant: "approve" },
    { label: "拒绝", action: "reject", variant: "reject" },
  ],
}: GenericApprovalPanelProps) {
  const { currentRole, currentUserId } = useAuthStore();
  const [decision, setDecision] = useState<ApprovalAction | null>(null);
  const [decisionRecord, setDecisionRecord] = useState<DecisionRecord | null>(null);
  const [completedStageIds, setCompletedStageIds] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState("");

  const normalizedApprovalChain = useMemo(
    () =>
      approvalChain.map((stage) => ({
        ...stage,
        roles: normalizeRoles(stage.roles ?? []),
      })),
    [approvalChain]
  );
  const activeStage =
    normalizedApprovalChain.find(
      (stage) => stage.required !== false && !completedStageIds.includes(stage.id)
    ) ?? null;
  const effectiveReviewRoles = activeStage?.roles.length ? activeStage.roles : normalizeRoles(reviewRoles);
  const reviewerAllowed = canReview(currentRole, effectiveReviewRoles);
  const visibleStatus = decisionRecord?.status ?? (
    decision === "approve" ? "approved" : decision === "reject" ? "rejected" : currentStatus
  );

  const orderedSteps = useMemo(
    () => [
      { label: "策略命中", detail: policyReason, done: true },
      ...(normalizedApprovalChain.length
        ? [
            {
              label: "多级审批阶段",
              detail: activeStage
                ? `${activeStage.name} (${activeStage.roles.join(" / ")})`
                : normalizedApprovalChain.map((stage) => stage.name).join(" -> "),
              done: Boolean(activeStage),
            },
          ]
        : []),
      {
        label: "审批角色校验",
        detail: `允许角色：${effectiveReviewRoles.join(" / ")}`,
        done: reviewerAllowed,
      },
      {
        label: "审批决策落库",
        detail: decisionRecord
          ? `${decisionRecord.reviewerId} 已${decisionRecord.action === "approve" ? "批准" : "拒绝"}该申请`
          : "等待授权审批角色处理",
        done: Boolean(decisionRecord),
      },
    ],
    [activeStage, decisionRecord, effectiveReviewRoles, normalizedApprovalChain, policyReason, reviewerAllowed]
  );

  async function submitDecision(action: ApprovalAction) {
    setIsSubmitting(true);
    setError(null);
    try {
      if (!threadId) {
        throw new Error("审批工作流缺少 threadId，无法从检查点恢复");
      }
      const res = await fetch("/api/chat", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId,
          action,
          reviewerId: currentUserId,
          reviewerRole: currentRole,
          comment,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(errorMessage(data, "审批请求失败"));
      }
      await res.text();
      setComment("");

      const stageId = activeStage?.id;
      const remainingStages = normalizedApprovalChain.filter(
        (stage) => stage.required !== false && stage.id !== stageId && !completedStageIds.includes(stage.id)
      );
      if (action === "approve" && stageId) {
        setCompletedStageIds((current) => [...current, stageId]);
      }
      if (action === "approve" && remainingStages.length > 0) {
        setDecision(null);
        setDecisionRecord(null);
      } else {
        setDecision(action);
        setDecisionRecord({
          ok: true,
          requestId,
          stageId,
          stageName: activeStage?.name,
          action,
          status: action === "approve" ? "approved" : "rejected",
          reviewerId: currentUserId,
          reviewerRole: currentRole,
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "审批请求失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <Card
      className={cn(
        "w-full overflow-hidden border border-indigo-200/80 bg-indigo-50/20 shadow-sm ring-1 ring-indigo-100/70",
        "animate-in fade-in slide-in-from-bottom-2 duration-300"
      )}
    >
      <CardHeader className="space-y-0 border-b border-indigo-100 bg-indigo-50/70 px-4 py-3 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-indigo-200 bg-white shadow-sm">
              <UserCheck className="h-4 w-4 text-indigo-600" />
            </div>
            <div className="min-w-0 space-y-1">
              <CardTitle className="text-base font-semibold text-slate-900">
                {title}
              </CardTitle>
              <CardDescription className="text-xs leading-relaxed text-indigo-900/70">
                {scenarioId} · {approvalType} · <span className="font-mono">{requestId}</span>
              </CardDescription>
            </div>
          </div>
          <Badge
            variant="outline"
            className={cn(
              "shrink-0 border font-semibold shadow-none",
              visibleStatus === "approved" && "border-emerald-200 bg-emerald-600 text-white",
              visibleStatus === "rejected" && "border-red-200 bg-red-600 text-white",
              visibleStatus !== "approved" && visibleStatus !== "rejected" && "border-indigo-200 bg-white text-indigo-800"
            )}
          >
            {statusLabel(visibleStatus)}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 px-4 py-4 sm:px-5">
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2">
            <p className="text-[10px] font-semibold uppercase text-slate-400">申请人</p>
            <p className="mt-1 font-mono text-sm font-medium text-slate-900">{requesterId || "-"}</p>
          </div>
          <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2">
            <p className="text-[10px] font-semibold uppercase text-slate-400">当前角色</p>
            <p className="mt-1 text-sm font-medium text-slate-900">{currentRole}</p>
          </div>
        </div>

        <div className="rounded-lg border border-slate-200/80 bg-white p-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-indigo-600" />
            <span className="text-sm font-semibold text-slate-900">审批策略</span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-600">{policyReason}</p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {reviewRoles.map((role) => (
              <Badge key={role} variant="outline" className="h-5 border-indigo-200 bg-indigo-50 text-[10px] text-indigo-800">
                {role}
              </Badge>
            ))}
            {matchedRules.map((rule) => (
              <Badge key={rule} variant="secondary" className="h-5 font-mono text-[10px]">
                {rule}
              </Badge>
            ))}
          </div>
        </div>

        {normalizedApprovalChain.length ? (
          <div className="rounded-lg border border-slate-200/80 bg-white p-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-indigo-600" />
              <span className="text-sm font-semibold text-slate-900">多级审批链路</span>
            </div>
            <div className="mt-3 grid gap-2">
              {normalizedApprovalChain.map((stage) => {
                const isActive = activeStage?.id === stage.id;
                return (
                  <div
                    key={stage.id}
                    className={cn(
                      "rounded-lg border px-3 py-2 text-xs",
                      isActive ? "border-indigo-200 bg-indigo-50" : "border-slate-200 bg-slate-50"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-semibold text-slate-900">{stage.name}</span>
                      <Badge variant="outline" className="h-5 bg-white text-[10px]">
                        {stage.required === false ? "optional" : "required"}
                      </Badge>
                    </div>
                    <div className="mt-1 font-mono text-[11px] text-slate-500">
                      {stage.id} - {stage.roles.join(" / ") || "no role bound"}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}

        <Separator className="bg-indigo-100" />

        <ul className="space-y-2">
          {orderedSteps.map((step) => (
            <li key={step.label} className="flex gap-3 rounded-lg border border-slate-200/70 bg-white px-3 py-2">
              <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-slate-50">
                {step.done ? (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                ) : (
                  <CircleDot className="h-3.5 w-3.5 text-indigo-500" />
                )}
              </div>
              <div className="min-w-0">
                <p className="text-xs font-semibold text-slate-900">{step.label}</p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">{step.detail}</p>
              </div>
            </li>
          ))}
        </ul>

        {!reviewerAllowed ? (
          <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900">
            <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" />
            当前角色仅有查看权限，审批动作需要授权审批角色处理。
          </div>
        ) : null}

        {reviewerAllowed && !decisionRecord ? (
          <label className="grid gap-1.5 text-xs font-medium text-slate-600">
            审批意见
            <textarea
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              maxLength={500}
              rows={2}
              placeholder="填写审批依据或补充说明"
              className="min-h-16 resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none focus:ring-2 focus:ring-indigo-200"
            />
          </label>
        ) : null}

        {error ? (
          <div className="flex gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            {error}
          </div>
        ) : null}

        {decisionRecord ? (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-relaxed text-emerald-800">
            {decisionRecord.message ?? "审批决策已记录。"}
            {decisionRecord.idempotent ? " 已按幂等结果返回。" : null}
          </div>
        ) : null}
      </CardContent>

      <CardFooter className="flex flex-col gap-2 border-t border-indigo-100 bg-white/70 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5">
        <div className="flex items-center gap-2 text-[11px] font-medium text-slate-500">
          <ClipboardCheck className="h-3.5 w-3.5" />
          审批控制台 · 后端鉴权与审计
        </div>
        <div className="flex w-full gap-2 sm:w-auto">
          {actions.map((item) => {
            const isReject = item.action === "reject" || item.variant === "reject";
            return (
              <Button
                key={item.action}
                size="sm"
                variant={isReject ? "destructive" : "default"}
                className={cn("flex-1 sm:flex-none", isReject ? "" : "bg-emerald-600 hover:bg-emerald-700")}
                disabled={!reviewerAllowed || Boolean(decisionRecord) || isSubmitting}
                onClick={() => submitDecision(item.action)}
              >
                {isSubmitting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : isReject ? (
                  <XCircle className="h-3.5 w-3.5" />
                ) : (
                  <CheckCircle2 className="h-3.5 w-3.5" />
                )}
                {item.label}
              </Button>
            );
          })}
        </div>
      </CardFooter>
    </Card>
  );
}
