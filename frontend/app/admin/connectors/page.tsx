"use client";

import { useCallback, useEffect, useMemo, useState, type ElementType } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowLeft,
  BellRing,
  Cable,
  CheckCircle2,
  CircleAlert,
  Clock3,
  DatabaseZap,
  GitBranch,
  KeyRound,
  Play,
  RadioTower,
  RefreshCcw,
  Save,
  ShieldCheck,
} from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type UnknownRecord = Record<string, unknown>;

interface ConnectorReport {
  summary?: UnknownRecord;
  connectors?: UnknownRecord[];
  webhooks?: UnknownRecord[];
  outbox_events?: UnknownRecord[];
}

interface ExceptionReport {
  summary?: UnknownRecord;
  data_quality_issues?: UnknownRecord[];
  reconciliation_issues?: UnknownRecord[];
}

interface ReadinessReport {
  interview_demo_ready?: boolean;
  production_ready?: boolean;
  required_pass_rate?: number;
  checks?: Array<{ id: string; status: string; description: string; passed: boolean }>;
  next_actions?: string[];
}

interface ConnectorForm {
  name: string;
  systemType: string;
  baseUrl: string;
  authType: string;
  status: string;
  mode: string;
  readOnly: boolean;
  shadowWrites: boolean;
  verifyTls: boolean;
  timeoutSeconds: number;
  maxRetries: number;
  changeTicket: string;
  operationPaths: string;
}

const DEFAULT_CONNECTOR_ID = "CONN-SAP-ODATA-DEMO";
const DEFAULT_PATHS = {
  get_order: "/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{order_id}')",
  create_credit_memo: "/sap/opu/odata/sap/API_CREDIT_MEMO_REQUEST_SRV/A_CreditMemoRequest",
  "doctype.business_partner": "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner",
};

const DEFAULT_FORM: ConnectorForm = {
  name: "SAP S/4HANA OData",
  systemType: "sap_s4hana",
  baseUrl: "https://sandbox.api.sap.com",
  authType: "api_key",
  status: "DRAFT",
  mode: "mock",
  readOnly: true,
  shadowWrites: true,
  verifyTls: true,
  timeoutSeconds: 15,
  maxRetries: 2,
  changeTicket: "",
  operationPaths: JSON.stringify(DEFAULT_PATHS, null, 2),
};

