import {
  readJsonBody,
  sameOriginRequest,
  validateSuiWalletAddress,
} from "@/lib/server/bff-core.mjs";
import {
  jsonError,
  proxyWalletVerification,
  readSessionToken,
} from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  if (!token.startsWith("orv_")) {
    return jsonError(403, "wallet_verification_unavailable", "Wallet verification requires a visitor session.");
  }

  const parsed = await readJsonBody(request, 4096);
  if (!parsed.ok) return jsonError(parsed.status, "invalid_request", "The wallet request is invalid.");
  const body = parsed.value;
  if (
    !body ||
    typeof body !== "object" ||
    Array.isArray(body) ||
    Object.keys(body).length !== 2 ||
    !("network" in body) ||
    !("address" in body) ||
    typeof body.network !== "string" ||
    !["mainnet", "testnet", "devnet"].includes(body.network) ||
    !validateSuiWalletAddress(body.address)
  ) {
    return jsonError(422, "invalid_request", "Provide a supported Sui network and wallet address.");
  }
  const origin = request.headers.get("origin");
  if (!origin) return jsonError(403, "cross_origin_request", "A valid application origin is required.");
  return proxyWalletVerification(
    token,
    "/api/v1/wallet/verify-challenge",
    "POST",
    "challenge",
    {
      purpose: "wallet_link",
      network: body.network,
      address: body.address,
    },
    origin,
  );
}
