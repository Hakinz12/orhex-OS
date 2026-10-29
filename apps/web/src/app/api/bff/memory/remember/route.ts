import { readJsonBody, sameOriginRequest, validateRememberBody } from "@/lib/server/bff-core.mjs";
import { jsonError, proxyMemoryOperation, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 180;

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const parsed = await readJsonBody(request);
  if (!parsed.ok) return jsonError(parsed.status, "invalid_request", "The memory request is invalid.");
  const body = validateRememberBody(parsed.value);
  if (!body) return jsonError(422, "invalid_request", "Provide valid memory text and supported fields.");
  return proxyMemoryOperation(token, "/api/v1/memory/remember", "POST", "operation", body);
}
