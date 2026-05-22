"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Boxes, Network, RotateCcw, Wrench } from "lucide-react";

import { Header } from "@/components/Header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function PlatformCapabilitiesPage() {
  const [mcpTools, setMcpTools] = useState<unknown[]>([]);
  const [saga, setSaga] = useState<Record<string, unknown> | null>(null);
  const [templates, setTemplates] = useState<unknown[]>([]);

  useEffect(() => {
    Promise.all([
      fetch("/api/admin/tools/mcp").then((res) => res.json()),
      fetch("/api/admin/saga/templates/refund").then((res) => res.json()),
      fetch("/api/admin/scenarios/templates").then((res) => res.json()),
    ]).then(([mcp, sagaTemplate, templateCatalog]) => {
      setMcpTools(mcp.tools ?? []);
      setSaga(sagaTemplate);
      setTemplates(templateCatalog.templates ?? []);
    });
  }, []);

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="mb-5 flex items-center gap-3">
          <Button asChild variant="ghost" size="icon">
            <Link href="/admin/scenarios" aria-label="返回场景配置">
              <ArrowLeft className="h-4 w-4" />
            </Link>
          </Button>
          <div>
            <h1 className="text-xl font-bold text-slate-950">Platform Capabilities</h1>
            <p className="text-sm text-slate-500">MCP-compatible tools、Saga 补偿事务和场景模板市场</p>
          </div>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Network className="h-4 w-4 text-blue-600" />
                MCP-compatible Tool Adapter
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {mcpTools.map((tool, index) => {
                const item = tool as { name?: string; annotations?: { destructiveHint?: boolean }; "x-tool-gateway"?: { riskLevel?: string; sideEffect?: string } };
                return (
                  <div key={item.name || index} className="rounded-md border border-slate-200 bg-white px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs font-semibold text-slate-800">{item.name}</span>
                      <Badge variant="outline">{item["x-tool-gateway"]?.riskLevel}</Badge>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">side effect: {item["x-tool-gateway"]?.sideEffect}</div>
                  </div>
                );
              })}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <RotateCcw className="h-4 w-4 text-blue-600" />
                Saga / Compensation
              </CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="max-h-[520px] overflow-auto rounded-md bg-slate-950 p-3 text-xs leading-5 text-slate-100">
                {saga ? JSON.stringify(saga, null, 2) : "loading"}
              </pre>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Boxes className="h-4 w-4 text-blue-600" />
                Scenario Template Marketplace
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {templates.map((template, index) => {
                const item = template as { id?: string; name?: string; category?: string; description?: string };
                return (
                  <div key={item.id || index} className="rounded-md border border-slate-200 bg-white px-3 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-medium text-slate-900">{item.name}</div>
                      <Badge variant="outline">{item.category}</Badge>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{item.description}</p>
                  </div>
                );
              })}
              <div className="mt-3 flex items-center gap-2 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700">
                <Wrench className="h-3.5 w-3.5" />
                模板可通过 API 实例化为 draft 场景，再进入校验、模拟、发布流程。
              </div>
            </CardContent>
          </Card>
        </div>
      </main>
    </div>
  );
}
