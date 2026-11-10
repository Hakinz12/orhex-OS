import { memoryRouteAccess } from "@/lib/server/bff-core.mjs";
import { jsonError, proxyMemoryOperation, readSessionToken } from "@/lib/server/bff";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  request: Request,
  context: { params: Promise<{ folder: string }> },
) {
  const access = memoryRouteAccess(request, await readSessionToken());
  if (!access.ok) {
    return jsonError(401, "unauthenticated", "Sign-in is required.");
  }
  const { token } = access;
  const { folder } = await context.params;
  if (folder !== "active" && folder !== "trash") {
    return jsonError(400, "invalid_request", "The memory folder is invalid.");
  }
  const search = new URL(request.url).searchParams;
  if ([...search.keys()].some((key) => key !== "limit") || search.getAll("limit").length > 1) {
    return jsonError(422, "invalid_request", "The memory list limit is invalid.");
  }
  let query: { limit?: number } | undefined;
  if (search.has("limit")) {
    const rawLimit = search.get("limit");
    if (!rawLimit || !/^[0-9]+$/.test(rawLimit)) {
      return jsonError(422, "invalid_request", "The memory list limit is invalid.");
    }
    const limit = Number(rawLimit);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
      return jsonError(422, "invalid_request", "The memory list limit is invalid.");
    }
    query = { limit };
  }
  return proxyMemoryOperation(
    token,
    `/api/v1/memory/records/${folder}`,
    "GET",
    "memory_list",
    undefined,
    query,
  );
}
