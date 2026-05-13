import { Card, CardContent, CardHeader } from "@/components/ui/card";

export default function DashboardLoading() {
  return (
    <div className="min-h-screen bg-slate-50">
      <div className="border-b border-slate-200 bg-white/90 px-4 py-4 sm:px-6">
        <div className="mx-auto h-8 max-w-6xl animate-pulse rounded bg-slate-200" />
      </div>
      <div className="mx-auto max-w-6xl space-y-8 px-4 py-8 sm:px-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Card key={i} className="border-slate-200/80">
              <CardHeader className="pb-2">
                <div className="h-4 w-24 animate-pulse rounded bg-slate-200" />
              </CardHeader>
              <CardContent>
                <div className="h-8 w-16 animate-pulse rounded bg-slate-200" />
              </CardContent>
            </Card>
          ))}
        </div>
        <Card className="border-slate-200/80">
          <CardHeader>
            <div className="h-5 w-48 animate-pulse rounded bg-slate-200" />
          </CardHeader>
          <CardContent>
            <div className="h-64 animate-pulse rounded-lg bg-slate-100" />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
