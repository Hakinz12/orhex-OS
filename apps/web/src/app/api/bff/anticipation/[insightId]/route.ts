import { callBackend, sameOriginRequest } from "@/lib/server/bff-core.mjs";
import { clearSessionCookie, jsonError, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function PATCH(request: Request, context: { params: Promise<{ insightId: string }> }) {
  if (!sameOriginRequest(request)) return jsonError(403, "cross_origin_request", "This request must come from this application.");
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const { insightId } = await context.params;
  if (!/^[A-Za-z0-9-]{1,64}$/.test(insightId)) return jsonError(422, "invalid_request", "The insight identifier is invalid.");
  const body = await request.json().catch(() => null) as { status?: unknown } | null;
  if (!body || (body.status !== "active" && body.status !== "dismissed")) return jsonError(422, "invalid_request", "Choose a valid insight status.");
  const result = await callBackend(`/api/v1/anticipation/${encodeURIComponent(insightId)}`, { token, method: "PATCH", body: { status: body.status } });
  if (result.kind !== "response") return jsonError(503, "service_unavailable", "Anticipation is temporarily unavailable.");
  if (result.response.status === 401) return clearSessionCookie(jsonError(401, "unauthenticated", "Your session is invalid or expired."), token);
  if (!result.response.ok) return jsonError(result.response.status, "anticipation_error", "The insight could not be updated.");
  const payload: unknown = await result.response.json().catch(() => null);
  if (!payload || typeof payload !== "object" || typeof (payload as { id?: unknown }).id !== "string") return jsonError(502, "invalid_backend_response", "Anticipation returned an invalid response.");
  return Response.json(payload, { headers: { "Cache-Control": "no-store" } });
}
