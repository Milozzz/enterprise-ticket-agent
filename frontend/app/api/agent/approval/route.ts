import { type NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${BACKEND_URL}/api/agent/approval`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Role": req.headers.get("X-User-Role") ?? body.reviewerRole ?? "",
        "X-User-Id": req.headers.get("X-User-Id") ?? body.reviewerId ?? "",
      },
      body: JSON.stringify(body),
      cache: "no-store",
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(data, { status: res.status });
    }
    return NextResponse.json(data);
  } catch (err) {
    console.error("[Generic Approval Proxy]", err);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
