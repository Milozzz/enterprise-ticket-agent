"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, CheckCircle2, FlaskConical, Loader2, ShieldCheck, XCircle } from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Dict = Record<string, any>;

export default function EvalDashboardPage() {
  const [report, setReport] = useState<Dict | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/admin/evals/p0-report", { cache: "no-store" })
      .then(async (res) => {
        const body = await res.json();
        if (!res.ok) throw new Error(body.detail ?? `Eval request failed (${res.status})`);
        setReport(body.eval_report);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "评测报告加载失败"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="icon">
              <Link href="/admin/platform" aria-label="返回平台治理">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-bold text-slate-950">Agent Eval Dashboard</h1>
              <p className="text-sm text-slate-500">轨迹、RAG、安全红队与回答质量门禁</p>
            </div>
          </div>
          {report ? (
            <Badge className={report.status === "PASS" ? "bg-emerald-600" : "bg-red-600"}>
              {report.status}
            </Badge>
          ) : null}
        </div>

        {loading ? (
          <div className="flex h-56 items-center justify-center text-sm text-slate-500">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />正在运行确定性评测
          </div>
        ) : error ? (
          <div className="border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
        ) : report ? (
          <div className="space-y-5">
            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <Metric label="Golden Trajectories" value={report.summary.trajectory_cases} detail={`${Math.round(report.trajectory.pass_rate * 100)}% pass`} />
              <Metric label="RAG Recall@4" value={`${Math.round(report.rag.recall_at_k * 100)}%`} detail={`${report.rag.corpus_document_count} policy documents`} />
              <Metric label="Safety Red Team" value={`${report.safety.passed_count}/${report.safety.case_count}`} detail="injection / RBAC / PII" />
              <Metric label="Answer Judge" value={`${report.judge.passed_count}/${report.judge.case_count}`} detail={report.judge.mode} />
            </section>

            <section className="border-y border-slate-200 bg-white px-4 py-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-900">Release Quality Gates</h2>
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                {Object.entries(report.quality_gates).map(([name, passed]) => (
                  <div key={name} className="flex items-center gap-2 text-sm text-slate-600">
                    {passed ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <XCircle className="h-4 w-4 text-red-600" />}
                    <span>{name}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="grid gap-4 xl:grid-cols-2">
              <Card>
                <CardHeader><CardTitle className="flex items-center gap-2 text-base"><FlaskConical className="h-4 w-4 text-blue-600" />Golden Trajectory</CardTitle></CardHeader>
                <CardContent className="space-y-3">
                  {report.trajectory.scenarios.flatMap((scenario: Dict) => scenario.results).map((item: Dict) => (
                    <div key={item.case_id} className="border-b border-slate-100 pb-3 last:border-0">
                      <div className="flex items-center justify-between gap-2 text-sm font-medium">
                        <span>{item.case_id}</span><Badge variant="outline">{item.passed ? "PASS" : "FAIL"}</Badge>
                      </div>
                      <p className="mt-1 text-xs text-slate-500">{item.output.trajectory?.route?.join(" -> ")}</p>
                      <p className="mt-1 font-mono text-xs text-slate-400">tools: {item.output.trajectory?.tool_sequence?.join(", ")}</p>
                    </div>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader><CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="h-4 w-4 text-blue-600" />Safety & RAG</CardTitle></CardHeader>
                <CardContent className="space-y-4">
                  {Object.entries(report.safety.categories).map(([name, metric]: [string, any]) => (
                    <div key={name} className="flex items-center justify-between border-b border-slate-100 pb-2 text-sm">
                      <span>{name}</span><span className="font-mono text-slate-500">{metric.passed_count}/{metric.case_count}</span>
                    </div>
                  ))}
                  <div className="grid grid-cols-2 gap-3 border-t border-slate-200 pt-4 text-sm">
                    <div><div className="text-slate-500">Citation faithfulness</div><div className="mt-1 text-lg font-semibold">{Math.round(report.rag.citation_faithfulness * 100)}%</div></div>
                    <div><div className="text-slate-500">Retrieval cases</div><div className="mt-1 text-lg font-semibold">{report.rag.passed_count}/{report.rag.case_count}</div></div>
                  </div>
                </CardContent>
              </Card>
            </section>
          </div>
        ) : null}
      </main>
    </div>
  );
}

function Metric({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return (
    <Card>
      <CardHeader className="pb-2"><CardTitle className="text-sm font-medium text-slate-500">{label}</CardTitle></CardHeader>
      <CardContent><div className="text-2xl font-bold text-slate-950">{value}</div><p className="mt-1 text-xs text-slate-500">{detail}</p></CardContent>
    </Card>
  );
}
