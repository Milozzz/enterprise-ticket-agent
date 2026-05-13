import { DashboardContent } from "@/components/dashboard/DashboardContent";
import type { DashboardStats } from "@/types";

export default async function DashboardPage() {
  const base = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  let stats: DashboardStats | null = null;
  try {
    const res = await fetch(`${base}/api/dashboard/stats`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });
    if (res.ok) {
      stats = (await res.json()) as DashboardStats;
    }
  } catch {
    stats = null;
  }
  return <DashboardContent stats={stats} />;
}

export const metadata = {
  title: "运营监控 | AI Enterprise Agent",
  description: "工单与审计聚合面板",
};
