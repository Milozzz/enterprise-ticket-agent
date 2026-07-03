"use client";

import { useEffect, useMemo, useState, type ElementType } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  Boxes,
  BrainCircuit,
  Database,
  FileSearch,
  Network,
  RotateCcw,
  ShieldCheck,
  Wrench,
} from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type UnknownRecord = Record<string, unknown>;

interface ToolItem {
  name: string;
  risk_level: string;
  side_effect: string;
  category: string;
  owner: string;
  retry_limit: number;
  timeout_seconds: number;
  idempotency_fields: string[];
}

interface ToolRegistry {
  tools?: ToolItem[];
  summary?: UnknownRecord;
  governance?: { controls?: string[]; boundary?: string };
}

interface KnowledgeBase {
  documents?: Array<{ id: string; title: string; permission_roles: string[]; content_length: number }>;
  summary?: UnknownRecord;
  governance?: UnknownRecord;
}

interface EvalReport {
  scenario_count?: number;
  case_count?: number;
  passed_count?: number;
  failed_count?: number;
  pass_rate?: number;
  avg_latency_ms?: number;
  scenarios?: Array<{ scenario_id: string; case_count: number; passed_count: number; pass_rate: number; avg_latency_ms: number }>;
}

function metricValue(value: unknown) {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "string") return value;
  return "-";
}

function riskTone(risk: string) {
  if (risk === "high") return "border-red-200 bg-red-50 text-red-700";
  if (risk === "medium") return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

export default function PlatformCapabilitiesPage() {
  const [toolRegistry, setToolRegistry] = useState<ToolRegistry | null>(null);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [evalReport, setEvalReport] = useState<EvalReport | null>(null);
  const [mcpTools, setMcpTools] = useState<unknown[]>([]);
  const [saga, setSaga] = useState<UnknownRecord | null>(null);
  const [templates, setTemplates] = useState<unknown[]>([]);

  useEffect(() => {
    Promise.all([
      fetch("/api/admin/tools/registry", { cache: "no-store" }).then((res) => res.json()),
      fetch("/api/admin/knowledge/policies", { cache: "no-store" }).then((res) => res.json()),
      fetch("/api/admin/evals/report", { cache: "no-store" }).then((res) => res.json()),
      fetch("/api/admin/tools/mcp", { cache: "no-store" }).then((res) => res.json()),
      fetch("/api/admin/saga/templates/refund", { cache: "no-store" }).then((res) => res.json()),
      fetch("/api/admin/scenarios/templates", { cache: "no-store" }).then((res) => res.json()),
    ]).then(([registry, kb, evals, mcp, sagaTemplate, templateCatalog]) => {
      setToolRegistry(registry);
      setKnowledgeBase(kb);
      setEvalReport(evals.eval_report ?? null);
      setMcpTools(mcp.tools ?? []);
      setSaga(sagaTemplate);
      setTemplates(templateCatalog.templates ?? []);
    });
  }, []);

  const highRiskTools = useMemo(
    () => (toolRegistry?.tools ?? []).filter((tool) => tool.risk_level === "high"),
    [toolRegistry]
  );

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
              <h1 className="text-xl font-bold text-slate-950">Platform Governance</h1>
              <p className="text-sm text-slate-500">Tool Registry、Knowledge Base、Eval Report 与 MCP-compatible assets</p>
            </div>
          </div>
          <Button asChild variant="outline">
            <Link href="/admin/evals">
              <BrainCircuit className="mr-2 h-4 w-4" />
              Eval Dashboard
            </Link>
          </Button>
        </div>

        <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <MetricCard icon={Wrench} label="Tools" value={metricValue(toolRegistry?.summary?.tool_count)} hint="registered gateway tools" />
          <MetricCard icon={ShieldCheck} label="High Risk" value={metricValue(toolRegistry?.summary?.high_risk_count)} hint="policy gated tools" />
          <MetricCard icon={FileSearch} label="Policies" value={metricValue(knowledgeBase?.summary?.document_count)} hint="citation-ready docs" />
          <MetricCard icon={BrainCircuit} label="Eval Pass" value={`${Math.round((evalReport?.pass_rate ?? 0) * 100)}%`} hint={`${evalReport?.passed_count ?? 0}/${evalReport?.case_count ?? 0} cases`} />
        </div>

        <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Wrench className="h-4 w-4 text-blue-600" />
                Tool Registry
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {(toolRegistry?.tools ?? []).map((tool) => (
                <div key={tool.name} className="rounded-md border border-slate-200 bg-white px-3 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div>
                      <div className="font-mono text-sm font-semibold text-slate-900">{tool.name}</div>
                      <div className="mt-1 text-xs text-slate-500">{tool.category} · owner: {tool.owner}</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <Badge className={riskTone(tool.risk_level)}>{tool.risk_level}</Badge>
                      <Badge variant="outline">{tool.side_effect}</Badge>
                    </div>
                  </div>
                  <div className="mt-2 grid gap-2 text-xs text-slate-500 sm:grid-cols-3">
                    <span>timeout {tool.timeout_seconds}s</span>
                    <span>retry {tool.retry_limit}</span>
                    <span>idem {tool.idempotency_fields.length ? tool.idempotency_fields.join(", ") : "none"}</span>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <FileSearch className="h-4 w-4 text-blue-600" />
                Knowledge Base
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {(knowledgeBase?.documents ?? []).slice(0, 6).map((doc) => (
                <div key={doc.id} className="rounded-md border border-slate-200 bg-white px-3 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-semibold text-slate-900">{doc.id}</div>
                    <Badge variant="outline">{doc.content_length} chars</Badge>
                  </div>
                  <p className="mt-1 text-sm text-slate-600">{doc.title}</p>
                  <p className="mt-1 text-xs text-slate-500">roles: {doc.permission_roles.join(", ")}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          <JsonPanel title="Scenario Eval Report" icon={BrainCircuit} data={evalReport} />
          <JsonPanel title="MCP-compatible Tools" icon={Network} data={{ count: mcpTools.length, highRiskTools }} />
          <JsonPanel title="Saga / Templates" icon={RotateCcw} data={{ saga, templateCount: templates.length }} />
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <InfoPanel
            icon={Database}
            title="Knowledge Base Upgrade"
            lines={[
              "Policy documents expose policy_id, clause_id, source, and role visibility.",
              "Search API returns citation-ready hits for RAG debugging.",
            ]}
          />
          <InfoPanel
            icon={Boxes}
            title="Scenario Platform Upgrade"
            lines={[
              "Scenario versions now support diff against current config.",
              "Eval report aggregates scenario pass rate, failed cases, and latency.",
            ]}
          />
        </div>
      </main>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: ElementType;
  label: string;
  value: string;
  hint: string;
}) {
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

function JsonPanel({ title, icon: Icon, data }: { title: string; icon: ElementType; data: unknown }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="h-4 w-4 text-blue-600" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <pre className="max-h-[360px] overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">
          {data ? JSON.stringify(data, null, 2) : "loading"}
        </pre>
      </CardContent>
    </Card>
  );
}

function InfoPanel({ icon: Icon, title, lines }: { icon: ElementType; title: string; lines: string[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="h-4 w-4 text-blue-600" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm text-slate-600">
        {lines.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </CardContent>
    </Card>
  );
}
