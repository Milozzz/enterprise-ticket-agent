const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ scenarioId: string; versionId: string }> }
) {
  const { scenarioId, versionId } = await params;
  const res = await fetch(`${BACKEND_URL}/api/admin/scenarios/${scenarioId}/versions/${versionId}/diff`, {
    cache: "no-store",
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("content-type") || "application/json" },
  });
}
