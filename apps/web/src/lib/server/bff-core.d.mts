export type BackendResult =
  | { kind: "response"; response: Response }
  | { kind: "unavailable" }
  | { kind: "rejected" };

export type SessionValidation =
  | { ok: true; expiresAt: Date }
  | { ok: false; status: 401 | 503 };

export function sessionCookieName(env?: NodeJS.ProcessEnv): string;
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
export function clearedSessionCookieOptions(env?: NodeJS.ProcessEnv): {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  expires: Date;
  maxAge: 0;
};
export function sameOriginRequest(request: Request): boolean;
export function backendBaseUrl(env?: NodeJS.ProcessEnv): string;
export function callBackend(
  path: string,
  options?: {
    token?: string;
    method?: "GET" | "POST";
    body?: Record<string, unknown>;
    env?: NodeJS.ProcessEnv;
    fetchImpl?: typeof fetch;
  },
): Promise<BackendResult>;
export function validateBackendSession(
  token: unknown,
  options?: { env?: NodeJS.ProcessEnv; fetchImpl?: typeof fetch },
): Promise<SessionValidation>;
export function readJsonBody(
  request: Request,
  maximumBytes?: number,
): Promise<{ ok: true; value: unknown } | { ok: false; status: 400 | 413 }>;
export function validateRememberBody(value: unknown): Record<string, unknown> | null;
export function validateRecallBody(value: unknown): Record<string, unknown> | null;
export function safeReadiness(payload: unknown): Record<string, string | boolean> | null;
export function safeMemoryOperation(payload: unknown): Record<string, unknown> | null;
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
