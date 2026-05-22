const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ scenarioId: string }> }
) {
  const { scenarioId } = await params;
  const body = await req.text();
  const res = await fetch(`${BACKEND_URL}/api/admin/scenarios/${scenarioId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body,
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
  });
}
