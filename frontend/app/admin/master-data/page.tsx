"use client";

import { useEffect, useState, type ElementType } from "react";
import Link from "next/link";
import { ArrowLeft, AlertTriangle, CheckCircle2, CopyCheck, FileClock, GitCommitHorizontal, ShieldCheck } from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type UnknownRecord = Record<string, unknown>;

interface MDGReport {
  summary?: UnknownRecord;
  rules?: UnknownRecord[];
  validation_results?: UnknownRecord[];
  duplicate_candidates?: UnknownRecord[];
  change_requests?: UnknownRecord[];
  versions?: UnknownRecord[];
  issues?: UnknownRecord[];
}

function text(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function statusTone(status: unknown) {
  const value = String(status || "").toUpperCase();
  if (["APPROVED", "APPLIED", "PASSED"].includes(value)) return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (["REJECTED", "FAILED", "CRITICAL"].includes(value)) return "border-red-200 bg-red-50 text-red-700";
  return "border-amber-200 bg-amber-50 text-amber-700";
}

export default function MasterDataGovernancePage() {
  const [report, setReport] = useState<MDGReport | null>(null);

  useEffect(() => {
    fetch("/api/erp/master-data-governance", { cache: "no-store" })
      .then((res) => res.json())
      .then(setReport);
  }, []);

  const summary = report?.summary ?? {};
  const changes = report?.change_requests ?? [];
  const issues = report?.issues ?? [];
  const duplicates = report?.duplicate_candidates ?? [];
  const versions = report?.versions ?? [];

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="icon">
              <Link href="/admin/business-data" aria-label="Back to business data">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-bold text-slate-950">Master Data Governance</h1>
              <p className="text-sm text-slate-500">Field validation, duplicate detection, change approval, and version history</p>
            </div>
          </div>
          <Button asChild variant="outline">
            <Link href="/admin/connectors">
              <GitCommitHorizontal className="mr-2 h-4 w-4" />
              Connectors
            </Link>
          </Button>
        </div>

        <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={ShieldCheck} label="Rules" value={text(summary.rule_count)} hint="validation policies" />
          <MetricCard icon={CopyCheck} label="Duplicates" value={text(summary.duplicate_candidate_count)} hint="open merge candidates" />
          <MetricCard icon={FileClock} label="Pending Changes" value={text(summary.pending_change_request_count)} hint="approval queue" />
          <MetricCard icon={AlertTriangle} label="Open Issues" value={text(summary.open_issue_count)} hint="data quality risks" />
        </div>

        <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <FileClock className="h-4 w-4 text-blue-600" />
                Change Requests
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {changes.map((item) => (
                <div key={text(item.mdg_request_id)} className="rounded-md border border-slate-200 bg-white px-3 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="font-mono text-sm font-semibold text-slate-900">{text(item.mdg_request_id)}</div>
                      <div className="mt-1 text-xs text-slate-500">{text(item.object_type)} / {text(item.object_id)}</div>
                    </div>
                    <Badge className={statusTone(item.status)}>{text(item.status)}</Badge>
                  </div>
                  <pre className="mt-2 max-h-32 overflow-auto rounded-md bg-slate-950 p-2 text-xs text-slate-100">
                    {JSON.stringify(item.proposed_change ?? {}, null, 2)}
                  </pre>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <CopyCheck className="h-4 w-4 text-blue-600" />
                Duplicate Candidates
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {duplicates.map((item) => (
                <div key={text(item.duplicate_id)} className="rounded-md border border-slate-200 bg-white px-3 py-3">
                  <div className="font-mono text-sm font-semibold text-slate-900">{text(item.duplicate_id)}</div>
                  <div className="mt-1 text-sm text-slate-700">{text(item.left_object_id)} {"->"} {text(item.right_object_id)}</div>
                  <div className="mt-2 h-2 rounded bg-slate-100">
                    <div className="h-2 rounded bg-blue-500" style={{ width: `${Math.round(Number(item.match_score || 0) * 100)}%` }} />
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_1fr]">
          <RecordList title="Data Quality Issues" icon={AlertTriangle} rows={issues} idField="issue_id" statusField="severity" />
          <RecordList title="Version History" icon={CheckCircle2} rows={versions} idField="version_id" statusField="object_type" />
        </div>
      </main>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, hint }: { icon: ElementType; label: string; value: string; hint: string }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-slate-600">{label}</CardTitle>
        <Icon className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold text-slate-950">{value}</div>
        <p className="mt-1 text-xs text-slate-500">{hint}</p>
      </CardContent>
    </Card>
  );
}

function RecordList({ title, icon: Icon, rows, idField, statusField }: { title: string; icon: ElementType; rows: UnknownRecord[]; idField: string; statusField: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="h-4 w-4 text-blue-600" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {rows.map((item) => (
          <div key={text(item[idField])} className="rounded-md border border-slate-200 bg-white px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-mono text-xs font-semibold text-slate-900">{text(item[idField])}</div>
              <Badge className={statusTone(item[statusField])}>{text(item[statusField])}</Badge>
            </div>
            <div className="mt-1 text-xs text-slate-500">{text(item.description ?? item.change_reason ?? item.message)}</div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
