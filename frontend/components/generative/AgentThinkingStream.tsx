"use client";

import { cn } from "@/lib/utils";
import type { AgentThinkingStep } from "@/types";
import {
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
  Search,
  Shield,
  CreditCard,
  Mail,
  User,
  Bot,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface AgentThinkingStreamProps {
  steps: AgentThinkingStep[];
}

const stepIcons: Record<string, React.ElementType> = {
  classifying: Search,
  looking_up_order: Search,
  checking_risk: Shield,
  executing_refund: CreditCard,
  sending_notification: Mail,
  awaiting_human: User,
  completed: CheckCircle2,
  error: XCircle,
};

const stepLabels: Record<string, string> = {
  classifying: "意图识别",
  looking_up_order: "查询订单",
  checking_risk: "风控评估",
  executing_refund: "执行退款",
  sending_notification: "发送通知",
  awaiting_human: "等待审批",
  completed: "处理完成",
  error: "处理异常",
};

export default function AgentThinkingStream({ steps }: AgentThinkingStreamProps) {
  if (!steps.length) return null;

  return (
    <Card
      className={cn(
        "w-full overflow-hidden border border-slate-200/80 bg-card shadow-sm ring-1 ring-black/[0.04]",
        "animate-in fade-in slide-in-from-bottom-2 duration-300"
      )}
    >
      <CardHeader className="space-y-0 border-b border-slate-100 bg-slate-50/60 px-4 py-3 sm:px-5">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-background shadow-sm">
            <Bot className="h-4 w-4 text-primary" aria-hidden />
          </div>
          <div className="min-w-0 flex-1 space-y-1">
            <CardTitle className="text-base font-semibold tracking-tight text-slate-900">
              Agent 执行链路
            </CardTitle>
            <CardDescription className="text-xs">
              节点状态实时更新（{steps.length} 步）
            </CardDescription>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-0 px-0 py-0">
        <ul className="divide-y divide-slate-100 bg-white">
          {steps.map((step, index) => {
            const Icon = stepIcons[step.step] ?? Circle;
            const label = step.label || stepLabels[step.step] || step.step;

            return (
              <li key={index} className="px-4 py-3 sm:px-5">
                <div className="flex items-start gap-3">
                  <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-slate-50">
                    {step.status === "running" && (
                      <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                    )}
                    {step.status === "done" && (
                      <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                    )}
                    {step.status === "error" && (
                      <XCircle className="h-3.5 w-3.5 text-destructive" />
                    )}
                    {step.status === "pending" && (
                      <Circle className="h-3.5 w-3.5 text-slate-300" />
                    )}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                      <Icon
                        className={cn(
                          "h-3.5 w-3.5 shrink-0",
                          step.status === "done" && "text-emerald-600",
                          step.status === "running" && "text-primary",
                          step.status === "error" && "text-destructive",
                          step.status === "pending" && "text-slate-400"
                        )}
                      />
                      <span
                        className={cn(
                          "text-sm font-medium",
                          step.status === "done" && "text-slate-900",
                          step.status === "running" && "text-primary",
                          step.status === "error" && "text-destructive",
                          step.status === "pending" && "text-slate-400"
                        )}
                      >
                        {label}
                      </span>
                      {step.timestamp ? (
                        <span className="ml-auto font-mono text-[10px] text-slate-400">
                          {step.timestamp}
                        </span>
                      ) : null}
                    </div>

                    {step.detail && step.status !== "pending" ? (
                      <div
                        className={cn(
                          "mt-1.5 text-xs leading-relaxed text-slate-600",
                          step.status === "running" && "streaming-cursor text-primary"
                        )}
                      >
                        {step.detail}
                      </div>
                    ) : null}
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
