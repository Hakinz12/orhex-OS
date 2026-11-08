import type {
  MemoryOperation,
  MemoryFolderResponse,
  MemoryRecallResponse,
  MemoryServiceStatus,
  ConversationResponse,
  SessionStatus,
  AnticipationInsight,
  OrhexTask,
  ActionProposal,
  OrchestrationRun,
  VerifiedWalletStatus,
  WalletVerificationChallenge,
} from "./types";

const DEFAULT_REQUEST_TIMEOUT_MS = 12_000;
const MEMORY_WRITE_TIMEOUT_MS = 170_000;
const CONVERSATION_TIMEOUT_MS = 45_000;

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

export function getVerifiedWallet(): Promise<VerifiedWalletStatus> {
  return requestJson("/api/bff/wallet/verified");
}

export function requestWalletVerificationChallenge(input: {
  network: string;
  address: string;
}): Promise<WalletVerificationChallenge> {
  return requestJson("/api/bff/wallet/challenge", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function submitWalletVerification(input: {
  challenge_id: string;
  signature: string;
}): Promise<VerifiedWalletStatus> {
  return requestJson("/api/bff/wallet/verify", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function createSession(token: string): Promise<SessionStatus> {
  return requestJson("/api/session", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function bootstrapVisitorSession(): Promise<SessionStatus> {
  return requestJson("/api/session/visitor", { method: "POST" });
}

export function rotateVisitorSession(): Promise<SessionStatus> {
  return requestJson("/api/session/visitor", { method: "PATCH" });
}

export function resetVisitorSession(): Promise<SessionStatus> {
  return requestJson("/api/session/visitor", { method: "PUT" });
}

export async function endVisitorSession(): Promise<void> {
  await requestJson("/api/session/visitor", { method: "DELETE" });
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

export function getConversation(conversationId: string): Promise<ConversationResponse> {
  return requestJson(
    `/api/bff/conversations/${encodeURIComponent(conversationId)}`,
    undefined,
    false,
    CONVERSATION_TIMEOUT_MS,
  );
}

export function sendConversationMessage(input: {
  message: string;
  conversation_id?: string;
  idempotency_key: string;
  include_wallet_context?: boolean;
}): Promise<ConversationResponse> {
  return requestJson(
    "/api/bff/conversations",
    {
      method: "POST",
      body: JSON.stringify(input),
    },
    false,
    CONVERSATION_TIMEOUT_MS,
  );
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

export function fetchAnticipations(): Promise<AnticipationInsight[]> {
  return requestJson("/api/bff/anticipation");
}

export function refreshAnticipations(): Promise<AnticipationInsight[]> {
  return requestJson("/api/bff/anticipation/refresh", { method: "POST" });
}

export function updateAnticipation(
  id: string,
  status: "active" | "dismissed",
): Promise<AnticipationInsight> {
  return requestJson(`/api/bff/anticipation/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}


export function fetchTasks(): Promise<OrhexTask[]> { return requestJson("/api/bff/tasks"); }
export function createTask(input:{title:string;description?:string;priority:"low"|"medium"|"high";due_at?:string|null}):Promise<OrhexTask>{return requestJson("/api/bff/tasks",{method:"POST",body:JSON.stringify(input)});}
export function updateTask(id:string,input:Partial<Pick<OrhexTask,"title"|"description"|"priority"|"status"|"due_at">>):Promise<OrhexTask>{return requestJson(`/api/bff/tasks/${encodeURIComponent(id)}`,{method:"PATCH",body:JSON.stringify(input)});}
export function fetchActionProposals():Promise<ActionProposal[]>{return requestJson("/api/bff/tasks/actions");}
export function prepareActionProposals():Promise<ActionProposal[]>{return requestJson("/api/bff/tasks/actions/prepare",{method:"POST"});}
export function decideAction(id:string,status:"approved"|"dismissed"):Promise<ActionProposal>{return requestJson(`/api/bff/tasks/actions/${encodeURIComponent(id)}/approve`,{method:"POST",body:JSON.stringify({status})});}

export function runOrchestration(): Promise<OrchestrationRun> {
  return requestJson("/api/bff/orchestration/run", {
    method: "POST",
    body: JSON.stringify({ idempotency_key: crypto.randomUUID(), trigger: "dashboard" }),
  });
}

export function fetchLatestOrchestration(): Promise<OrchestrationRun> {
  return requestJson("/api/bff/orchestration/latest");
}
