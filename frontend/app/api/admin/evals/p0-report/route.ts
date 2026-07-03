const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET() {
  const headers: Record<string, string> = {};
  if (process.env.ADMIN_API_KEY) headers["X-Admin-API-Key"] = process.env.ADMIN_API_KEY;
  const res = await fetch(`${BACKEND_URL}/api/admin/evals/p0-report`, {
    headers,
    cache: "no-store",
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
  });
}
