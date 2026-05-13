"use client";

import { cn } from "@/lib/utils";
import { ShieldAlert, ShieldCheck, AlertTriangle } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Progress } from "@/components/ui/progress";

interface RiskAlertData {
  riskScore?: number;
  risk_score?: number;
  riskLevel?: "low" | "medium" | "high";
  risk_level?: "low" | "medium" | "high";
  reasons: string[];
  autoApprove?: boolean;
  auto_approve?: boolean;
  threshold: number;
  recommendation?: string;
}

interface RiskAlertProps {
  data: RiskAlertData;
}

export default function RiskAlert({ data }: RiskAlertProps) {
  const riskLevel = data.riskLevel ?? data.risk_level ?? "low";
  const riskScore = Math.min(100, Math.max(0, data.riskScore ?? data.risk_score ?? 0));
  const autoApprove = data.autoApprove ?? data.auto_approve ?? true;
  const recommendation = data.recommendation;

  const config = {
    high: {
      card: "border-red-200/80 bg-red-50/40",
      ring: "ring-red-200/30",
      header: "border-b border-red-100 bg-red-50/80",
      iconWrap: "border-red-200 bg-red-50",
      icon: ShieldAlert,
      iconColor: "text-red-600",
      title: "text-red-900",
      desc: "text-red-700/80",
      badge: "border-red-200 bg-red-600 text-white hover:bg-red-600",
      progressTrack: "bg-red-100",
      progressBar: "[&>div]:bg-red-500",
      bullet: "text-red-400",
    },
    medium: {
      card: "border-orange-200/80 bg-orange-50/40",
      ring: "ring-orange-200/30",
      header: "border-b border-orange-100 bg-orange-50/80",
      iconWrap: "border-orange-200 bg-orange-50",
      icon: AlertTriangle,
      iconColor: "text-orange-600",
      title: "text-orange-900",
      desc: "text-orange-800/80",
      badge: "border-orange-200 bg-orange-500 text-white hover:bg-orange-500",
      progressTrack: "bg-orange-100",
      progressBar: "[&>div]:bg-orange-500",
      bullet: "text-orange-400",
    },
    low: {
      card: "border-emerald-200/80 bg-emerald-50/40",
      ring: "ring-emerald-200/30",
      header: "border-b border-emerald-100 bg-emerald-50/80",
      iconWrap: "border-emerald-200 bg-emerald-50",
      icon: ShieldCheck,
      iconColor: "text-emerald-600",
      title: "text-emerald-900",
      desc: "text-emerald-800/80",
      badge: "border-emerald-200 bg-emerald-600 text-white hover:bg-emerald-600",
      progressTrack: "bg-emerald-100",
      progressBar: "[&>div]:bg-emerald-500",
      bullet: "text-emerald-400",
    },
  }[riskLevel];

  const Icon = config.icon;
  const levelLabel =
    riskLevel === "high" ? "高风险" : riskLevel === "medium" ? "中风险" : "低风险";

  return (
    <Card
      className={cn(
        "w-full overflow-hidden shadow-sm ring-1",
        config.card,
        config.ring,
        "animate-in fade-in slide-in-from-bottom-2 duration-300"
      )}
    >
      <CardHeader className={cn("space-y-0 px-4 py-3 sm:px-5", config.header)}>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border shadow-sm",
                config.iconWrap
              )}
            >
              <Icon className={cn("h-4 w-4", config.iconColor)} />
            </div>
            <div className="min-w-0 space-y-1">
              <CardTitle className={cn("text-base font-semibold tracking-tight", config.title)}>
                风控评估结果
              </CardTitle>
              <CardDescription className={cn("text-xs", config.desc)}>
                基于金额、原因与历史规则的综合评分
              </CardDescription>
            </div>
          </div>
          <Badge variant="outline" className={cn("shrink-0 border font-semibold shadow-none", config.badge)}>
            {levelLabel}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4 px-4 py-4 sm:px-5">
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-slate-600">风险评分</span>
            <span className={cn("font-mono font-bold tabular-nums", config.title)}>
              {riskScore} / 100
            </span>
          </div>
          <Progress
            value={riskScore}
            className={cn("h-2.5", config.progressTrack, config.progressBar)}
          />
          <div className="flex flex-wrap items-center gap-x-1 gap-y-2 text-[11px] leading-relaxed text-slate-600">
            <span>
              阈值 <span className="font-mono font-medium">{data.threshold}</span>
            </span>
            {autoApprove ? (
              <Badge variant="secondary" className="h-5 text-[10px]">
                可自动审批
              </Badge>
            ) : (
              <Badge variant="outline" className="h-5 border-amber-200 bg-amber-50 text-[10px] text-amber-900">
                需人工审核
              </Badge>
            )}
          </div>
        </div>

        {recommendation ? (
          <>
            <Separator className="bg-slate-200/80" />
            <p className="rounded-lg border border-slate-200/80 bg-white/60 px-3 py-2 text-xs leading-relaxed text-slate-700">
              {recommendation}
            </p>
          </>
        ) : null}

        {data.reasons?.length > 0 ? (
          <>
            <Separator className="bg-slate-200/80" />
            <div>
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
                风险因素
              </p>
              <ul className="space-y-2">
                {data.reasons.map((reason, i) => (
                  <li
                    key={i}
                    className="flex gap-2 text-xs leading-relaxed text-slate-700"
                  >
                    <span className={cn("mt-0.5 shrink-0 font-bold", config.bullet)}>·</span>
                    <span>{reason}</span>
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
