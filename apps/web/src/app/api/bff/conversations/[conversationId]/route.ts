import { jsonError, proxyConversationRequest, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function GET(
  _request: Request,
  context: { params: Promise<{ conversationId: string }> },
) {
  const { conversationId } = await context.params;
  if (!UUID_PATTERN.test(conversationId)) {
    return jsonError(422, "invalid_request", "The conversation identifier is invalid.");
  }
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  return proxyConversationRequest(
    token,
    `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
    "GET",
  );
}
