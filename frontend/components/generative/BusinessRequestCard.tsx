"use client";

import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Clock3,
  FileText,
  KeyRound,
  ReceiptText,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

type FieldTone = "default" | "success" | "warning" | "danger";

interface BusinessField {
  label: string;
  value: string | number | null;
  tone?: FieldTone;
}

interface PolicySummary {
  effect: string;
  requiresHumanReview: boolean;
  matchedRules: string[];
  reason: string;
  version?: string;
}

interface TimelineItem {
  label: string;
  status: "completed" | "current" | "pending";
  description?: string;
}

interface BusinessRequestCardProps {
  scenarioId: string;
  scenarioName: string;
  requestId: string;
  status: string;
  title: string;
  summary: string;
  fields: BusinessField[];
  policy?: PolicySummary;
  timeline?: TimelineItem[];
}

const toneClass: Record<FieldTone, string> = {
  default: "border-slate-200 bg-white text-slate-800",
  success: "border-emerald-200 bg-emerald-50 text-emerald-800",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  danger: "border-red-200 bg-red-50 text-red-800",
};

function scenarioIcon(scenarioId: string) {
  if (scenarioId === "permission_request") return KeyRound;
  if (scenarioId === "reimbursement") return ReceiptText;
  return FileText;
}

function statusLabel(status: string) {
  const normalized = status?.toLowerCase();
  if (normalized === "pending_review") return "等待审批";
  if (normalized === "auto_approved") return "自动通过";
  if (normalized === "approved") return "已批准";
  if (normalized === "rejected") return "已拒绝";
  return status || "处理中";
}

function statusClass(status: string) {
  const normalized = status?.toLowerCase();
  if (normalized === "auto_approved" || normalized === "approved") {
    return "border-emerald-200 bg-emerald-600 text-white";
  }
  if (normalized === "rejected") {
    return "border-red-200 bg-red-600 text-white";
  }
  return "border-amber-200 bg-amber-50 text-amber-900";
}

function timelineIcon(status: TimelineItem["status"]) {
  if (status === "completed") return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />;
  if (status === "current") return <Clock3 className="h-3.5 w-3.5 text-primary" />;
  return <Clock3 className="h-3.5 w-3.5 text-slate-300" />;
}

export default function BusinessRequestCard({
  scenarioId,
  scenarioName,
  requestId,
  status,
  title,
  summary,
  fields,
  policy,
  timeline = [],
}: BusinessRequestCardProps) {
  const Icon = scenarioIcon(scenarioId);
  const requiresReview = Boolean(policy?.requiresHumanReview);

  return (
    <Card
      className={cn(
        "w-full overflow-hidden border border-slate-200/80 bg-card shadow-sm ring-1 ring-black/[0.04]",
        "animate-in fade-in slide-in-from-bottom-2 duration-300"
      )}
    >
      <CardHeader className="space-y-0 border-b border-slate-100 bg-slate-50/70 px-4 py-3 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white shadow-sm">
              <Icon className="h-4 w-4 text-primary" />
            </div>
            <div className="min-w-0 space-y-1">
              <CardTitle className="text-base font-semibold text-slate-900">
                {title}
              </CardTitle>
              <CardDescription className="text-xs leading-relaxed text-slate-500">
                {scenarioName} · <span className="font-mono">{requestId}</span>
              </CardDescription>
            </div>
          </div>
          <Badge variant="outline" className={cn("shrink-0 border font-semibold shadow-none", statusClass(status))}>
            {statusLabel(status)}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 px-4 py-4 sm:px-5">
        <p className="rounded-lg border border-slate-200/80 bg-white px-3 py-2 text-xs leading-relaxed text-slate-700">
          {summary}
        </p>

        <div className="grid gap-2 sm:grid-cols-2">
          {fields.map((field) => (
            <div
              key={field.label}
              className={cn(
                "min-w-0 rounded-lg border px-3 py-2",
                toneClass[field.tone ?? "default"]
              )}
            >
              <p className="text-[10px] font-semibold uppercase opacity-60">
                {field.label}
              </p>
              <p className="mt-1 truncate text-sm font-medium tabular-nums">
                {field.value ?? "-"}
              </p>
            </div>
          ))}
        </div>

        {policy ? (
          <>
            <Separator className="bg-slate-100" />
            <div className="rounded-lg border border-slate-200/80 bg-slate-50/70 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  {requiresReview ? (
                    <AlertTriangle className="h-4 w-4 text-amber-600" />
                  ) : (
                    <ShieldCheck className="h-4 w-4 text-emerald-600" />
                  )}
                  <span className="text-sm font-semibold text-slate-900">Policy-as-Code 判定</span>
                </div>
                <Badge
                  variant="outline"
                  className={cn(
                    "border text-[10px]",
                    requiresReview
                      ? "border-amber-200 bg-amber-50 text-amber-900"
                      : "border-emerald-200 bg-emerald-50 text-emerald-800"
                  )}
                >
                  {requiresReview ? "需要 HITL" : "可自动处理"}
                </Badge>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-slate-600">{policy.reason}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {policy.version ? (
                  <Badge variant="secondary" className="h-5 font-mono text-[10px]">
                    policy {policy.version}
                  </Badge>
                ) : null}
                {(policy.matchedRules ?? []).length > 0 ? (
                  policy.matchedRules.map((rule) => (
                    <Badge key={rule} variant="outline" className="h-5 border-slate-200 bg-white font-mono text-[10px] text-slate-600">
                      {rule}
                    </Badge>
                  ))
                ) : (
                  <Badge variant="outline" className="h-5 border-slate-200 bg-white text-[10px] text-slate-500">
                    no matched rule
                  </Badge>
                )}
              </div>
            </div>
          </>
        ) : null}

        {timeline.length > 0 ? (
          <>
            <Separator className="bg-slate-100" />
            <div>
              <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-slate-700">
                <ClipboardCheck className="h-4 w-4 text-slate-500" />
                执行链路
              </div>
              <ul className="space-y-2">
                {timeline.map((item, index) => (
                  <li key={`${item.label}-${index}`} className="flex gap-2 rounded-lg border border-slate-200/70 bg-white px-3 py-2">
                    <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-slate-50">
                      {timelineIcon(item.status)}
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-medium text-slate-900">{item.label}</p>
                      {item.description ? (
                        <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">{item.description}</p>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
