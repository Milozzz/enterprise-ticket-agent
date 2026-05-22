const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET() {
  const res = await fetch(`${BACKEND_URL}/api/admin/scenarios/evals/catalog`, {
    cache: "no-store",
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
  });
}
