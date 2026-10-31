import { memoryRouteAccess } from "@/lib/server/bff-core.mjs";
import { jsonError, proxyMemoryOperation, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  _request: Request,
  context: { params: Promise<{ folder: string }> },
) {
  const access = memoryRouteAccess(_request, await readSessionToken());
  if (!access.ok) {
    return jsonError(401, "unauthenticated", "Sign-in is required.");
  }
  const { token } = access;
  const { folder } = await context.params;
  if (folder !== "active" && folder !== "trash") {
    return jsonError(400, "invalid_request", "The memory folder is invalid.");
  }
  return proxyMemoryOperation(
    token,
    `/api/v1/memory/records/${folder}`,
    "GET",
    "memory_list",
  );
}
