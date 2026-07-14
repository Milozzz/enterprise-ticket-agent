const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

async function proxy(req: Request, path: string[], method: "GET" | "POST") {
  const url = new URL(req.url);
  const target = `${BACKEND_URL}/api/agent/governance/${path.join("/")}${url.search}`;
  const body = method === "POST" ? await req.text() : undefined;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const authorization = req.headers.get("Authorization");
  if (authorization) headers.Authorization = authorization;
  const adminKey = req.headers.get("X-Admin-API-Key") || process.env.ADMIN_API_KEY;
  if (adminKey) headers["X-Admin-API-Key"] = adminKey;
  const response = await fetch(target, { method, headers, body, cache: "no-store" });
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": response.headers.get("content-type") || "application/json" },
  });
}

type RouteContext = { params: Promise<{ path: string[] }> };

export async function GET(req: Request, context: RouteContext) {
  return proxy(req, (await context.params).path, "GET");
}

export async function POST(req: Request, context: RouteContext) {
  return proxy(req, (await context.params).path, "POST");
}
