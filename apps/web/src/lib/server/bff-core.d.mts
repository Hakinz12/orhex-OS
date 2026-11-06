export type BackendResult =
  | { kind: "response"; response: Response }
  | { kind: "unavailable" }
  | { kind: "rejected" };

export type SessionValidation =
  | { ok: true; expiresAt: Date }
  | { ok: false; status: 401 | 503 };

export type VisitorSessionValidation =
  | {
      ok: true;
      visitorIdentityId: string;
      createdAt: Date;
      credentialCreatedAt: Date;
      credentialExpiresAt: Date | null;
    }
  | { ok: false; status: 401 | 503 };

export function sessionCookieName(env?: NodeJS.ProcessEnv): string;
export function visitorCookieName(env?: NodeJS.ProcessEnv): string;
export function sessionCookieOptions(
  expiresAt: Date | string,
  env?: NodeJS.ProcessEnv,
): {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  expires: Date;
  maxAge: number;
};
export function visitorCookieOptions(env?: NodeJS.ProcessEnv): {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  expires: Date;
  maxAge: number;
};
export function clearedSessionCookieOptions(env?: NodeJS.ProcessEnv): {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  expires: Date;
  maxAge: 0;
};
export function clearedVisitorCookieOptions(env?: NodeJS.ProcessEnv): {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  expires: Date;
  maxAge: 0;
};
export function sameOriginRequest(request: Request, env?: NodeJS.ProcessEnv): boolean;
export function memoryRouteAccess(
  request: Request,
  token: string | null,
  requiresSameOrigin?: boolean,
):
  | { ok: true; token: string }
  | { ok: false; status: 401 | 403 };
export function backendBaseUrl(env?: NodeJS.ProcessEnv): string;
export function callBackend(
  path: string,
  options?: {
    token?: string;
    method?: "GET" | "POST" | "PATCH" | "DELETE";
    body?: Record<string, unknown>;
    query?: { limit?: number; cursor?: string };
    env?: NodeJS.ProcessEnv;
    fetchImpl?: typeof fetch;
  },
): Promise<BackendResult>;
export function validateBackendSession(
  token: unknown,
  options?: { env?: NodeJS.ProcessEnv; fetchImpl?: typeof fetch },
): Promise<SessionValidation>;
export function validateBackendVisitorSession(
  token: unknown,
  options?: { env?: NodeJS.ProcessEnv; fetchImpl?: typeof fetch },
): Promise<VisitorSessionValidation>;
export function readJsonBody(
  request: Request,
  maximumBytes?: number,
): Promise<{ ok: true; value: unknown } | { ok: false; status: 400 | 413 }>;
export function validateRememberBody(value: unknown): Record<string, unknown> | null;
export function validateRecallBody(value: unknown): Record<string, unknown> | null;
export function validateConversationRequest(value: unknown): {
  message: string;
  conversation_id?: string | null;
  idempotency_key: string;
  include_wallet_context?: boolean;
} | null;
export function safeConversation(payload: unknown): {
  conversation_id: string;
  version: number;
  messages: Array<{
    id: string;
    role: "user" | "assistant";
    content: string;
    created_at: string;
  }>;
} | null;
export function safeReadiness(payload: unknown): Record<string, string | boolean> | null;
export function safeMemoryOperation(payload: unknown): Record<string, unknown> | null;
export function safeMemoryList(payload: unknown): {
  folder: "active" | "trash";
  total: number;
  memories: Array<Record<string, unknown>>;
} | null;
export function safeRecall(payload: unknown): {
  query: string;
  total: number;
  results: Array<{
    blob_id: string;
    text: string;
    distance: number;
    score?: number;
    created_at?: string | null;
  }>;
} | null;
export function safeErrorResponse(status: number): {
  status: number;
  body: { error: string; message: string };
};
export function validateSuiWalletAddress(value: unknown): value is string;
export function parseSuiPagination(searchParams: URLSearchParams): {
  limit?: number;
  cursor?: string;
} | null;
export function safeSuiBalances(payload: unknown): Record<string, unknown> | null;
export function safeSuiObjects(payload: unknown): Record<string, unknown> | null;
export function safeSuiErrorResponse(status: number): {
  status: number;
  body: { error: string; message: string };
};
export function safeSuiBackendResponse(
  response: Response,
  resource: "balances" | "objects",
): Promise<{ status: number; body: Record<string, unknown> }>;
