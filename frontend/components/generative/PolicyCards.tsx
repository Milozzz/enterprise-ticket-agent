"use client";

import { BookOpen, ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface PolicyHit {
  id: string;
  document_id?: string;
  paragraph_id?: string;
  clause_id?: string;
  title: string;
  score: number;
  source: string;
  retrieval_method?: string;
  excerpt: string;
}

export default function PolicyCards({ results = [] }: { results?: PolicyHit[] }) {
  if (!results.length) return null;

  return (
    <div className="grid gap-3">
      {results.map((policy) => (
        <Card key={`${policy.id}-${policy.paragraph_id ?? policy.clause_id}`} className="border-slate-200">
          <CardHeader className="space-y-2 px-4 pb-2 pt-4">
            <div className="flex items-start justify-between gap-3">
              <CardTitle className="flex min-w-0 items-center gap-2 text-sm">
                <BookOpen className="h-4 w-4 shrink-0 text-blue-600" />
                <span className="truncate">{policy.title}</span>
              </CardTitle>
              <Badge variant="outline">{Math.round(policy.score * 100)}%</Badge>
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-slate-500">
              <span className="font-mono">{policy.document_id ?? policy.id}</span>
              <span className="font-mono">{policy.paragraph_id ?? policy.clause_id}</span>
              <span>{policy.retrieval_method ?? "retrieval"}</span>
            </div>
          </CardHeader>
          <CardContent className="px-4 pb-4 text-sm leading-6 text-slate-600">
            <p>{policy.excerpt}</p>
            <div className="mt-3 flex items-center gap-1 text-xs text-slate-400">
              <ExternalLink className="h-3 w-3" />
              <span className="break-all">{policy.source}</span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
