import type {
  MemoryOperation,
  MemoryFolderResponse,
  MemoryRecallResponse,
  MemoryServiceStatus,
  SessionStatus,
} from "./types";

const DEFAULT_REQUEST_TIMEOUT_MS = 12_000;
const MEMORY_WRITE_TIMEOUT_MS = 170_000;

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function requestJson<T>(
  path: string,
  init?: RequestInit,
  acceptFailedOperation = false,
  timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new ApiError("ORHEX could not reach its service. Please try again.", 0);
  }

  let payload: { error?: string; message?: string } & Record<string, unknown>;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError("ORHEX received an invalid service response.", response.status);
  }
  const isFailedOperation =
    acceptFailedOperation &&
    response.status === 502 &&
    typeof payload.operation_id === "string" &&
    typeof payload.status === "string";
  if (!response.ok && !isFailedOperation) {
    throw new ApiError(
      typeof payload.message === "string" ? payload.message : "The request could not be completed.",
      response.status,
    );
  }
  return payload as T;
}

export function getSession(): Promise<SessionStatus> {
  return requestJson("/api/session");
}

export function createSession(token: string): Promise<SessionStatus> {
  return requestJson("/api/session", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export async function logout(): Promise<void> {
  await requestJson("/api/session", { method: "DELETE" });
}

export function fetchMemoryStatus(): Promise<MemoryServiceStatus> {
  return requestJson("/api/bff/memory/status");
}

export function rememberMemory(input: {
  text: string;
  category?: string;
  memory_type?: string;
  idempotency_key: string;
}): Promise<MemoryOperation> {
  return requestJson("/api/bff/memory/remember", {
    method: "POST",
    body: JSON.stringify(input),
  }, true, MEMORY_WRITE_TIMEOUT_MS);
}

export function recallMemories(query: string, limit = 5): Promise<MemoryRecallResponse> {
  return requestJson("/api/bff/memory/recall", {
    method: "POST",
    body: JSON.stringify({ query, limit }),
  });
}

export function getMemoryOperation(operationId: string): Promise<MemoryOperation> {
  return requestJson(`/api/bff/memory/operations/${encodeURIComponent(operationId)}`);
}

export function reconcileMemoryOperation(operationId: string): Promise<MemoryOperation> {
  return requestJson(
    `/api/bff/memory/operations/${encodeURIComponent(operationId)}/reconcile`,
    { method: "POST" },
    true,
  );
}

export function listMemories(folder: "active" | "trash"): Promise<MemoryFolderResponse> {
  return requestJson(`/api/bff/memory/records/${folder}`);
}

export function trashMemory(operationId: string): Promise<MemoryOperation> {
  return requestJson(`/api/bff/memory/operations/${encodeURIComponent(operationId)}`, {
    method: "DELETE",
  });
}

export function restoreMemory(operationId: string): Promise<MemoryOperation> {
  return requestJson(
    `/api/bff/memory/operations/${encodeURIComponent(operationId)}/restore`,
    { method: "POST" },
  );
}

export function checkPermanentDeleteSupport(operationId: string): Promise<MemoryOperation> {
  return requestJson(
    `/api/bff/memory/operations/${encodeURIComponent(operationId)}/permanent-delete`,
    { method: "POST" },
  );
}
