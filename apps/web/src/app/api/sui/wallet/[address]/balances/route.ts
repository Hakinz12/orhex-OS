import { memoryRouteAccess } from "@/lib/server/bff-core.mjs";
import { jsonError, proxySuiWalletData, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ address: string }> },
) {
  const access = memoryRouteAccess(request, await readSessionToken());
  if (!access.ok) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const { address } = await context.params;
  return proxySuiWalletData(access.token, request, address, "balances");
}
