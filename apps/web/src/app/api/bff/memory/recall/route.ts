import { readJsonBody, sameOriginRequest, validateRecallBody } from "@/lib/server/bff-core.mjs";
import { jsonError, proxyMemoryOperation, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const parsed = await readJsonBody(request);
  if (!parsed.ok) return jsonError(parsed.status, "invalid_request", "The recall request is invalid.");
  const body = validateRecallBody(parsed.value);
  if (!body) return jsonError(422, "invalid_request", "Provide a valid recall query and result limit.");
  return proxyMemoryOperation(token, "/api/v1/memory/recall", "POST", "recall", body);
}
