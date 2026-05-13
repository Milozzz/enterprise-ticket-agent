import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

/**
 * GET /api/health
 * 代理检查 Python 后端 /health（供浏览器同源请求）
 */
export async function GET() {
  try {
    const res = await fetch(`${BACKEND_URL}/health`, {
      method: "GET",
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) {
      return NextResponse.json(
        { ok: false, error: `Backend returned ${res.status}` },
        { status: 503 }
      );
    }
    const data = (await res.json()) as Record<string, unknown>;
    const merged: Record<string, unknown> = { ok: true, ...data };
    if (data.health_schema !== 2 || typeof data.simulate_database_down === "undefined") {
      merged.hint =
        "BACKEND_URL 指向的服务可能不是本项目最新后端。请直接访问 http://127.0.0.1:8000/health ，并检查 frontend 的 .env 中 BACKEND_URL。";
    }
    return NextResponse.json(merged);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "连接失败";
    return NextResponse.json({ ok: false, error: msg }, { status: 503 });
  }
}