function text(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

function numberValue(value: unknown) {
  return typeof value === "number" ? value : Number(value || 0);
}

function tone(value: unknown) {
  const normalized = String(value || "").toUpperCase();
  if (["ACTIVE", "DISPATCHED", "RESOLVED", "PASS", "HEALTHY", "COMPLETED"].includes(normalized)) {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (["ERROR", "FAILED", "DEAD_LETTER", "CRITICAL", "FAIL", "UNHEALTHY"].includes(normalized)) {
    return "border-red-200 bg-red-50 text-red-700";
  }
  return "border-amber-200 bg-amber-50 text-amber-700";
}

async function jsonOrError(response: Response) {
  const body = await response.json();
  if (!response.ok) throw new Error(body?.detail?.error || body?.detail || `HTTP ${response.status}`);
  return body;
}

export default function ExternalConnectorsPage() {
  const [connectorId, setConnectorId] = useState(DEFAULT_CONNECTOR_ID);
  const [connectorReport, setConnectorReport] = useState<ConnectorReport | null>(null);
  const [exceptionReport, setExceptionReport] = useState<ExceptionReport | null>(null);
  const [runtime, setRuntime] = useState<UnknownRecord | null>(null);
  const [health, setHealth] = useState<UnknownRecord | null>(null);
  const [readiness, setReadiness] = useState<ReadinessReport | null>(null);
  const [metrics, setMetrics] = useState<UnknownRecord | null>(null);
  const [form, setForm] = useState<ConnectorForm>(DEFAULT_FORM);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const hydrateForm = useCallback((report: ConnectorReport) => {
    const item = (report.connectors ?? []).find((row) => row.connector_id === connectorId);
    if (!item) return;
    const config = (item.config as UnknownRecord | undefined) ?? {};
    setForm({
      name: text(item.name) === "-" ? DEFAULT_FORM.name : text(item.name),
      systemType: text(item.system_type) === "-" ? DEFAULT_FORM.systemType : text(item.system_type),
      baseUrl: text(item.base_url) === "-" ? DEFAULT_FORM.baseUrl : text(item.base_url),
      authType: text(item.auth_type) === "-" ? DEFAULT_FORM.authType : text(item.auth_type),
      status: text(item.status) === "-" ? "DRAFT" : text(item.status).toUpperCase(),
      mode: text(config.mode) === "-" ? "mock" : text(config.mode),
      readOnly: config.read_only === undefined ? true : Boolean(config.read_only),
      shadowWrites: config.shadow_writes === undefined ? true : Boolean(config.shadow_writes),
      verifyTls: config.verify_tls === undefined ? true : Boolean(config.verify_tls),
      timeoutSeconds: numberValue(config.timeout_seconds) || 15,
      maxRetries: numberValue(config.max_retries) || 2,
      changeTicket: text(config.change_ticket) === "-" ? "" : text(config.change_ticket),
      operationPaths: JSON.stringify(config.operation_paths ?? DEFAULT_PATHS, null, 2),
    });
  }, [connectorId]);

  const refresh = useCallback(async () => {
    setBusy(true);
    setResult(null);
    try {
      const [connectors, exceptions, runtimeData, healthData, readinessData, metricData] = await Promise.all([
        fetch("/api/erp/connectors", { cache: "no-store" }).then(jsonOrError),
        fetch("/api/erp/exceptions", { cache: "no-store" }).then(jsonOrError),
        fetch(`/api/erp/runtime/${connectorId}/config`, { cache: "no-store" }).then(jsonOrError),
        fetch(`/api/erp/runtime/${connectorId}/health`, { cache: "no-store" }).then(jsonOrError),
        fetch(`/api/admin/evals/enterprise-readiness?connector_id=${encodeURIComponent(connectorId)}`, { cache: "no-store" }).then(jsonOrError),
        fetch("/api/dashboard/erp-business-metrics?days=7", { cache: "no-store" }).then(jsonOrError),
      ]);
      setConnectorReport(connectors);
      setExceptionReport(exceptions);
      setRuntime(runtimeData.connector ?? null);
      setHealth(healthData);
      setReadiness(readinessData.readiness ?? null);
      setMetrics(metricData);
      hydrateForm(connectors);
    } catch (error) {
      setResult({ ok: false, message: error instanceof Error ? error.message : "加载失败" });
    } finally {
      setBusy(false);
    }
  }, [connectorId, hydrateForm]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function saveConfig() {
    setBusy(true);
    setResult(null);
    try {
      const operationPaths = JSON.parse(form.operationPaths) as Record<string, string>;
      const response = await fetch(`/api/erp/runtime/${connectorId}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name,
          system_type: form.systemType,
          base_url: form.baseUrl,
          auth_type: form.authType,
          status: form.status,
          mode: form.mode,
          read_only: form.readOnly,
          shadow_writes: form.shadowWrites,
          verify_tls: form.verifyTls,
          timeout_seconds: form.timeoutSeconds,
          max_retries: form.maxRetries,
          circuit_failure_threshold: 3,
          circuit_reset_seconds: 30,
          operation_paths: operationPaths,
          capabilities: { odata: true, csrf: true, etag: true, idempotency: true },
          change_ticket: form.changeTicket || null,
        }),
      });
      await jsonOrError(response);
      setResult({ ok: true, message: "Connector 配置已保存，密钥未写入数据库。" });
      await refresh();
    } catch (error) {
      setResult({ ok: false, message: error instanceof Error ? error.message : "保存失败" });
    } finally {
      setBusy(false);
    }
  }

  async function runShadowSaga() {
    setBusy(true);
    setResult(null);
    try {
      const response = await fetch("/api/erp/runtime/refunds/execute-finance-saga", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          order_id: "ERP-ORD-1002",
          refund_request_id: `SHADOW-${Date.now()}`,
          open_item_id: "OI-ERP-1002",
          amount: 1299,
          currency: "CNY",
          connector_id: connectorId,
          approval_id: "APR-SHADOW-CONSOLE",
          dry_run: true,
        }),
      });
      const data = await jsonOrError(response);
      setResult({ ok: true, message: `${data.status}: ${data.steps?.length ?? 0} 个步骤验证通过。` });
      await refresh();
    } catch (error) {
      setResult({ ok: false, message: error instanceof Error ? error.message : "预演失败" });
    } finally {
      setBusy(false);
    }
  }

  const summary = connectorReport?.summary ?? {};
  const execution = (metrics?.connector_executions as UnknownRecord | undefined) ?? {};
  const slo = (metrics?.slo as UnknownRecord | undefined) ?? {};
  const checks = readiness?.checks ?? [];
  const requiredPass = Math.round((readiness?.required_pass_rate ?? 0) * 100);
  const productionStatus = readiness?.production_ready ? "READY" : "BLOCKED";
  const healthStatus = text(health?.status).toUpperCase();
  const runtimeMode = text(runtime?.mode).toUpperCase();
  const secretVariables = useMemo(() => {
    if (form.authType === "api_key") return ["SAP_API_KEY"];
    if (form.authType === "basic") return ["SAP_USERNAME", "SAP_PASSWORD"];
    if (form.authType === "bearer") return ["SAP_BEARER_TOKEN"];
    if (form.authType === "principal_propagation") return ["X-SAP-Principal-Token"];
    if (form.authType === "oauth2_client_credentials") return ["SAP_TOKEN_URL", "SAP_CLIENT_ID", "SAP_CLIENT_SECRET"];
    return [];
  }, [form.authType]);

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="icon">
              <Link href="/admin/business-data" aria-label="返回业务数据">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-bold text-slate-950">ERP Connector Control Plane</h1>
              <p className="text-sm text-slate-500">连接配置、执行保护、发布门禁与业务 SLO</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => void runShadowSaga()} disabled={busy}>
              <Play className="mr-2 h-4 w-4" />
              Shadow 验证
            </Button>
            <Button variant="outline" size="icon" onClick={() => void refresh()} disabled={busy} title="刷新">
              <RefreshCcw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        {result && (
          <div className={`mb-4 flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${result.ok ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"}`}>
            {result.ok ? <CheckCircle2 className="h-4 w-4" /> : <CircleAlert className="h-4 w-4" />}
            {result.message}
          </div>
        )}

        <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <MetricCard icon={Cable} label="Connectors" value={text(summary.connector_count)} hint="registered systems" />
          <MetricCard icon={RadioTower} label="Runtime" value={runtimeMode} hint={connectorId} />
          <MetricCard icon={Activity} label="Health" value={healthStatus} hint={`${text(health?.latency_ms)} ms`} toneValue={healthStatus} />
          <MetricCard icon={ShieldCheck} label="Release Gates" value={`${requiredPass}%`} hint={productionStatus} toneValue={productionStatus === "READY" ? "PASS" : "BLOCKED"} />
          <MetricCard icon={Clock3} label="Success Rate" value={`${Math.round(numberValue(execution.success_rate) * 100)}%`} hint={`SLO ${text(slo.met)}`} toneValue={slo.met ? "PASS" : "FAIL"} />
        </div>

        <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Cable className="h-4 w-4 text-blue-600" />
                Connector Configuration
              </CardTitle>
              <select
                value={connectorId}
                onChange={(event) => setConnectorId(event.target.value)}
                className="h-9 rounded-md border border-slate-200 bg-white px-3 text-sm"
              >
                <option value={DEFAULT_CONNECTOR_ID}>{DEFAULT_CONNECTOR_ID}</option>
                <option value="CONN-MOCK-ERP">CONN-MOCK-ERP</option>
              </select>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="Name" value={form.name} onChange={(value) => setForm({ ...form, name: value })} />
                <Field label="System Type" value={form.systemType} onChange={(value) => setForm({ ...form, systemType: value })} />
              </div>
              <Field label="Base URL" value={form.baseUrl} onChange={(value) => setForm({ ...form, baseUrl: value })} />
              <div className="grid gap-3 md:grid-cols-3">
                <SelectField label="Mode" value={form.mode} values={["mock", "live"]} onChange={(value) => setForm({ ...form, mode: value })} />
                <SelectField label="Authentication" value={form.authType} values={["api_key", "oauth2_client_credentials", "principal_propagation", "bearer", "basic", "none"]} onChange={(value) => setForm({ ...form, authType: value })} />
                <SelectField label="Status" value={form.status} values={["DRAFT", "ACTIVE", "PAUSED", "ERROR"]} onChange={(value) => setForm({ ...form, status: value })} />
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <Toggle label="Read only" checked={form.readOnly} onChange={(checked) => setForm({ ...form, readOnly: checked })} />
                <Toggle label="Shadow writes" checked={form.shadowWrites} onChange={(checked) => setForm({ ...form, shadowWrites: checked })} />
                <Toggle label="Verify TLS" checked={form.verifyTls} onChange={(checked) => setForm({ ...form, verifyTls: checked })} />
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <NumberField label="Timeout (seconds)" value={form.timeoutSeconds} onChange={(value) => setForm({ ...form, timeoutSeconds: value })} />
                <NumberField label="Max retries" value={form.maxRetries} onChange={(value) => setForm({ ...form, maxRetries: value })} />
                <Field label="Change ticket" value={form.changeTicket} onChange={(value) => setForm({ ...form, changeTicket: value })} placeholder="Required for live writes" />
              </div>
              <label className="block space-y-1.5">
                <span className="text-xs font-medium text-slate-600">Operation Paths</span>
                <textarea
                  value={form.operationPaths}
                  onChange={(event) => setForm({ ...form, operationPaths: event.target.value })}
                  rows={9}
                  spellCheck={false}
                  className="w-full resize-y rounded-md border border-slate-200 bg-slate-950 px-3 py-2 font-mono text-xs leading-5 text-slate-100 outline-none focus:border-blue-400"
                />
              </label>
              <div className="flex justify-end">
                <Button onClick={() => void saveConfig()} disabled={busy}>
                  <Save className="mr-2 h-4 w-4" />
                  Save Configuration
                </Button>
              </div>
            </CardContent>
          </Card>

          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <KeyRound className="h-4 w-4 text-blue-600" />
                  Secret Boundary
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {secretVariables.length ? secretVariables.map((variable) => (
                  <div key={variable} className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
                    <code className="text-xs font-semibold text-slate-800">{variable}</code>
                    <Badge variant="outline">environment only</Badge>
                  </div>
                )) : <div className="text-sm text-slate-500">No credential variables required.</div>}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <ShieldCheck className="h-4 w-4 text-blue-600" />
                  Release Gates
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {checks.map((check) => (
                  <div key={check.id} className="flex items-start justify-between gap-3 border-b border-slate-100 py-2 last:border-0">
                    <div>
                      <div className="font-mono text-xs font-semibold text-slate-900">{check.id}</div>
                      <div className="mt-1 text-xs text-slate-500">{check.description}</div>
                    </div>
                    <Badge className={tone(check.status)}>{check.status}</Badge>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <RecordList title="Outbox Events" icon={GitBranch} rows={connectorReport?.outbox_events ?? []} idField="outbox_event_id" statusField="status" detailField="event_type" />
          <RecordList title={`Exceptions (${text(exceptionReport?.summary?.open_count)} open)`} icon={DatabaseZap} rows={[...(exceptionReport?.data_quality_issues ?? []), ...(exceptionReport?.reconciliation_issues ?? [])]} idField="issue_id" fallbackIdField="reconciliation_issue_id" statusField="severity" detailField="description" />
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <RecordList title="Webhook Subscriptions" icon={BellRing} rows={connectorReport?.webhooks ?? []} idField="subscription_id" statusField="active" detailField="event_type" />
          <JsonPanel title="ERP Business Metrics" data={metrics} />
        </div>
      </main>
    </div>
  );
}

function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-blue-400" />
    </label>
  );
}

function NumberField({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <input type="number" min={0} value={value} onChange={(event) => onChange(Number(event.target.value))} className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-blue-400" />
    </label>
  );
}

function SelectField({ label, value, values, onChange }: { label: string; value: string; values: string[]; onChange: (value: string) => void }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-slate-600">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm outline-none focus:border-blue-400">
        {values.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>
    </label>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex h-10 cursor-pointer items-center justify-between rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-700">
      {label}
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 accent-blue-600" />
    </label>
  );
}

function MetricCard({ icon: Icon, label, value, hint, toneValue }: { icon: ElementType; label: string; value: string; hint: string; toneValue?: unknown }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-slate-600">{label}</CardTitle>
        <Icon className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2 text-xl font-bold text-slate-950">
          {value}
          {toneValue ? <span className={`h-2 w-2 rounded-full ${tone(toneValue).includes("emerald") ? "bg-emerald-500" : tone(toneValue).includes("red") ? "bg-red-500" : "bg-amber-500"}`} /> : null}
        </div>
        <p className="mt-1 truncate text-xs text-slate-500">{hint}</p>
      </CardContent>
    </Card>
  );
}

function RecordList({ title, icon: Icon, rows, idField, fallbackIdField, statusField, detailField }: { title: string; icon: ElementType; rows: UnknownRecord[]; idField: string; fallbackIdField?: string; statusField: string; detailField: string }) {
  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Icon className="h-4 w-4 text-blue-600" />{title}</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        {rows.length === 0 ? <div className="text-sm text-slate-500">No records</div> : rows.slice(0, 8).map((item, index) => {
          const id = text(item[idField] ?? (fallbackIdField ? item[fallbackIdField] : undefined) ?? index);
          return (
            <div key={id} className="rounded-md border border-slate-200 bg-white px-3 py-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-mono text-xs font-semibold text-slate-900">{id}</div>
                <Badge className={tone(item[statusField])}>{text(item[statusField] ?? item.status)}</Badge>
              </div>
              <div className="mt-1 text-xs text-slate-500">{text(item[detailField] ?? item.mismatch_type)}</div>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

function JsonPanel({ title, data }: { title: string; data: unknown }) {
  return (
    <Card>
      <CardHeader><CardTitle className="text-base">{title}</CardTitle></CardHeader>
      <CardContent><pre className="max-h-80 overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">{data ? JSON.stringify(data, null, 2) : "loading"}</pre></CardContent>
    </Card>
  );
}
