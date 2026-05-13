import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ threadId: string }> }
) {
  try {
    const { threadId } = await params;
    const res = await fetch(`${BACKEND_URL}/api/agent/audit/${threadId}`, {
      cache: "no-store",
    });

    if (!res.ok) {
      return NextResponse.json({ error: "Failed to fetch audit logs" }, { status: res.status });
    }

    return NextResponse.json(await res.json());
  } catch (err) {
    console.error("[Audit Proxy]", err);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}