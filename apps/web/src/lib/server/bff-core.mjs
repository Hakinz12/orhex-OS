const SESSION_PATH = "/api/v1/auth/session";
const MEMORY_STATUS_PATH = "/api/v1/memory/status";
const MEMORY_WRITE_TIMEOUT_MS = 160_000;
const BACKEND_REQUEST_TIMEOUT_MS = 8_000;

export function sessionCookieName(env = process.env) {
  // Keep the existing cookie name so active browser sessions survive the rebrand.
  return env.NODE_ENV === "production"
    ? "__Host-orhrex_session"
    : "orhrex_session";
}

export function sessionCookieOptions(expiresAt, env = process.env) {
  const maxAge = Math.max(0, Math.floor((new Date(expiresAt).getTime() - Date.now()) / 1000));
  return {
    httpOnly: true,
    secure: env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(expiresAt),
    maxAge,
  };
}

export function clearedSessionCookieOptions(env = process.env) {
  return {
    httpOnly: true,
    secure: env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
    maxAge: 0,
  };
}

export function sameOriginRequest(request) {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    const originUrl = new URL(origin);
    const requestUrl = new URL(request.url);
    const requestHost =
      request.headers.get("x-forwarded-host")?.split(",")[0]?.trim() ||
      request.headers.get("host") ||
      requestUrl.host;
    const forwardedProtocol = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
    const requestProtocol = forwardedProtocol || requestUrl.protocol.slice(0, -1);
    return (
      ["http:", "https:"].includes(originUrl.protocol) &&
      originUrl.host.toLowerCase() === requestHost.toLowerCase() &&
      originUrl.protocol === `${requestProtocol}:`
    );
  } catch {
    return false;
  }
}

export function memoryRouteAccess(request, token, requiresSameOrigin = false) {
  if (requiresSameOrigin && !sameOriginRequest(request)) {
    return { ok: false, status: 403 };
  }
  return token
    ? { ok: true, token }
    : { ok: false, status: 401 };
}

export function backendBaseUrl(env = process.env) {
  const configured =
    env.ORHEX_API_BASE_URL ||
    // Backward compatibility for existing deployments using the previous name.
    env.ORHREX_API_BASE_URL ||
    (env.NODE_ENV === "production" ? undefined : "http://127.0.0.1:8000");
  if (!configured) throw new Error("backend_not_configured");
  const url = new URL(configured);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    !url.hostname ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("backend_url_invalid");
  }
  if (env.NODE_ENV === "production" && url.protocol !== "https:") {
    throw new Error("backend_https_required");
  }
  return url.origin;
}

function isAllowedBackendPath(path, method) {
  if (path === SESSION_PATH && method === "GET") return true;
  if (path === MEMORY_STATUS_PATH && method === "GET") return true;
  if (path === "/api/v1/memory/remember" && method === "POST") return true;
  if (path === "/api/v1/memory/recall" && method === "POST") return true;
  if (/^\/api\/v1\/memory\/records\/(active|trash)$/.test(path) && method === "GET") return true;
  if (/^\/api\/v1\/memory\/operations\/[A-Za-z0-9-]{1,64}$/.test(path) && method === "DELETE") return true;
  return /^\/api\/v1\/memory\/operations\/[A-Za-z0-9-]{1,64}$/.test(path) && method === "GET"
    || /^\/api\/v1\/memory\/operations\/[A-Za-z0-9-]{1,64}\/(reconcile|restore|permanent-delete)$/.test(path) && method === "POST";
}

