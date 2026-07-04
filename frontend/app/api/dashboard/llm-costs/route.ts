import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET(request: NextRequest) {
  const query = request.nextUrl.searchParams.toString();
  try {
    const res = await fetch(`${BACKEND_URL}/api/dashboard/llm-costs${query ? `?${query}` : ""}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) return NextResponse.json({ daily: [], sessions: [], totals: {} }, { status: res.status });
    return NextResponse.json(await res.json());
  } catch {
    return NextResponse.json({ daily: [], sessions: [], totals: {} });
  }
}
