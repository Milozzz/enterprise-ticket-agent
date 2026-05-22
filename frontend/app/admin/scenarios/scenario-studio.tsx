"use client";

import { useEffect, useMemo, useState, type ElementType } from "react";
import Link from "next/link";
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  Bot,
  Braces,
  CheckCircle2,
  CircleDot,
  ClipboardCheck,
  CopyPlus,
  FileCode2,
  GitBranch,
  KeyRound,
  Loader2,
  Play,
  RefreshCw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Workflow,
  Wrench,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Header } from "@/components/Header";
import { cn } from "@/lib/utils";

type ScenarioStatus = "active" | "draft" | "paused";

interface ApprovalStage {
  id: string;
  name: string;
  roles: string[];
  required: boolean;
}

interface ScenarioHitl {
  enabled: boolean;
  review_roles: string[];
  description: string;
  approval_chain: ApprovalStage[];
}

interface ScenarioConfig {
  id: string;
  name: string;
  description: string;
  workflow: string;
  status: ScenarioStatus;
  owner: string;
  business_domain: string;
  logic_module: string;
  logic_file: string;
  sla_minutes: number;
  tags: string[];
  intents: string[];
  keywords: string[];
  allowed_roles: string[];
  tools: string[];
  policies: string[];
  hitl: ScenarioHitl;
  runtime?: Record<string, unknown>;
}

interface ToolSpec {
  name: string;
  action: string;
  risk_level: string;
  side_effect: string;
  description: string;
}

interface WorkflowInfo {
  name: string;
  entrypoint: string;
}

interface PolicyInfo {
  name: string;
  rule_count: number;
  rules: Array<{ id?: string; description?: string; effect?: string }>;
}

interface AdminConfig {
  scenarios: ScenarioConfig[];
  tools: ToolSpec[];
  workflows: WorkflowInfo[];
  policies: PolicyInfo[];
  policy_version: string;
  roles: string[];
  status_options: ScenarioStatus[];
}

interface SimulationResult {
  scenario_id: string;
  workflow: string;
  confidence: number;
  matched_keywords: string[];
  reason: string;
  scenario: ScenarioConfig;
}

type StudioTab = "overview" | "routing" | "capabilities" | "governance" | "json";

const emptyConfig: AdminConfig = {
  scenarios: [],
  tools: [],
  workflows: [],
  policies: [],
  policy_version: "unknown",
  roles: ["USER", "AGENT", "MANAGER", "SECURITY", "FINANCE"],
  status_options: ["active", "draft", "paused"],
};

const tabItems: Array<{ id: StudioTab; label: string; icon: ElementType }> = [
  { id: "overview", label: "架构总览", icon: Workflow },
  { id: "routing", label: "路由配置", icon: SlidersHorizontal },
  { id: "capabilities", label: "能力治理", icon: ShieldCheck },
  { id: "governance", label: "发布与归属", icon: ClipboardCheck },
  { id: "json", label: "配置预览", icon: Braces },
];

function listToText(values: string[]) {
  return values.join("\n");
}

function textToList(value: string) {
  return value
    .split(/\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function statusStyle(status: ScenarioStatus) {
  if (status === "active") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (status === "paused") return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-slate-200 bg-slate-100 text-slate-700";
}

function riskStyle(risk: string) {
  if (risk === "high") return "border-red-200 bg-red-50 text-red-700";
  if (risk === "medium") return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-emerald-200 bg-emerald-50 text-emerald-700";
}

function defaultApprovalChain(reviewRoles: string[]): ApprovalStage[] {
  const roles = reviewRoles.length ? reviewRoles : ["MANAGER"];
  return [
    {
      id: "manager_review",
      name: "Manager review",
      roles,
      required: true,
    },
  ];
}

function cloneScenario(source?: ScenarioConfig): ScenarioConfig {
  const seed = Date.now().toString(36);
  return {
    id: `custom_scenario_${seed}`,
    name: "New Enterprise Scenario",
    description: "Describe the business scenario, approval boundary, and tool contract.",
    workflow: source?.workflow ?? "permission_request_workflow",
    status: "draft",
    owner: source?.owner ?? "Platform",
    business_domain: source?.business_domain ?? "General",
    logic_module: source?.logic_module ?? "app.agent.nodes.permission_request.permission_request_node",
    logic_file: source?.logic_file ?? "backend/app/agent/nodes/permission_request.py",
    sla_minutes: source?.sla_minutes ?? 60,
    tags: source?.tags ?? ["Draft"],
    intents: source?.intents ?? ["custom"],
    keywords: source?.keywords ?? ["新场景"],
    allowed_roles: source?.allowed_roles ?? ["USER", "AGENT", "MANAGER"],
    tools: source?.tools ?? [],
    policies: source?.policies ?? [],
    hitl: {
      enabled: source?.hitl.enabled ?? true,
      review_roles: source?.hitl.review_roles ?? ["MANAGER"],
      description: source?.hitl.description ?? "Configure who must review this scenario before side effects execute.",
      approval_chain: source?.hitl.approval_chain?.length
        ? structuredClone(source.hitl.approval_chain)
        : defaultApprovalChain(source?.hitl.review_roles ?? ["MANAGER"]),
    },
    runtime: source?.runtime ? structuredClone(source.runtime) : {},
  };
}

function MetricCard({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: string;
  hint: string;
  icon: ElementType;
}) {
  return (
    <Card className="border-slate-200/80 bg-white shadow-sm ring-1 ring-black/[0.03]">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium text-slate-600">{label}</CardTitle>
        <Icon className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold tracking-tight text-slate-950">{value}</div>
        <p className="mt-1 text-xs text-slate-500">{hint}</p>
      </CardContent>
    </Card>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</span>
      <div className="mt-1.5">{children}</div>
      {hint ? <p className="mt-1 text-xs leading-5 text-slate-500">{hint}</p> : null}
    </label>
  );
}

function TextInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        "h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-900 shadow-sm outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/15",
        props.className
      )}
    />
  );
}

