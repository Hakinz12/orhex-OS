import { callBackend } from "@/lib/server/bff-core.mjs";
import { clearSessionCookie, jsonError, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_request: Request, context: { params: Promise<{ address: string }> }) {
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const { address } = await context.params;
  if (!/^0x[0-9a-fA-F]{1,64}$/.test(address)) {
    return jsonError(422, "invalid_request", "The Sui wallet address is invalid.");
  }
  const result = await callBackend(`/api/v1/sui/intelligence/${address}`, { token, method: "GET" });
  if (result.kind !== "response") return jsonError(503, "service_unavailable", "Blockchain intelligence is temporarily unavailable.");
  if (result.response.status === 401) return clearSessionCookie(jsonError(401, "unauthenticated", "Your session is invalid or expired."), token);
  const payload: unknown = await result.response.json().catch(() => null);
  if (!result.response.ok) return jsonError(result.response.status, "blockchain_intelligence_error", "Wallet intelligence could not be loaded.");
  if (!payload || typeof payload !== "object" || !("portfolio" in payload) || !("activity" in payload)) {
    return jsonError(502, "invalid_backend_response", "The blockchain service returned an invalid response.");
  }
  return Response.json(payload, { headers: { "Cache-Control": "no-store" } });
}
