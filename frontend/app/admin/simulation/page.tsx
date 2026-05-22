"use client";

import { useEffect, useState, type ElementType } from "react";
import Link from "next/link";
import { ArrowLeft, FlaskConical, GitBranch, Loader2, Play, ShieldCheck } from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";

interface ScenarioSummary {
  id: string;
  name: string;
  workflow: string;
  status: string;
}

async function readJsonOrThrow(res: Response, label: string) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail =
      typeof data === "object" && data && "detail" in data
        ? String((data as { detail?: unknown }).detail)
        : res.statusText;
    throw new Error(`${label} failed (${res.status}): ${detail}`);
  }
  return data as Record<string, unknown>;
}

export default function SimulationLabPage() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioId, setScenarioId] = useState("permission_request");
  const [message, setMessage] = useState("permission request GitHub admin access for production release");
  const [loading, setLoading] = useState(false);
  const [runtimeResult, setRuntimeResult] = useState<Record<string, unknown> | null>(null);
  const [routeResult, setRouteResult] = useState<Record<string, unknown> | null>(null);
  const [evalResult, setEvalResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch("/api/admin/scenarios", { cache: "no-store" })
      .then((res) => res.json())
      .then((data) => {
        setScenarios(data.scenarios ?? []);
        if (data.scenarios?.[0]?.id) setScenarioId(data.scenarios[0].id);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "加载场景失败"));
  }, []);

  const runSimulation = async () => {
    setLoading(true);
    setError("");
    setRuntimeResult(null);
    setRouteResult(null);
    setEvalResult(null);
    try {
      const [routeRes, runtimeRes, evalRes] = await Promise.all([
        fetch("/api/admin/scenarios/simulate-route", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
        }),
        fetch(`/api/admin/scenarios/${scenarioId}/simulate-runtime`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message, user_id: "sim-user", user_role: "USER", dry_run: true }),
        }),
        fetch(`/api/admin/scenarios/${scenarioId}/eval`, { method: "POST" }),
      ]);
      setRouteResult(await readJsonOrThrow(routeRes, "Route simulation"));
      setRuntimeResult(await readJsonOrThrow(runtimeRes, "Runtime dry-run"));
      setEvalResult(await readJsonOrThrow(evalRes, "Scenario eval"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "模拟失败");
    } finally {
      setLoading(false);
    }
  };

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
              <h1 className="text-xl font-bold text-slate-950">Simulation Lab</h1>
              <p className="text-sm text-slate-500">路由、运行时 dry-run、场景级 eval 的完整链路预演</p>
            </div>
          </div>
          <Button onClick={runSimulation} disabled={loading}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
            运行模拟
          </Button>
        </div>

        {error ? <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div> : null}

        <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">输入</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <label className="block text-sm font-medium text-slate-700">
                场景
                <select
                  value={scenarioId}
                  onChange={(event) => setScenarioId(event.target.value)}
                  className="mt-2 h-10 w-full rounded-md border border-slate-200 bg-white px-3 text-sm"
                >
                  {scenarios.map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>
                      {scenario.name} / {scenario.workflow}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm font-medium text-slate-700">
                测试话术
                <Textarea value={message} onChange={(event) => setMessage(event.target.value)} className="mt-2 min-h-32" />
              </label>
              <div className="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-700">
                dry-run 会执行抽槽、策略、Tool Gateway 和 UI 事件生成，但不会产生真实业务副作用。
              </div>
            </CardContent>
          </Card>

          <div className="grid gap-4 xl:grid-cols-3">
            <ResultPanel title="Supervisor 路由" icon={GitBranch} data={routeResult} />
            <ResultPanel title="Runtime Dry-run" icon={FlaskConical} data={runtimeResult} />
            <ResultPanel title="场景 Eval" icon={ShieldCheck} data={evalResult} />
          </div>
        </div>
      </main>
    </div>
  );
}

function ResultPanel({
  title,
  icon: Icon,
  data,
}: {
  title: string;
  icon: ElementType;
  data: Record<string, unknown> | null;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2 text-base">
          <Icon className="h-4 w-4 text-blue-600" />
          {title}
        </CardTitle>
        <Badge variant="outline">{data ? "ready" : "idle"}</Badge>
      </CardHeader>
      <CardContent>
        <pre className="max-h-[560px] overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">
          {data ? JSON.stringify(data, null, 2) : "尚未运行"}
        </pre>
      </CardContent>
    </Card>
  );
}
