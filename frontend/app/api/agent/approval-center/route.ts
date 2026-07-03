const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const query = url.searchParams.toString();
  const res = await fetch(`${BACKEND_URL}/api/agent/approval-center${query ? `?${query}` : ""}`, {
    cache: "no-store",
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
  });
}

export async function POST() {
  const res = await fetch(`${BACKEND_URL}/api/agent/approval-center/escalate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
  });
}