function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cn(
        "min-h-24 w-full resize-y rounded-md border border-slate-200 bg-white px-3 py-2 text-sm leading-6 text-slate-900 shadow-sm outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/15",
        props.className
      )}
    />
  );
}

function TogglePill({
  active,
  children,
  onClick,
  tone = "slate",
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
  tone?: "slate" | "blue" | "green" | "amber";
}) {
  const activeStyle = {
    slate: "border-slate-300 bg-slate-900 text-white",
    blue: "border-blue-200 bg-blue-50 text-blue-700",
    green: "border-emerald-200 bg-emerald-50 text-emerald-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
  }[tone];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition hover:bg-slate-50",
        active ? activeStyle : "border-slate-200 bg-white text-slate-600"
      )}
    >
      {active ? <CheckCircle2 className="h-3.5 w-3.5" /> : <CircleDot className="h-3.5 w-3.5" />}
      {children}
    </button>
  );
}

export function ScenarioStudio() {
  const [config, setConfig] = useState<AdminConfig>(emptyConfig);
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<ScenarioConfig | null>(null);
  const [original, setOriginal] = useState<ScenarioConfig | null>(null);
  const [activeTab, setActiveTab] = useState<StudioTab>("overview");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [routeInput, setRouteInput] = useState("帮我申请 GitHub 管理员权限，用于发布配置");
  const [simulation, setSimulation] = useState<SimulationResult | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [isNew, setIsNew] = useState(false);

  const selected = draft;
  const dirty = useMemo(() => {
    if (!selected || !original) return false;
    return JSON.stringify(selected) !== JSON.stringify(original);
  }, [selected, original]);

  const activeScenarios = config.scenarios.filter((scenario) => scenario.status === "active").length;
  const selectedTools = selected
    ? config.tools.filter((tool) => selected.tools.includes(tool.name))
    : [];
  const selectedPolicies = selected
    ? config.policies.filter((policy) => selected.policies.includes(policy.name))
    : [];

  const loadConfig = async () => {
    setLoading(true);
    setMessage("");
    try {
      const res = await fetch("/api/admin/scenarios", { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as AdminConfig;
      setConfig(data);
      const nextSelected =
        data.scenarios.find((scenario) => scenario.id === selectedId) ?? data.scenarios[0] ?? null;
      if (nextSelected) {
        setSelectedId(nextSelected.id);
        setDraft(structuredClone(nextSelected));
        setOriginal(structuredClone(nextSelected));
        setIsNew(false);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载配置失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectScenario = (scenario: ScenarioConfig) => {
    setSelectedId(scenario.id);
    setDraft(structuredClone(scenario));
    setOriginal(structuredClone(scenario));
    setIsNew(false);
    setMessage("");
  };

  const patchDraft = <K extends keyof ScenarioConfig>(key: K, value: ScenarioConfig[K]) => {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  };

  const patchHitl = <K extends keyof ScenarioHitl>(key: K, value: ScenarioHitl[K]) => {
    setDraft((current) =>
      current ? { ...current, hitl: { ...current.hitl, [key]: value } } : current
    );
  };

  const patchApprovalStage = <K extends keyof ApprovalStage>(
    index: number,
    key: K,
    value: ApprovalStage[K]
  ) => {
    setDraft((current) => {
      if (!current) return current;
      const approvalChain = [...current.hitl.approval_chain];
      approvalChain[index] = { ...approvalChain[index], [key]: value };
      return { ...current, hitl: { ...current.hitl, approval_chain: approvalChain } };
    });
  };

  const addApprovalStage = () => {
    setDraft((current) => {
      if (!current) return current;
      const nextIndex = current.hitl.approval_chain.length + 1;
      const approvalChain = [
        ...current.hitl.approval_chain,
        {
          id: `stage_${nextIndex}`,
          name: `Approval stage ${nextIndex}`,
          roles: ["MANAGER"],
          required: true,
        },
      ];
      return { ...current, hitl: { ...current.hitl, approval_chain: approvalChain } };
    });
  };

  const removeApprovalStage = (index: number) => {
    setDraft((current) => {
      if (!current) return current;
      const approvalChain = current.hitl.approval_chain.filter((_, itemIndex) => itemIndex !== index);
      return { ...current, hitl: { ...current.hitl, approval_chain: approvalChain } };
    });
  };

  const toggleApprovalStageRole = (index: number, role: string) => {
    setDraft((current) => {
      if (!current) return current;
      const approvalChain = [...current.hitl.approval_chain];
      const stage = approvalChain[index];
      const roles = stage.roles.includes(role)
        ? stage.roles.filter((item) => item !== role)
        : [...stage.roles, role];
      approvalChain[index] = { ...stage, roles };
      return { ...current, hitl: { ...current.hitl, approval_chain: approvalChain } };
    });
  };

  const toggleListValue = (key: "tools" | "policies" | "allowed_roles", value: string) => {
    setDraft((current) => {
      if (!current) return current;
      const next = current[key].includes(value)
        ? current[key].filter((item) => item !== value)
        : [...current[key], value];
      return { ...current, [key]: next };
    });
  };

  const saveScenario = async () => {
    if (!draft) return;
    setSaving(true);
    setMessage("");
    try {
      const res = await fetch(isNew ? "/api/admin/scenarios" : `/api/admin/scenarios/${draft.id}`, {
        method: isNew ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.detail || "保存失败");
      const saved = payload.scenario as ScenarioConfig;
      setConfig((current) => {
        const exists = current.scenarios.some((scenario) => scenario.id === saved.id);
        return {
          ...current,
          scenarios: exists
            ? current.scenarios.map((scenario) => (scenario.id === saved.id ? saved : scenario))
            : [...current.scenarios, saved],
        };
      });
      setSelectedId(saved.id);
      setDraft(structuredClone(saved));
      setOriginal(structuredClone(saved));
      setIsNew(false);
      setMessage("配置已保存，Supervisor Registry 已刷新。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const startNewScenario = () => {
    const next = cloneScenario(selected ?? config.scenarios[0]);
    setSelectedId(next.id);
    setDraft(next);
    setOriginal({ ...next, id: "", name: "" });
    setIsNew(true);
    setActiveTab("overview");
    setMessage("已基于当前模板创建草稿，保存后会写入新的场景配置文件。");
  };

  const runSimulation = async () => {
    setSimulating(true);
    setMessage("");
    try {
      const res = await fetch("/api/admin/scenarios/simulate-route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: routeInput }),
      });
      if (!res.ok) throw new Error(await res.text());
      setSimulation((await res.json()) as SimulationResult);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "模拟路由失败");
    } finally {
      setSimulating(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <div className="border-b border-slate-200 bg-white/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <div className="flex items-center gap-3">
            <Button asChild variant="ghost" size="icon" className="shrink-0">
              <Link href="/" aria-label="返回对话">
                <ArrowLeft className="h-4 w-4" />
              </Link>
            </Button>
            <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-blue-100 bg-blue-50 text-blue-600">
              <Settings2 className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-slate-950">Scenario Studio</h1>
              <p className="text-xs text-slate-500">
                配置 Supervisor 路由、场景能力、策略治理和 HITL 审批边界
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
              Policy {config.policy_version}
            </Badge>
            <Button variant="outline" size="sm" onClick={loadConfig} disabled={loading}>
              <RefreshCw className={cn("mr-2 h-3.5 w-3.5", loading && "animate-spin")} />
              刷新
            </Button>
            <Button size="sm" onClick={saveScenario} disabled={!selected || saving || (!dirty && !isNew)}>
              {saving ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" /> : <Save className="mr-2 h-3.5 w-3.5" />}
              保存配置
            </Button>
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
        {message ? (
          <div className="mb-4 flex items-start gap-2 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{message}</span>
          </div>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-4">
          <MetricCard
            label="场景资产"
            value={String(config.scenarios.length)}
            hint={`${activeScenarios} 个 active，${config.scenarios.length - activeScenarios} 个非生产状态`}
            icon={Sparkles}
          />
          <MetricCard
            label="工具目录"
            value={String(config.tools.length)}
            hint="由 Tool Gateway 统一治理权限、风险和副作用"
            icon={Wrench}
          />
          <MetricCard
            label="策略集"
            value={String(config.policies.length)}
            hint="Policy-as-Code 控制审批与自动化边界"
            icon={ShieldCheck}
          />
          <MetricCard
            label="工作流入口"
            value={String(config.workflows.length)}
            hint="Supervisor 根据场景配置选择 LangGraph 入口"
            icon={GitBranch}
          />
        </div>

        <div className="mt-6 grid min-h-[720px] gap-5 xl:grid-cols-[320px_minmax(0,1fr)_360px]">
          <aside className="rounded-xl border border-slate-200 bg-white shadow-sm ring-1 ring-black/[0.03]">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">场景目录</h2>
                <p className="text-xs text-slate-500">Supervisor 可路由的业务场景</p>
              </div>
              <Button variant="outline" size="icon-sm" onClick={startNewScenario}>
                <CopyPlus className="h-3.5 w-3.5" />
              </Button>
            </div>
            <div className="space-y-2 p-3">
              {loading ? (
                <div className="flex items-center justify-center py-12 text-sm text-slate-500">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  加载配置
                </div>
              ) : (
                config.scenarios.map((scenario) => (
                  <button
                    key={scenario.id}
                    onClick={() => selectScenario(scenario)}
                    className={cn(
                      "w-full rounded-lg border px-3 py-3 text-left transition hover:border-blue-200 hover:bg-blue-50/40",
                      selectedId === scenario.id
                        ? "border-blue-200 bg-blue-50 shadow-sm"
                        : "border-slate-100 bg-white"
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-medium text-slate-950">{scenario.name}</div>
                        <div className="mt-1 font-mono text-[11px] text-slate-500">{scenario.id}</div>
                      </div>
                      <Badge variant="outline" className={cn("text-[10px]", statusStyle(scenario.status))}>
                        {scenario.status}
                      </Badge>
                    </div>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-500">
                      {scenario.description}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {scenario.tags.slice(0, 3).map((tag) => (
                        <span key={tag} className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </button>
                ))
              )}
            </div>
          </aside>

          <section className="min-w-0 rounded-xl border border-slate-200 bg-white shadow-sm ring-1 ring-black/[0.03]">
            {selected ? (
              <>
                <div className="border-b border-slate-100 px-5 py-4">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-xl font-bold tracking-tight text-slate-950">{selected.name}</h2>
                        <Badge variant="outline" className={cn("text-xs", statusStyle(selected.status))}>
                          {selected.status}
                        </Badge>
                        {dirty || isNew ? (
                          <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                            未保存
                          </Badge>
                        ) : null}
                      </div>
                      <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">{selected.description}</p>
                    </div>
                    <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-right">
                      <div className="text-[11px] uppercase tracking-wide text-slate-500">Workflow</div>
                      <div className="mt-1 font-mono text-xs font-semibold text-slate-800">{selected.workflow}</div>
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    {tabItems.map((tab) => (
                      <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={cn(
                          "inline-flex h-8 items-center gap-2 rounded-full border px-3 text-xs font-medium transition",
                          activeTab === tab.id
                            ? "border-slate-900 bg-slate-900 text-white"
                            : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                        )}
                      >
                        <tab.icon className="h-3.5 w-3.5" />
                        {tab.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="p-5">
                  {activeTab === "overview" && (
                    <div className="space-y-5">
                      <div className="grid gap-4 2xl:grid-cols-2">
                        <Field label="场景名称">
                          <TextInput value={selected.name} onChange={(e) => patchDraft("name", e.target.value)} />
                        </Field>
                        <Field label="场景 ID" hint={isNew ? "保存后会成为 JSON 文件名和 Supervisor 场景标识。" : "已发布场景的 ID 建议保持稳定。"}>
                          <TextInput
                            value={selected.id}
                            disabled={!isNew}
                            onChange={(e) => patchDraft("id", e.target.value)}
                            className={!isNew ? "bg-slate-50 text-slate-500" : ""}
                          />
                        </Field>
                      </div>
                      <Field label="业务描述">
                        <TextArea
                          value={selected.description}
                          onChange={(e) => patchDraft("description", e.target.value)}
                        />
                      </Field>

                      <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                        <div className="mb-4 flex items-center justify-between gap-3">
                          <div>
                            <h3 className="text-sm font-semibold text-slate-900">Supervisor 编排视图</h3>
                            <p className="text-xs text-slate-500">配置决定路由和治理，业务执行仍由后端节点负责。</p>
                          </div>
                          <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
                            {selected.business_domain}
                          </Badge>
                        </div>
                        <div className="grid gap-3 md:grid-cols-4">
                          {[
                            { icon: Bot, label: "Supervisor", detail: "场景识别与路由" },
                            { icon: GitBranch, label: selected.workflow, detail: "LangGraph 工作流入口" },
                            { icon: Wrench, label: `${selected.tools.length} tools`, detail: "Tool Gateway 治理" },
                            { icon: ShieldCheck, label: selected.hitl.enabled ? "HITL enabled" : "Auto mode", detail: "策略与审批边界" },
                          ].map((node, index) => (
                            <div key={node.label} className="relative rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
                              <node.icon className="h-4 w-4 text-blue-600" />
                              <div className="mt-3 text-sm font-semibold text-slate-900">{node.label}</div>
                              <div className="mt-1 text-xs text-slate-500">{node.detail}</div>
                              {index < 3 ? (
                                <div className="absolute -right-3 top-1/2 hidden h-px w-3 bg-slate-300 md:block" />
                              ) : null}
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="grid gap-4 2xl:grid-cols-2">
                        <div className="rounded-xl border border-slate-200 p-4">
                          <div className="flex items-center gap-2">
                            <FileCode2 className="h-4 w-4 text-slate-500" />
                            <h3 className="text-sm font-semibold text-slate-900">业务逻辑入口</h3>
                          </div>
                          <div className="mt-4 space-y-3">
                            <Field label="Logic Module">
                              <TextInput
                                value={selected.logic_module}
                                onChange={(e) => patchDraft("logic_module", e.target.value)}
                              />
                            </Field>
                            <Field label="Logic File">
                              <TextInput
                                value={selected.logic_file}
                                onChange={(e) => patchDraft("logic_file", e.target.value)}
                              />
                            </Field>
                          </div>
                        </div>
                        <div className="rounded-xl border border-slate-200 p-4">
                          <div className="flex items-center gap-2">
                            <KeyRound className="h-4 w-4 text-slate-500" />
                            <h3 className="text-sm font-semibold text-slate-900">配置与代码边界</h3>
                          </div>
                          <div className="mt-4 space-y-3 text-sm leading-6 text-slate-600">
                            <p>页面配置：场景识别关键词、可用工具、策略集、审批角色、SLA 和发布状态。</p>
                            <p>后端逻辑：字段抽取、业务校验、DB 写入、外部系统调用，放在对应的 node/tool 文件中。</p>
                            <p>这样新增场景时既能低代码配置，又不会把高风险业务逻辑散落在前端。</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {activeTab === "routing" && (
                    <div className="grid gap-5 2xl:grid-cols-[minmax(0,1fr)_360px]">
                      <div className="space-y-4">
                        <Field label="Intents" hint="一个场景可包含多个用户意图，供评估和后续 LLM 分类扩展使用。">
                          <TextArea
                            value={listToText(selected.intents)}
                            onChange={(e) => patchDraft("intents", textToList(e.target.value))}
                          />
                        </Field>
                        <Field label="Supervisor Keywords" hint="当前 Supervisor 采用确定性关键词路由；后续可升级为 LLM classifier + 规则兜底。">
                          <TextArea
                            className="min-h-44"
                            value={listToText(selected.keywords)}
                            onChange={(e) => patchDraft("keywords", textToList(e.target.value))}
                          />
                        </Field>
                      </div>

                      <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                        <div className="flex items-center gap-2">
                          <Search className="h-4 w-4 text-blue-600" />
                          <h3 className="text-sm font-semibold text-slate-900">路由模拟器</h3>
                        </div>
                        <p className="mt-1 text-xs leading-5 text-slate-500">
                          用真实用户输入测试 Supervisor 会命中哪个场景。
                        </p>
                        <TextArea
                          className="mt-4 min-h-28 bg-white"
                          value={routeInput}
                          onChange={(e) => setRouteInput(e.target.value)}
                        />
                        <Button className="mt-3 w-full" onClick={runSimulation} disabled={simulating}>
                          {simulating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                          运行模拟
                        </Button>
                        {simulation ? (
                          <div className="mt-4 rounded-lg border border-blue-100 bg-white p-3">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-semibold text-slate-900">{simulation.scenario.name}</span>
                              <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">
                                {(simulation.confidence * 100).toFixed(0)}%
                              </Badge>
                            </div>
                            <div className="mt-2 font-mono text-xs text-slate-500">{simulation.workflow}</div>
                            <div className="mt-3 flex flex-wrap gap-1.5">
                              {simulation.matched_keywords.length ? (
                                simulation.matched_keywords.map((keyword) => (
                                  <span key={keyword} className="rounded-full bg-blue-50 px-2 py-1 text-[11px] text-blue-700">
                                    {keyword}
                                  </span>
                                ))
                              ) : (
                                <span className="text-xs text-slate-500">未命中关键词，使用默认场景。</span>
                              )}
                            </div>
                            <p className="mt-3 text-xs leading-5 text-slate-500">{simulation.reason}</p>
                          </div>
                        ) : null}
                      </div>
                    </div>
                  )}

                  {activeTab === "capabilities" && (
                    <div className="grid gap-5 2xl:grid-cols-2">
                      <div className="space-y-5">
                        <div>
                          <h3 className="text-sm font-semibold text-slate-900">Tool Gateway 能力授权</h3>
                          <p className="mt-1 text-xs text-slate-500">选择该场景允许调用的工具；实际执行仍会经过后端权限和风险校验。</p>
                          <div className="mt-3 grid gap-2">
                            {config.tools.map((tool) => (
                              <button
                                key={tool.name}
                                type="button"
                                onClick={() => toggleListValue("tools", tool.name)}
                                className={cn(
                                  "rounded-lg border p-3 text-left transition hover:border-blue-200 hover:bg-blue-50/30",
                                  selected.tools.includes(tool.name)
                                    ? "border-blue-200 bg-blue-50"
                                    : "border-slate-200 bg-white"
                                )}
                              >
                                <div className="flex items-start justify-between gap-3">
                                  <div>
                                    <div className="font-mono text-xs font-semibold text-slate-900">{tool.name}</div>
                                    <p className="mt-1 text-xs leading-5 text-slate-500">{tool.description}</p>
                                  </div>
                                  <Badge variant="outline" className={cn("text-[10px]", riskStyle(tool.risk_level))}>
                                    {tool.risk_level}
                                  </Badge>
                                </div>
                                <div className="mt-2 text-[11px] text-slate-500">
                                  action: {tool.action} · side effect: {tool.side_effect}
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>

                      <div className="space-y-5">
                        <div>
                          <h3 className="text-sm font-semibold text-slate-900">Policy-as-Code 策略集</h3>
                          <p className="mt-1 text-xs text-slate-500">选择该场景绑定的审批策略，规则内容来自后端 policy 文件。</p>
                          <div className="mt-3 grid gap-2">
                            {config.policies.map((policy) => (
                              <button
                                key={policy.name}
                                type="button"
                                onClick={() => toggleListValue("policies", policy.name)}
                                className={cn(
                                  "rounded-lg border p-3 text-left transition hover:border-emerald-200 hover:bg-emerald-50/30",
                                  selected.policies.includes(policy.name)
                                    ? "border-emerald-200 bg-emerald-50"
                                    : "border-slate-200 bg-white"
                                )}
                              >
                                <div className="flex items-center justify-between">
                                  <div className="font-mono text-xs font-semibold text-slate-900">{policy.name}</div>
                                  <Badge variant="outline" className="border-emerald-200 bg-white text-emerald-700">
                                    {policy.rule_count} rules
                                  </Badge>
                                </div>
                                <div className="mt-2 space-y-1">
                                  {policy.rules.slice(0, 2).map((rule) => (
                                    <div key={rule.id} className="text-[11px] leading-5 text-slate-500">
                                      {rule.id}
                                    </div>
                                  ))}
                                </div>
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="rounded-xl border border-slate-200 p-4">
                          <h3 className="text-sm font-semibold text-slate-900">角色与 HITL</h3>
                          <div className="mt-4 space-y-4">
                            <div>
                              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                                Allowed Roles
                              </div>
                              <div className="flex flex-wrap gap-2">
                                {config.roles.map((role) => (
                                  <TogglePill
                                    key={role}
                                    active={selected.allowed_roles.includes(role)}
                                    onClick={() => toggleListValue("allowed_roles", role)}
                                    tone="blue"
                                  >
                                    {role}
                                  </TogglePill>
                                ))}
                              </div>
                            </div>
                            <div>
                              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                                Review Roles
                              </div>
                              <div className="flex flex-wrap gap-2">
                                {config.roles.map((role) => (
                                  <TogglePill
                                    key={role}
                                    active={selected.hitl.review_roles.includes(role)}
                                    onClick={() => {
                                      const next = selected.hitl.review_roles.includes(role)
                                        ? selected.hitl.review_roles.filter((item) => item !== role)
                                        : [...selected.hitl.review_roles, role];
                                      patchHitl("review_roles", next);
                                    }}
                                    tone="amber"
                                  >
                                    {role}
                                  </TogglePill>
                                ))}
                              </div>
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                              <div className="flex items-center justify-between gap-3">
                                <div>
                                  <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                                    Approval Chain
                                  </div>
                                  <p className="mt-1 text-xs leading-5 text-slate-500">
                                    Configure ordered HITL stages. Each stage is sent as stageId during approval.
                                  </p>
                                </div>
                                <Button type="button" variant="outline" size="sm" onClick={addApprovalStage}>
                                  Add Stage
                                </Button>
                              </div>
                              <div className="mt-3 space-y-3">
                                {selected.hitl.approval_chain.length ? (
                                  selected.hitl.approval_chain.map((stage, index) => (
                                    <div key={`${stage.id}-${index}`} className="rounded-lg border border-slate-200 bg-white p-3">
                                      <div className="grid gap-3 md:grid-cols-2">
                                        <Field label="Stage ID">
                                          <TextInput
                                            value={stage.id}
                                            onChange={(e) => patchApprovalStage(index, "id", e.target.value)}
                                          />
                                        </Field>
                                        <Field label="Stage Name">
                                          <TextInput
                                            value={stage.name}
                                            onChange={(e) => patchApprovalStage(index, "name", e.target.value)}
                                          />
                                        </Field>
                                      </div>
                                      <div className="mt-3">
                                        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                                          Stage Roles
                                        </div>
                                        <div className="flex flex-wrap gap-2">
                                          {config.roles.map((role) => (
                                            <TogglePill
                                              key={role}
                                              active={stage.roles.includes(role)}
                                              onClick={() => toggleApprovalStageRole(index, role)}
                                              tone="amber"
                                            >
                                              {role}
                                            </TogglePill>
                                          ))}
                                        </div>
                                      </div>
                                      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                                        <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
                                          <input
                                            type="checkbox"
                                            checked={stage.required}
                                            onChange={(e) => patchApprovalStage(index, "required", e.target.checked)}
                                            className="h-4 w-4 accent-slate-900"
                                          />
                                          Required stage
                                        </label>
                                        <Button
                                          type="button"
                                          variant="outline"
                                          size="sm"
                                          onClick={() => removeApprovalStage(index)}
                                        >
                                          Remove
                                        </Button>
                                      </div>
                                    </div>
                                  ))
                                ) : (
                                  <div className="rounded-md border border-dashed border-slate-300 bg-white px-3 py-4 text-center text-xs text-slate-500">
                                    No approval stages configured. Add a stage or rely on scenario-level review roles.
                                  </div>
                                )}
                              </div>
                            </div>
                            <label className="flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                              <span className="text-sm font-medium text-slate-700">启用人工审批</span>
                              <input
                                type="checkbox"
                                checked={selected.hitl.enabled}
                                onChange={(e) => patchHitl("enabled", e.target.checked)}
                                className="h-4 w-4 accent-slate-900"
                              />
                            </label>
                            <Field label="HITL Description">
                              <TextArea
                                value={selected.hitl.description}
                                onChange={(e) => patchHitl("description", e.target.value)}
                              />
                            </Field>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {activeTab === "governance" && (
                    <div className="grid gap-5 2xl:grid-cols-2">
                      <div className="space-y-4">
                        <Field label="发布状态">
                          <div className="flex flex-wrap gap-2">
                            {config.status_options.map((status) => (
                              <TogglePill
                                key={status}
                                active={selected.status === status}
                                onClick={() => patchDraft("status", status)}
                                tone={status === "active" ? "green" : status === "paused" ? "amber" : "slate"}
                              >
                                {status}
                              </TogglePill>
                            ))}
                          </div>
                        </Field>
                        <div className="grid gap-4 md:grid-cols-2">
                          <Field label="Owner">
                            <TextInput value={selected.owner} onChange={(e) => patchDraft("owner", e.target.value)} />
                          </Field>
                          <Field label="Business Domain">
                            <TextInput
                              value={selected.business_domain}
                              onChange={(e) => patchDraft("business_domain", e.target.value)}
                            />
                          </Field>
                        </div>
                        <Field label="SLA Minutes">
                          <TextInput
                            type="number"
                            min={1}
                            max={10080}
                            value={selected.sla_minutes}
                            onChange={(e) => patchDraft("sla_minutes", Number(e.target.value || 1))}
                          />
                        </Field>
                        <Field label="Tags">
                          <TextArea
                            value={listToText(selected.tags)}
                            onChange={(e) => patchDraft("tags", textToList(e.target.value))}
                          />
                        </Field>
                      </div>

                      <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                        <h3 className="text-sm font-semibold text-slate-900">发布检查</h3>
                        <div className="mt-4 space-y-3">
                          {[
                            { ok: selected.keywords.length > 0, text: "至少配置一个 Supervisor 关键词" },
                            { ok: selected.workflow in Object.fromEntries(config.workflows.map((w) => [w.name, w.entrypoint])), text: "工作流入口已注册" },
                            { ok: selected.tools.length > 0, text: "至少绑定一个 Tool Gateway 工具" },
                            { ok: selected.policies.length > 0, text: "至少绑定一个策略集" },
                            { ok: !selected.hitl.enabled || selected.hitl.review_roles.length > 0, text: "HITL 已配置审批角色" },
                            {
                              ok:
                                !selected.hitl.enabled ||
                                selected.hitl.approval_chain.every((stage) => stage.id && stage.roles.length > 0),
                              text: "多级审批阶段均绑定 stageId 与角色",
                            },
                            { ok: Boolean(selected.logic_file), text: "业务逻辑文件已声明" },
                          ].map((item) => (
                            <div key={item.text} className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm">
                              {item.ok ? (
                                <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                              ) : (
                                <AlertCircle className="h-4 w-4 text-amber-600" />
                              )}
                              <span className={item.ok ? "text-slate-700" : "text-amber-800"}>{item.text}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}

                  {activeTab === "json" && (
                    <div className="grid gap-5 2xl:grid-cols-[minmax(0,1fr)_320px]">
                      <pre className="max-h-[640px] overflow-auto rounded-xl border border-slate-200 bg-slate-950 p-4 text-xs leading-6 text-slate-100">
                        {JSON.stringify(selected, null, 2)}
                      </pre>
                      <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
                        <h3 className="text-sm font-semibold text-slate-900">存储位置</h3>
                        <p className="mt-2 text-sm leading-6 text-slate-600">
                          保存后会写入后端场景配置目录，并立即刷新内存中的 Supervisor Registry。
                        </p>
                        <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3 font-mono text-xs text-slate-600">
                          backend/app/scenarios/{selected.id}.json
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="flex h-full min-h-[600px] items-center justify-center text-sm text-slate-500">
                暂无场景配置
              </div>
            )}
          </section>

          <aside className="space-y-5">
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm ring-1 ring-black/[0.03]">
              <div className="flex items-center gap-2">
                <Activity className="h-4 w-4 text-slate-500" />
                <h3 className="text-sm font-semibold text-slate-900">当前场景摘要</h3>
              </div>
              {selected ? (
                <div className="mt-4 space-y-3 text-sm">
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">Owner</span>
                    <span className="font-medium text-slate-800">{selected.owner}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">SLA</span>
                    <span className="font-medium text-slate-800">{selected.sla_minutes} min</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">Keywords</span>
                    <span className="font-medium text-slate-800">{selected.keywords.length}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">Tools</span>
                    <span className="font-medium text-slate-800">{selected.tools.length}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-slate-500">Policies</span>
                    <span className="font-medium text-slate-800">{selected.policies.length}</span>
                  </div>
                </div>
              ) : null}
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm ring-1 ring-black/[0.03]">
              <div className="flex items-center gap-2">
                <Wrench className="h-4 w-4 text-slate-500" />
                <h3 className="text-sm font-semibold text-slate-900">已绑定工具</h3>
              </div>
              <div className="mt-4 space-y-2">
                {selectedTools.length ? (
                  selectedTools.map((tool) => (
                    <div key={tool.name} className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-xs font-semibold text-slate-800">{tool.name}</span>
                        <Badge variant="outline" className={cn("text-[10px]", riskStyle(tool.risk_level))}>
                          {tool.risk_level}
                        </Badge>
                      </div>
                      <div className="mt-1 text-[11px] text-slate-500">{tool.side_effect}</div>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-slate-500">未绑定工具</p>
                )}
              </div>
            </div>

            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm ring-1 ring-black/[0.03]">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-slate-500" />
                <h3 className="text-sm font-semibold text-slate-900">策略摘要</h3>
              </div>
              <div className="mt-4 space-y-2">
                {selectedPolicies.length ? (
                  selectedPolicies.map((policy) => (
                    <div key={policy.name} className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                      <div className="font-mono text-xs font-semibold text-slate-800">{policy.name}</div>
                      <div className="mt-1 text-[11px] text-slate-500">{policy.rule_count} rules</div>
                    </div>
                  ))
                ) : (
                  <p className="text-sm text-slate-500">未绑定策略</p>
                )}
              </div>
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
}
