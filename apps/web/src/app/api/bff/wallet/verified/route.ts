import { jsonError, proxyWalletVerification, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const token = await readSessionToken();
  if (!token) return jsonError(401, "unauthenticated", "Sign-in is required.");
  if (!token.startsWith("orv_")) {
    return jsonError(403, "wallet_verification_unavailable", "Wallet verification requires a visitor session.");
  }
  return proxyWalletVerification(
    token,
    "/api/v1/wallet/verified",
    "GET",
    "status",
  );
}
