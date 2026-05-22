"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ClipboardCheck, Loader2, RefreshCw } from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface ApprovalItem {
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
  createdAt?: string;
}

export default function ApprovalCenterPage() {
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadApprovals = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/agent/approval-center?limit=100", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "加载审批中心失败");
      setItems(data.items ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载审批中心失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadApprovals();
  }, []);

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
              <p className="text-sm text-slate-500">统一查看多场景、多级审批决策记录</p>
            </div>
          </div>
          <Button variant="outline" onClick={loadApprovals} disabled={loading}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            刷新
          </Button>
        </div>

        {error ? <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div> : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ClipboardCheck className="h-4 w-4 text-blue-600" />
              审批决策
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="flex items-center justify-center py-16 text-sm text-slate-500">
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                加载中
              </div>
            ) : items.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[960px] text-left text-sm">
                  <thead className="border-b border-slate-200 text-xs uppercase text-slate-500">
                    <tr>
                      <th className="py-3 pr-4">Request</th>
                      <th className="py-3 pr-4">Scenario</th>
                      <th className="py-3 pr-4">Stage</th>
                      <th className="py-3 pr-4">Action</th>
                      <th className="py-3 pr-4">Reviewer</th>
                      <th className="py-3 pr-4">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.decisionId} className="border-b border-slate-100">
                        <td className="py-3 pr-4 font-mono text-xs text-slate-700">{item.requestId}</td>
                        <td className="py-3 pr-4">
                          <div className="font-medium text-slate-900">{item.scenarioId}</div>
                          <div className="text-xs text-slate-500">{item.approvalType}</div>
                        </td>
                        <td className="py-3 pr-4">
                          <div className="text-slate-800">{item.stageName || item.stageId || "scenario-level"}</div>
                        </td>
                        <td className="py-3 pr-4">
                          <Badge variant="outline" className={item.status === "approved" ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-red-200 bg-red-50 text-red-700"}>
                            {item.action}
                          </Badge>
                        </td>
                        <td className="py-3 pr-4">
                          <div className="text-slate-800">{item.reviewerRole}</div>
                          <div className="text-xs text-slate-500">{item.reviewerId}</div>
                        </td>
                        <td className="py-3 pr-4 text-xs text-slate-500">{item.createdAt || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-10 text-center text-sm text-slate-500">
                暂无审批决策记录。运行需要 HITL 的场景并点击审批后，这里会出现记录。
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
