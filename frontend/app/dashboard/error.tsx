"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[dashboard]", error);
  }, [error]);

  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4 bg-slate-50 px-4">
      <AlertTriangle className="h-10 w-10 text-destructive" />
      <h2 className="text-center text-lg font-semibold text-slate-900">面板加载异常</h2>
      <p className="max-w-md text-center text-sm text-slate-600">{error.message}</p>
      <div className="flex gap-3">
        <Button variant="outline" onClick={() => reset()}>
          重试
        </Button>
        <Button asChild>
          <Link href="/">返回对话</Link>
        </Button>
      </div>
    </div>
  );
}
