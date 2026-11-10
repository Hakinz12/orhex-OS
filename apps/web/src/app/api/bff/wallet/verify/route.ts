import { readJsonBody, sameOriginRequest } from "@/lib/server/bff-core.mjs";
import {
  jsonError,
  proxyWalletVerification,
  readSessionToken,
} from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const BASE64_PATTERN = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  if (!token.startsWith("orv_")) {
    return jsonError(403, "wallet_verification_unavailable", "Wallet verification requires a visitor session.");
  }

  const parsed = await readJsonBody(request, 24_576);
  if (!parsed.ok) return jsonError(parsed.status, "invalid_request", "The signature request is invalid.");
  const body = parsed.value;
  if (
    !body ||
    typeof body !== "object" ||
    Array.isArray(body) ||
    Object.keys(body).length !== 3 ||
    !("challenge_id" in body) ||
    !("signature" in body) ||
    !("signed_message" in body) ||
    typeof body.challenge_id !== "string" ||
    !UUID_PATTERN.test(body.challenge_id) ||
    typeof body.signature !== "string" ||
    body.signature.length < 1 ||
    body.signature.length > 16_384 ||
    !BASE64_PATTERN.test(body.signature) ||
    typeof body.signed_message !== "string" ||
    body.signed_message.length < 1 ||
    body.signed_message.length > 4_096 ||
    !BASE64_PATTERN.test(body.signed_message)
  ) {
    return jsonError(422, "invalid_request", "Provide a valid wallet challenge and signature.");
  }
  const origin = request.headers.get("origin");
  if (!origin) return jsonError(403, "cross_origin_request", "A valid application origin is required.");
  const correlationId = request.headers.get("x-orhex-correlation-id");
  if (
    !correlationId ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(correlationId)
  ) {
    return jsonError(400, "invalid_request", "The wallet verification request is invalid.");
  }
  return proxyWalletVerification(
    token,
    "/api/v1/wallet/verify-signature",
    "POST",
    "verification",
    {
      challenge_id: body.challenge_id,
      signature: body.signature,
      signed_message: body.signed_message,
    },
    origin,
    correlationId,
  );
}
