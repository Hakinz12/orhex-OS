import { callBackend } from "@/lib/server/bff-core.mjs";
import { clearSessionCookie, jsonError, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const result = await callBackend("/api/v1/anticipation", { token, method: "GET" });
  if (result.kind !== "response") return jsonError(503, "service_unavailable", "Anticipation is temporarily unavailable.");
  if (result.response.status === 401) return clearSessionCookie(jsonError(401, "unauthenticated", "Your session is invalid or expired."), token);
  if (!result.response.ok) return jsonError(result.response.status, "anticipation_error", "Insights could not be loaded.");
  const payload: unknown = await result.response.json().catch(() => null);
  if (!Array.isArray(payload)) return jsonError(502, "invalid_backend_response", "Anticipation returned an invalid response.");
  return Response.json(payload, { headers: { "Cache-Control": "no-store" } });
}
