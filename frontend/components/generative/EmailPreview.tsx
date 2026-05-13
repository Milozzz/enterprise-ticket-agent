"use client";

import { cn, formatDate } from "@/lib/utils";
import { Mail, CheckCircle2 } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

interface EmailPreviewData {
  to: string;
  subject: string;
  body: string;
  sentAt?: string;
  status?: "preview" | "sent";
}

interface EmailPreviewProps {
  data: EmailPreviewData;
}

export default function EmailPreview({ data }: EmailPreviewProps) {
  const isSent = data?.status === "sent";

  if (!data) return null;

  return (
    <Card
      className={cn(
        "w-full overflow-hidden border border-slate-200/80 shadow-sm ring-1 ring-black/[0.04]",
        "animate-in fade-in slide-in-from-bottom-2 duration-300"
      )}
    >
      <CardHeader className="space-y-0 border-b border-slate-100 bg-slate-50/60 px-4 py-3 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-background shadow-sm">
              <Mail className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="min-w-0 space-y-1">
              <CardTitle className="text-base font-semibold tracking-tight text-slate-900">
                {isSent ? "邮件已发送" : "邮件预览"}
              </CardTitle>
              <CardDescription className="text-xs">
                {isSent ? "通知已投递至用户邮箱" : "发送前预览正文内容"}
              </CardDescription>
            </div>
          </div>
          {isSent ? (
            <Badge
              variant="outline"
              className="h-6 shrink-0 gap-1 border-emerald-200 bg-emerald-50 font-medium text-emerald-800 shadow-none"
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              已发送
              {data.sentAt ? (
                <span className="ml-1 font-mono text-[10px] font-normal text-emerald-700/80">
                  {formatDate(data.sentAt)}
                </span>
              ) : null}
            </Badge>
          ) : (
            <Badge variant="secondary" className="h-6 shrink-0 text-[10px] font-medium">
              草稿
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-0 px-0 pb-0 pt-0">
        <div className="grid gap-3 border-b border-slate-100 bg-white px-4 py-3 sm:px-5">
          <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
            <span className="w-14 shrink-0 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              收件人
            </span>
            <span className="break-all text-sm font-medium text-slate-900">{data.to}</span>
          </div>
          <Separator className="bg-slate-100" />
          <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-3">
            <span className="w-14 shrink-0 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
              主题
            </span>
            <span className="text-sm text-slate-800">{data.subject}</span>
          </div>
        </div>

        <div className="px-4 py-3 sm:px-5">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-400">
            正文
          </p>
          <div className="rounded-lg border border-slate-200 bg-slate-950/95 px-3 py-3 shadow-inner">
            <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-emerald-400/95 [scrollbar-width:thin]">
              {data.body}
            </pre>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