export async function callBackend(
  path,
  { token, method = "GET", body, env = process.env, fetchImpl = fetch } = {},
) {
  if (!isAllowedBackendPath(path, method)) {
    return { kind: "rejected" };
  }
  let base;
  try {
    base = backendBaseUrl(env);
  } catch {
    return { kind: "unavailable" };
  }

  try {
    const headers = new Headers({ Accept: "application/json" });
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetchImpl(`${base}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(
        path === "/api/v1/memory/remember"
          ? MEMORY_WRITE_TIMEOUT_MS
          : BACKEND_REQUEST_TIMEOUT_MS,
      ),
      redirect: "error",
    });
    return { kind: "response", response };
  } catch (error) {
    const errorName =
      error instanceof Error && /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(error.name)
        ? error.name
        : "UnknownError";
    console.warn("Backend request failed: %s", errorName);
    return { kind: "unavailable" };
  }
}

export async function validateBackendSession(token, options = {}) {
  if (typeof token !== "string" || !/^orh_[A-Za-z0-9_-]{32,100}$/.test(token)) {
    return { ok: false, status: 401 };
  }
  const result = await callBackend(SESSION_PATH, { ...options, token });
  if (result.kind !== "response") return { ok: false, status: 503 };
  if (result.response.status === 401) return { ok: false, status: 401 };
  if (!result.response.ok) return { ok: false, status: 503 };
  try {
    const payload = await result.response.json();
    const expiresAt = new Date(payload.expires_at);
    if (
      Object.keys(payload).length !== 1 ||
      !Number.isFinite(expiresAt.getTime()) ||
      expiresAt.getTime() <= Date.now()
    ) {
      return { ok: false, status: 401 };
    }
    return { ok: true, expiresAt };
  } catch {
    return { ok: false, status: 503 };
  }
}

export async function readJsonBody(request, maximumBytes = 16_384) {
  const contentLength = Number(request.headers.get("content-length") || 0);
  if (contentLength > maximumBytes) return { ok: false, status: 413 };
  try {
    const text = await request.text();
    if (new TextEncoder().encode(text).byteLength > maximumBytes) {
      return { ok: false, status: 413 };
    }
    return { ok: true, value: JSON.parse(text) };
  } catch {
    return { ok: false, status: 400 };
  }
}

export function validateRememberBody(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const allowed = new Set(["text", "category", "memory_type", "metadata", "idempotency_key"]);
  if (Object.keys(value).some((key) => !allowed.has(key))) return null;
  if (typeof value.text !== "string" || value.text.trim().length < 3 || value.text.length > 8000) return null;
  if (value.category !== undefined && (typeof value.category !== "string" || value.category.length < 1 || value.category.length > 64)) return null;
  if (value.memory_type !== undefined && (typeof value.memory_type !== "string" || value.memory_type.length < 1 || value.memory_type.length > 64)) return null;
  if (value.metadata !== undefined && (!value.metadata || typeof value.metadata !== "object" || Array.isArray(value.metadata))) return null;
  if (value.idempotency_key !== undefined && (
    typeof value.idempotency_key !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.idempotency_key)
  )) return null;
  return {
    text: value.text.trim(),
    ...(value.category !== undefined ? { category: value.category } : {}),
    ...(value.memory_type !== undefined ? { memory_type: value.memory_type } : {}),
    ...(value.metadata !== undefined ? { metadata: value.metadata } : {}),
    ...(value.idempotency_key !== undefined ? { idempotency_key: value.idempotency_key } : {}),
  };
}

export function validateRecallBody(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Object.keys(value).some((key) => !["query", "limit"].includes(key))) return null;
  if (typeof value.query !== "string" || value.query.trim().length < 3 || value.query.length > 1000) return null;
  if (value.limit !== undefined && (!Number.isInteger(value.limit) || value.limit < 1 || value.limit > 20)) return null;
  return { query: value.query.trim(), ...(value.limit !== undefined ? { limit: value.limit } : {}) };
}

export function safeReadiness(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const allowed = ["status", "write_ready", "relayer_status", "relayer_version", "mode", "error", "message"];
  return Object.fromEntries(
    Object.entries(payload).filter(([key, value]) => allowed.includes(key) && ["string", "boolean"].includes(typeof value)),
  );
}

export function safeMemoryOperation(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  if (typeof payload.status !== "string" || typeof payload.operation_id !== "string") return null;
  const allowed = [
    "status", "operation_id", "blob_id", "memory_id", "category",
    "memory_type", "metadata", "created_at", "confirmed_at", "trashed_at",
    "permanent_delete_started_at", "error",
  ];
  return Object.fromEntries(
    Object.entries(payload).filter(([key]) => allowed.includes(key)),
  );
}

export function safeMemoryList(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  if (
    !["active", "trash"].includes(payload.folder) ||
    !Array.isArray(payload.memories) ||
    !Number.isInteger(payload.total) ||
    payload.total !== payload.memories.length
  ) return null;
  if (payload.memories.some((item) => !item || typeof item !== "object" || Array.isArray(item))) return null;
  const memories = payload.memories.map((item) => ({
    operation_id: item.operation_id,
    text: item.text,
    status: item.status,
    blob_id: item.blob_id,
    category: item.category,
    memory_type: item.memory_type,
    created_at: item.created_at,
    confirmed_at: item.confirmed_at,
    trashed_at: item.trashed_at,
    permanent_delete_started_at: item.permanent_delete_started_at,
  }));
  if (memories.some((item) =>
    typeof item.operation_id !== "string" ||
    typeof item.text !== "string" ||
    item.status !== "confirmed" ||
    typeof item.category !== "string" ||
    typeof item.memory_type !== "string" ||
    typeof item.created_at !== "string" ||
    (item.blob_id !== null && typeof item.blob_id !== "string")
  )) return null;
  return { folder: payload.folder, total: memories.length, memories };
}

export function safeRecall(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload) || !Array.isArray(payload.results)) return null;
  if (typeof payload.query !== "string" || !Number.isInteger(payload.total) || payload.total < 0) return null;
  const results = payload.results.map((item) => ({
    blob_id: item.blob_id,
    text: item.text,
    distance: item.distance,
    ...(item.score !== undefined ? { score: item.score } : {}),
    ...(item.created_at !== undefined ? { created_at: item.created_at } : {}),
  }));
  if (results.some((item) =>
    typeof item.blob_id !== "string" ||
    typeof item.text !== "string" ||
    typeof item.distance !== "number"
  )) return null;
  return { query: payload.query, total: payload.total, results };
}

export function safeErrorResponse(status) {
  if (status === 401) return { status: 401, body: { error: "unauthenticated", message: "Your session is invalid or expired." } };
  if (status === 403) return { status: 403, body: { error: "forbidden", message: "This operation is not permitted." } };
  if (status === 404) return { status: 404, body: { error: "not_found", message: "The requested memory operation was not found." } };
  if (status === 409) return { status: 409, body: { error: "conflict", message: "The request conflicts with an existing operation." } };
  if (status === 422) return { status: 422, body: { error: "invalid_request", message: "The request was rejected by the memory service." } };
  if (status === 429) return { status: 429, body: { error: "rate_limited", message: "Too many requests. Please retry shortly." } };
  if (status === 501) return { status: 501, body: { error: "unsupported_operation", message: "Walrus permanent deletion is unsupported; the memory remains in Trash." } };
  if (status === 502) return { status: 502, body: { error: "memory_service_error", message: "The memory service could not complete the request." } };
  return { status: 503, body: { error: "service_unavailable", message: "The memory service is temporarily unavailable." } };
}
