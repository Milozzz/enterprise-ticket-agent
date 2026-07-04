"use client";

import { useEffect, useMemo, useState, type ElementType } from "react";
import Link from "next/link";
import { ArrowLeft, Boxes, ClipboardList, Database, FileText, GitBranch, RefreshCcw, ShieldCheck } from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Counts = Record<string, number>;
type UnknownRecord = Record<string, unknown>;

interface ErpSummary {
  doctype_count?: number;
  counts?: Counts;
  commercial_readiness?: Record<string, boolean>;
}

interface ErpCatalog {
  doctypes?: string[];
  workflows?: Array<{ id: string; description: string; seed_endpoint: string }>;
}

interface OrderAggregate {
  order?: UnknownRecord;
  customer?: UnknownRecord | null;
  refund_requests?: UnknownRecord[];
  support_tickets?: UnknownRecord[];
  document_flows?: UnknownRecord[];
  return_authorizations?: UnknownRecord[];
  inventory_movements?: UnknownRecord[];
  stock_reservations?: UnknownRecord[];
  open_items?: UnknownRecord[];
  change_documents?: UnknownRecord[];
  journal_entries?: UnknownRecord[];
  policy_versions?: UnknownRecord[];
  data_quality_issues?: UnknownRecord[];
  enterprise_reference?: Record<string, UnknownRecord[]>;
  events?: UnknownRecord[];
  validation?: { valid?: boolean; errors?: string[]; checks?: UnknownRecord };
}

const DEFAULT_ORDER_ID = "ERP-ORD-1002";

export default function BusinessDataCenterPage() {
  const [catalog, setCatalog] = useState<ErpCatalog | null>(null);
  const [summary, setSummary] = useState<ErpSummary | null>(null);
  const [aggregate, setAggregate] = useState<OrderAggregate | null>(null);
  const [loading, setLoading] = useState(false);
  const [seedMessage, setSeedMessage] = useState("");

  async function refresh() {
    setLoading(true);
    try {
      const [catalogRes, summaryRes, orderRes] = await Promise.all([
        fetch("/api/erp/catalog", { cache: "no-store" }),
        fetch("/api/erp/summary", { cache: "no-store" }),
        fetch(`/api/erp/orders/${DEFAULT_ORDER_ID}`, { cache: "no-store" }),
      ]);
      setCatalog(await catalogRes.json());
      setSummary(await summaryRes.json());
      setAggregate(orderRes.ok ? await orderRes.json() : null);
    } finally {
      setLoading(false);
    }
  }

  async function seedDemoData() {
    setLoading(true);
    setSeedMessage("");
    try {
      const res = await fetch("/api/erp/seed/return-to-refund", { method: "POST" });
      const json = await res.json();
      setSeedMessage(`Seeded ${json.case_count ?? 0} return-to-refund cases`);
      await refresh();
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const readiness = summary?.commercial_readiness ?? {};
  const topDoctypes = useMemo(() => {
    const counts = summary?.counts ?? {};
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 18);
  }, [summary]);

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="icon">
              <Link href="/admin/platform" aria-label="Back to platform">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-bold text-slate-950">Business Data Center</h1>
              <p className="text-sm text-slate-500">Mini ERP schema, document flow, finance controls, P2P/MFG/Asset demo data</p>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => void refresh()} disabled={loading}>
              <RefreshCcw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
            <Button onClick={() => void seedDemoData()} disabled={loading}>
              <Database className="mr-2 h-4 w-4" />
              Seed Demo Data
            </Button>
          </div>
        </div>

        {seedMessage ? <div className="mb-4 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{seedMessage}</div> : null}

        <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={Database} label="Doctypes" value={summary?.doctype_count ?? 0} hint="ERP-like resources" />
          <MetricCard icon={GitBranch} label="Document Flow" value={summary?.counts?.document_flow ?? 0} hint="linked ERP documents" />
          <MetricCard icon={FileText} label="Open Items" value={summary?.counts?.open_item ?? 0} hint="AR/AP finance controls" />
          <MetricCard icon={ClipboardList} label="Work Orders" value={summary?.counts?.work_order ?? 0} hint="manufacturing sample" />
        </div>

        <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldCheck className="h-4 w-4 text-blue-600" />
                Commercial Readiness
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 sm:grid-cols-2">
              {Object.entries(readiness).map(([key, ready]) => (
                <div key={key} className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-3 py-2">
                  <span className="text-sm text-slate-700">{key.replaceAll("_", " ")}</span>
                  <Badge className={ready ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-700"}>
                    {ready ? "ready" : "missing"}
                  </Badge>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Boxes className="h-4 w-4 text-blue-600" />
                Doctype Coverage
              </CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {topDoctypes.map(([doctype, count]) => (
                <div key={doctype} className="rounded-md border border-slate-200 bg-white px-3 py-2">
                  <div className="font-mono text-xs font-semibold text-slate-800">{doctype}</div>
                  <div className="mt-1 text-lg font-bold text-slate-950">{count}</div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_1fr]">
          <JsonPanel title={`Order Aggregate ${DEFAULT_ORDER_ID}`} data={aggregate} />
          <JsonPanel title="Workflow Catalog" data={catalog?.workflows ?? []} />
        </div>
      </main>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, hint }: { icon: ElementType; label: string; value: number; hint: string }) {
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

function JsonPanel({ title, data }: { title: string; data: unknown }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <pre className="max-h-[520px] overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">
          {data ? JSON.stringify(data, null, 2) : "No data. Seed demo data first."}
        </pre>
      </CardContent>
    </Card>
  );
}
