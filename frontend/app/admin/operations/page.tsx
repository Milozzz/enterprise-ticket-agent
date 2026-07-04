"use client";

import { useCallback, useEffect, useState, type ElementType } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowLeft,
  Check,
  CircleAlert,
  ClipboardCheck,
  Gauge,
  RefreshCcw,
  ServerCog,
  ShieldCheck,
  WalletCards,
} from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Snapshot {
  tenant_id: string;
  generated_at: string;
  onboarding: {
    status: string;
    environment: string | null;
    checklist: Record<string, boolean>;
  };
  subscription: {
    plan: string | null;
    status: string | null;
    quota: number;
    consumed: number;
    remaining: number;
    usage_percent: number;
    by_metric: Record<string, number>;
  };
  operations: {
    sagas: Record<string, number>;
    a2a_tasks: Record<string, number>;
    outbox: Record<string, number>;
    evidence_items: number;
    compensations: number;
  };
  business_kpis: {
    automated_finance_transactions: number;
    manual_review_cases: number;
    failed_transactions: number;
    workflow_success_rate: number;
  };
  slos: Array<{ metric: string; target: string; actual: number; met: boolean }>;
}

const CHECKLIST_LABELS: Record<string, string> = {
  identity_configured: "企业身份与角色映射",
  connector_configured: "SAP Connector 配置",
  connector_health_passed: "连接健康检查",
  policy_published: "生产 Policy 发布",
  approval_roles_mapped: "审批职责映射",
  eval_gate_passed: "场景 Eval 质量门禁",
  shadow_write_verified: "Shadow Write 验证",
  evidence_export_verified: "审计证据导出验证",
  production_change_approved: "生产变更审批",
};

async function jsonOrError(response: Response) {
  const body = await response.json();
  if (!response.ok) throw new Error(body?.detail || `HTTP ${response.status}`);
  return body;
}

function count(values: Record<string, number> | undefined) {
  return Object.values(values ?? {}).reduce((sum, value) => sum + value, 0);
}

