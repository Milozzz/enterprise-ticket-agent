import { NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET(req: Request) {
  const url = new URL(req.url);
  try {
    const res = await fetch(`${BACKEND_URL}/api/dashboard/erp-business-metrics${url.search}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return NextResponse.json({ error: `Backend ${res.status}` }, { status: res.status });
    return NextResponse.json(await res.json());
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 503 });
  }
}
