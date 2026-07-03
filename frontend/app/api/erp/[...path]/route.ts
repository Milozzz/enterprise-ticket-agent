const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

async function proxy(req: Request, path: string[], method: "GET" | "POST" | "PUT") {
  const url = new URL(req.url);
  const target = `${BACKEND_URL}/api/erp/${path.join("/")}${url.search}`;
  const body = method === "POST" || method === "PUT" ? await req.text() : undefined;
  const headers: Record<string, string> = {};
  if (method === "POST" || method === "PUT") headers["Content-Type"] = "application/json";
  const authorization = req.headers.get("Authorization");
  if (authorization) headers.Authorization = authorization;
  if (process.env.ADMIN_API_KEY) headers["X-Admin-API-Key"] = process.env.ADMIN_API_KEY;
  const idempotencyKey = req.headers.get("Idempotency-Key");
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  const res = await fetch(target, {
    method,
    headers,
    body,
    cache: "no-store",
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
  });
}

export async function GET(
  req: Request,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path, "GET");
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path, "POST");
}

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  return proxy(req, path, "PUT");
}