export default function CommercialOperationsPage() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [plan, setPlan] = useState("PILOT");

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const data = await fetch("/api/commercial/operations/snapshot", {
        cache: "no-store",
      }).then(jsonOrError);
      setSnapshot(data);
      if (data.subscription?.plan) setPlan(data.subscription.plan);
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "运营数据加载失败");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function provision() {
    setBusy(true);
    try {
      await fetch("/api/commercial/onboarding/provision", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Enterprise Agent Demo",
          industry: "ecommerce",
          region: "CN",
          plan_code: "PILOT",
          monthly_action_quota: 1000,
        }),
      }).then(jsonOrError);
      setMessage("租户运营底账已初始化");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "初始化失败");
    } finally {
      setBusy(false);
    }
  }

  async function verifyChecklistItem(key: string) {
    setBusy(true);
    try {
      await fetch("/api/commercial/onboarding", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ checklist: { [key]: true } }),
      }).then(jsonOrError);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "更新失败");
    } finally {
      setBusy(false);
    }
  }

  async function savePlan() {
    setBusy(true);
    try {
      await fetch("/api/commercial/subscription", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plan_code: plan,
          monthly_action_quota: plan === "ENTERPRISE" ? 100000 : plan === "TEAM" ? 10000 : 1000,
          status: "ACTIVE",
        }),
      }).then(jsonOrError);
      setMessage("套餐与动作配额已更新");
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "套餐更新失败");
    } finally {
      setBusy(false);
    }
  }

  const initialized = snapshot?.subscription.plan !== null;

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="icon">
              <Link href="/admin/scenarios" aria-label="返回场景后台">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div>
              <h1 className="text-xl font-bold text-slate-950">Commercial Operations</h1>
              <p className="text-sm text-slate-500">租户开通、动作配额、SLA 与业务成效</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {!initialized && (
              <Button onClick={provision} disabled={busy}>
                <ServerCog className="mr-2 h-4 w-4" />
                初始化租户
              </Button>
            )}
            <Button variant="outline" size="icon" onClick={() => void refresh()} disabled={busy} title="刷新">
              <RefreshCcw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
            </Button>
          </div>
        </div>

        {message && (
          <div className="mb-4 flex items-center gap-2 border-l-4 border-blue-500 bg-blue-50 px-4 py-3 text-sm text-blue-800">
            <CircleAlert className="h-4 w-4" />
            {message}
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Metric icon={ShieldCheck} label="Onboarding" value={snapshot?.onboarding.status ?? "-"} />
          <Metric icon={WalletCards} label="Plan" value={snapshot?.subscription.plan ?? "未开通"} />
          <Metric icon={Activity} label="Workflow Success" value={`${snapshot?.business_kpis.workflow_success_rate ?? 0}%`} />
          <Metric icon={ClipboardCheck} label="Evidence" value={String(snapshot?.operations.evidence_items ?? 0)} />
        </div>

        <section className="mt-5 grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">生产开通清单</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {Object.entries(snapshot?.onboarding.checklist ?? CHECKLIST_LABELS).map(([key, raw]) => {
                const verified = typeof raw === "boolean" ? raw : false;
                return (
                  <div key={key} className="flex min-h-11 items-center justify-between gap-3 border-b border-slate-100 py-2 last:border-0">
                    <div className="flex items-center gap-2 text-sm text-slate-700">
                      <span className={`flex h-5 w-5 items-center justify-center rounded-full ${verified ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-400"}`}>
                        <Check className="h-3.5 w-3.5" />
                      </span>
                      {CHECKLIST_LABELS[key] ?? key}
                    </div>
                    {verified ? (
                      <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700">已验证</Badge>
                    ) : (
                      <Button size="sm" variant="outline" disabled={!initialized || busy} onClick={() => void verifyChecklistItem(key)}>
                        标记验证
                      </Button>
                    )}
                  </div>
                );
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">套餐与动作配额</CardTitle>
            </CardHeader>
            <CardContent className="space-y-5">
              <div>
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="text-slate-600">本周期使用量</span>
                  <span className="font-semibold text-slate-900">
                    {snapshot?.subscription.consumed ?? 0} / {snapshot?.subscription.quota ?? 0}
                  </span>
                </div>
                <Progress value={Math.min(100, snapshot?.subscription.usage_percent ?? 0)} />
                <p className="mt-2 text-xs text-slate-500">剩余 {snapshot?.subscription.remaining ?? 0} actions</p>
              </div>
              <div className="grid grid-cols-[1fr_auto] gap-2">
                <Select value={plan} onValueChange={setPlan}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="PILOT">Pilot · 1,000</SelectItem>
                    <SelectItem value="TEAM">Team · 10,000</SelectItem>
                    <SelectItem value="ENTERPRISE">Enterprise · 100,000</SelectItem>
                  </SelectContent>
                </Select>
                <Button onClick={savePlan} disabled={!initialized || busy}>应用</Button>
              </div>
              <div className="space-y-2">
                {Object.entries(snapshot?.subscription.by_metric ?? {}).map(([metric, value]) => (
                  <div key={metric} className="flex justify-between text-sm">
                    <span className="font-mono text-xs text-slate-600">{metric}</span>
                    <span className="font-semibold text-slate-900">{value}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </section>

        <section className="mt-5">
          <div className="mb-3 flex items-center gap-2">
            <Gauge className="h-4 w-4 text-blue-600" />
            <h2 className="font-semibold text-slate-900">Service Level Objectives</h2>
          </div>
          <div className="overflow-hidden border border-slate-200 bg-white">
            <div className="grid grid-cols-[1.4fr_1fr_1fr_90px] border-b bg-slate-50 px-4 py-2 text-xs font-semibold uppercase text-slate-500">
              <span>Metric</span><span>Target</span><span>Actual</span><span>Status</span>
            </div>
            {(snapshot?.slos ?? []).map((slo) => (
              <div key={slo.metric} className="grid min-h-12 grid-cols-[1.4fr_1fr_1fr_90px] items-center border-b px-4 text-sm last:border-0">
                <span className="font-mono text-xs text-slate-700">{slo.metric}</span>
                <span className="text-slate-600">{slo.target}</span>
                <span className="font-semibold text-slate-900">{slo.actual}</span>
                <Badge className={slo.met ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-red-200 bg-red-50 text-red-700"}>
                  {slo.met ? "达标" : "未达标"}
                </Badge>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-5 grid gap-4 md:grid-cols-3">
          <Operations title="Saga" values={snapshot?.operations.sagas} />
          <Operations title="A2A Tasks" values={snapshot?.operations.a2a_tasks} />
          <Operations title="Outbox" values={snapshot?.operations.outbox} />
        </section>
      </main>
    </div>
  );
}

function Metric({ icon: Icon, label, value }: { icon: ElementType; label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-slate-600">{label}</CardTitle>
        <Icon className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardContent><div className="text-2xl font-bold text-slate-950">{value}</div></CardContent>
    </Card>
  );
}

function Operations({ title, values }: { title: string; values?: Record<string, number> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          {title}<Badge variant="outline">{count(values)}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {Object.entries(values ?? {}).map(([key, value]) => (
          <div key={key} className="flex justify-between text-sm">
            <span className="text-slate-600">{key}</span>
            <span className="font-semibold text-slate-900">{value}</span>
          </div>
        ))}
        {!Object.keys(values ?? {}).length && <p className="text-sm text-slate-400">暂无运行数据</p>}
      </CardContent>
    </Card>
  );
}
