"use client";

import { cn, formatDate } from "@/lib/utils";
import { CheckCircle2, Circle, Loader2, ListOrdered } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
interface TimelineStep {
  label: string;
  description?: string;
  timestamp?: string;
  status: "completed" | "current" | "pending";
}

interface RefundTimelineProps {
  data?: { steps: TimelineStep[] };
  steps?: TimelineStep[];
}

export default function RefundTimeline({ data, steps: stepsProp }: RefundTimelineProps) {
  const steps = data?.steps ?? stepsProp ?? [];

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
            <ListOrdered className="h-4 w-4 text-muted-foreground" />
          </div>
          <div className="min-w-0 space-y-1">
            <CardTitle className="text-base font-semibold tracking-tight text-slate-900">
              退款进度
            </CardTitle>
            <CardDescription className="text-xs">
              当前流程节点与完成状态
            </CardDescription>
          </div>
        </div>
      </CardHeader>

      <CardContent className="relative px-4 py-4 sm:px-5">
        <div
          className="absolute left-[2.125rem] top-6 bottom-6 w-px bg-slate-200 sm:left-[2.375rem]"
          aria-hidden
        />

        <ul className="relative space-y-5">
          {steps.map((step, index) => (
            <li key={index}>
              <div className="flex gap-3">
                <div className="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200 bg-background shadow-sm">
                  {step.status === "completed" && (
                    <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  )}
                  {step.status === "current" && (
                    <Loader2 className="h-4 w-4 animate-spin text-primary" />
                  )}
                  {step.status === "pending" && (
                    <Circle className="h-4 w-4 text-slate-300" />
                  )}
                </div>

                <div className="min-w-0 flex-1 pb-0.5 pt-0.5">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
                    <span
                      className={cn(
                        "text-sm font-medium",
                        step.status === "completed" && "text-slate-900",
                        step.status === "current" && "text-primary",
                        step.status === "pending" && "text-slate-400"
                      )}
                    >
                      {step.label}
                    </span>
                    {step.timestamp ? (
                      <span className="font-mono text-[10px] text-slate-400">
                        {formatDate(step.timestamp)}
                      </span>
                    ) : null}
                  </div>
                  {step.description ? (
                    <p
                      className={cn(
                        "mt-1 text-xs leading-relaxed",
                        step.status === "pending" ? "text-slate-400" : "text-slate-600"
                      )}
                    >
                      {step.description}
                    </p>
                  ) : null}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
