import { readJsonBody, sameOriginRequest, validateConversationRequest } from "@/lib/server/bff-core.mjs";
import { jsonError, proxyConversationRequest, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const parsed = await readJsonBody(request, 8_192);
  if (!parsed.ok) return jsonError(parsed.status, "invalid_request", "The conversation request is invalid.");
  const body = validateConversationRequest(parsed.value);
  if (!body) return jsonError(422, "invalid_request", "Provide a valid message and conversation identifier.");
  return proxyConversationRequest(token, "/api/v1/conversations", "POST", body);
}
