import { sameOriginRequest } from "@/lib/server/bff-core.mjs";
import { jsonError, proxyMemoryOperation, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ recordId: string }> },
) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const { recordId } = await context.params;
  if (!/^[A-Za-z0-9-]{1,64}$/.test(recordId)) {
    return jsonError(400, "invalid_request", "The operation identifier is invalid.");
  }
  return proxyMemoryOperation(
    token,
    `/api/v1/memory/operations/${encodeURIComponent(recordId)}/reconcile`,
    "POST",
    "operation",
  );
}
