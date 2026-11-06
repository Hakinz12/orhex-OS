const SESSION_PATH = "/api/v1/auth/session";
const VISITOR_BOOTSTRAP_PATH = "/api/v1/visitor/bootstrap";
const VISITOR_SESSION_PATH = "/api/v1/visitor/session";
const MEMORY_STATUS_PATH = "/api/v1/memory/status";
const SUI_WALLET_PATH = /^\/api\/v1\/sui\/wallet\/0x[0-9a-fA-F]{1,64}\/(balances|objects)$/;
const MEMORY_WRITE_TIMEOUT_MS = 160_000;
const CONVERSATION_REQUEST_TIMEOUT_MS = 40_000;
const BACKEND_REQUEST_TIMEOUT_MS = 8_000;
const MAX_SUI_CURSOR_LENGTH = 512;
const VISITOR_COOKIE_MAX_AGE_SECONDS = 31_536_000;

export function sessionCookieName(env = process.env) {
  // Keep the existing cookie name so active browser sessions survive the rebrand.
  return env.NODE_ENV === "production"
    ? "__Host-orhrex_session"
    : "orhrex_session";
}

export function visitorCookieName(env = process.env) {
  return env.NODE_ENV === "production"
    ? "__Host-orhrex_visitor_session"
    : "orhrex_visitor_session";
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

export function visitorCookieOptions(env = process.env) {
  const expires = new Date(Date.now() + VISITOR_COOKIE_MAX_AGE_SECONDS * 1000);
  return {
    httpOnly: true,
    secure: env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires,
    maxAge: VISITOR_COOKIE_MAX_AGE_SECONDS,
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

export function clearedVisitorCookieOptions(env = process.env) {
  return clearedSessionCookieOptions(env);
}

export function sameOriginRequest(request, env = process.env) {
  const origin = request.headers.get("origin");
  if (!origin) return false;
  try {
    const originUrl = new URL(origin);
    const requestUrl = new URL(request.url);
    const configuredOrigin = env.ORHEX_APP_ORIGIN;
    if (env.NODE_ENV === "production" && !configuredOrigin) return false;
    const configuredUrl = configuredOrigin ? new URL(configuredOrigin) : null;
    if (
      configuredUrl &&
      (configuredUrl.pathname !== "/" ||
        configuredUrl.search ||
        configuredUrl.hash ||
        configuredUrl.username ||
        configuredUrl.password ||
        (env.NODE_ENV === "production" && configuredUrl.protocol !== "https:"))
    ) return false;
    const expectedOrigin = configuredUrl?.origin ?? requestUrl.origin;
    return (
      ["http:", "https:"].includes(originUrl.protocol) &&
      !originUrl.username &&
      !originUrl.password &&
      originUrl.pathname === "/" &&
      !originUrl.search &&
      !originUrl.hash &&
      originUrl.origin.toLowerCase() === expectedOrigin.toLowerCase()
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
  if (path === VISITOR_BOOTSTRAP_PATH && method === "POST") return true;
  if (path === VISITOR_SESSION_PATH && ["GET", "DELETE"].includes(method)) return true;
  if (
    ["/api/v1/visitor/session/rotate", "/api/v1/visitor/session/reset"].includes(path) &&
    method === "POST"
  ) return true;
  if (path === MEMORY_STATUS_PATH && method === "GET") return true;
  if (path === "/api/v1/tasks" && ["GET", "POST"].includes(method)) return true;
  if (path === "/api/v1/orchestration/run" && method === "POST") return true;
  if (path === "/api/v1/orchestration/latest" && method === "GET") return true;
  if (path === "/api/v1/orchestration/runs" && method === "GET") return true;
  if (/^\/api\/v1\/tasks\/[A-Za-z0-9-]{1,64}$/.test(path) && method === "PATCH") return true;
  if (path === "/api/v1/tasks/actions" && method === "GET") return true;
  if (path === "/api/v1/tasks/actions/prepare" && method === "POST") return true;
  if (/^\/api\/v1\/tasks\/actions\/[A-Za-z0-9-]{1,64}\/approve$/.test(path) && method === "POST") return true;
  if (path === "/api/v1/anticipation" && method === "GET") return true;
  if (path === "/api/v1/anticipation/refresh" && method === "POST") return true;
  if (/^\/api\/v1\/anticipation\/[A-Za-z0-9-]{1,64}$/.test(path) && method === "PATCH") return true;
  if (path === "/api/v1/conversations" && method === "POST") return true;
  if (
    /^\/api\/v1\/conversations\/[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(path) &&
    method === "GET"
  ) return true;
  if (SUI_WALLET_PATH.test(path) && method === "GET") return true;
  if (/^\/api\/v1\/sui\/intelligence\/0x[0-9a-fA-F]{1,64}$/.test(path) && method === "GET") return true;
  if (path === "/api/v1/memory/remember" && method === "POST") return true;
  if (path === "/api/v1/memory/recall" && method === "POST") return true;
  if (/^\/api\/v1\/memory\/records\/(active|trash)$/.test(path) && method === "GET") return true;
  if (/^\/api\/v1\/memory\/operations\/[A-Za-z0-9-]{1,64}$/.test(path) && method === "DELETE") return true;
  return /^\/api\/v1\/memory\/operations\/[A-Za-z0-9-]{1,64}$/.test(path) && method === "GET"
    || /^\/api\/v1\/memory\/operations\/[A-Za-z0-9-]{1,64}\/(reconcile|restore|permanent-delete)$/.test(path) && method === "POST";
}

export async function callBackend(
  path,
  { token, method = "GET", body, query, env = process.env, fetchImpl = fetch } = {},
) {
  if (!isAllowedBackendPath(path, method)) {
    return { kind: "rejected" };
  }
  const isSuiWalletRequest = SUI_WALLET_PATH.test(path);
  let search = "";
  if (query !== undefined) {
    if (!isSuiWalletRequest || !query || typeof query !== "object" || Array.isArray(query)) {
      return { kind: "rejected" };
    }
    if (
      Object.keys(query).some((key) => !["limit", "cursor"].includes(key)) ||
      (query.limit !== undefined &&
        (!Number.isInteger(query.limit) || query.limit < 1 || query.limit > 50)) ||
      (query.cursor !== undefined &&
        (typeof query.cursor !== "string" ||
          query.cursor.length < 1 ||
          query.cursor.length > MAX_SUI_CURSOR_LENGTH ||
          query.cursor !== query.cursor.trim() ||
          /[\u0000-\u001f\u007f]/.test(query.cursor)))
    ) {
      return { kind: "rejected" };
    }
    const params = new URLSearchParams();
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    if (query.cursor !== undefined) params.set("cursor", query.cursor);
    search = params.size > 0 ? `?${params.toString()}` : "";
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
    const response = await fetchImpl(`${base}${path}${search}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(
        path === "/api/v1/memory/remember"
          ? MEMORY_WRITE_TIMEOUT_MS
          : path === "/api/v1/conversations" ||
              path.startsWith("/api/v1/conversations/")
            ? CONVERSATION_REQUEST_TIMEOUT_MS
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

export async function validateBackendVisitorSession(token, options = {}) {
  if (typeof token !== "string" || !/^orv_[A-Za-z0-9_-]{43,100}$/.test(token)) {
    return { ok: false, status: 401 };
  }
  const result = await callBackend(VISITOR_SESSION_PATH, { ...options, token });
  if (result.kind !== "response") return { ok: false, status: 503 };
  if (result.response.status === 401) return { ok: false, status: 401 };
  if (!result.response.ok) return { ok: false, status: 503 };
  try {
    const payload = await result.response.json();
    const allowedKeys = [
      "id",
      "user_id",
      "created_at",
      "credential_created_at",
      "credential_expires_at",
      "status",
    ];
    const createdAt = new Date(payload.created_at);
    const credentialCreatedAt = new Date(payload.credential_created_at);
    const expiresAt = payload.credential_expires_at === null
      ? null
      : new Date(payload.credential_expires_at);
    if (
      !payload ||
      typeof payload !== "object" ||
      Array.isArray(payload) ||
      Object.keys(payload).length !== allowedKeys.length ||
      Object.keys(payload).some((key) => !allowedKeys.includes(key)) ||
      typeof payload.id !== "string" ||
      typeof payload.user_id !== "string" ||
      !Number.isFinite(createdAt.getTime()) ||
      !Number.isFinite(credentialCreatedAt.getTime()) ||
      payload.status !== "active" ||
      (expiresAt !== null &&
        (!Number.isFinite(expiresAt.getTime()) || expiresAt.getTime() <= Date.now()))
    ) {
      return { ok: false, status: 401 };
    }
    return {
      ok: true,
      visitorIdentityId: payload.id,
      createdAt,
      credentialCreatedAt,
      credentialExpiresAt: expiresAt,
    };
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

export function validateConversationRequest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (Object.keys(value).some((key) => !["message", "conversation_id", "idempotency_key", "include_wallet_context"].includes(key))) return null;
  if (typeof value.message !== "string" || !value.message.trim() || value.message.length > 4000) return null;
  if (value.include_wallet_context !== undefined && typeof value.include_wallet_context !== "boolean") return null;
  if (typeof value.idempotency_key !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.idempotency_key)
  ) return null;
  if (value.conversation_id !== undefined && value.conversation_id !== null &&
    (typeof value.conversation_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value.conversation_id))
  ) return null;
  return {
    message: value.message.trim(),
    idempotency_key: value.idempotency_key,
    ...(value.include_wallet_context !== undefined ? { include_wallet_context: value.include_wallet_context } : {}),
    ...(value.conversation_id !== undefined ? { conversation_id: value.conversation_id } : {}),
  };
}

export function safeConversation(payload) {
  if (
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload) ||
    typeof payload.conversation_id !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(payload.conversation_id) ||
    !Number.isInteger(payload.version) ||
    payload.version < 1 ||
    !Array.isArray(payload.messages) ||
    payload.messages.length > 40
  ) return null;
  const messages = payload.messages.map((message) => ({
    id: message?.id,
    role: message?.role,
    content: message?.content,
    created_at: message?.created_at,
  }));
  if (messages.some((message) =>
    typeof message.id !== "string" ||
    !["user", "assistant"].includes(message.role) ||
    typeof message.content !== "string" ||
    message.content.length > 8000 ||
    typeof message.created_at !== "string" ||
    !Number.isFinite(Date.parse(message.created_at))
  )) return null;
  const memoryContext = payload.memory_context ?? {
    status: "not_retrieved",
    count: 0,
  };
  if (
    !memoryContext ||
    typeof memoryContext !== "object" ||
    Array.isArray(memoryContext) ||
    !["retrieved", "empty", "unavailable", "not_retrieved"].includes(memoryContext.status) ||
    !Number.isInteger(memoryContext.count) ||
    memoryContext.count < 0 ||
    memoryContext.count > 5 ||
    (memoryContext.status !== "retrieved" && memoryContext.count !== 0) ||
    (memoryContext.status === "retrieved" && memoryContext.count === 0)
  ) return null;
  return {
    conversation_id: payload.conversation_id,
    version: payload.version,
    messages,
    memory_context: {
      status: memoryContext.status,
      count: memoryContext.count,
    },
  };
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

function isSuiAddress(value) {
  return typeof value === "string" && /^0x[0-9a-fA-F]{1,64}$/.test(value);
}

export function validateSuiWalletAddress(value) {
  return isSuiAddress(value);
}

export function parseSuiPagination(searchParams) {
  const keys = [...searchParams.keys()];
  if (
    keys.some((key) => !["limit", "cursor"].includes(key)) ||
    keys.some((key, index) => keys.indexOf(key) !== index)
  ) return null;
  const query = {};
  if (searchParams.has("limit")) {
    const rawLimit = searchParams.get("limit");
    if (!rawLimit || !/^[0-9]+$/.test(rawLimit)) return null;
    const limit = Number(rawLimit);
    if (!Number.isSafeInteger(limit) || limit < 1 || limit > 50) return null;
    query.limit = limit;
  }
  if (searchParams.has("cursor")) {
    const cursor = searchParams.get("cursor");
    if (
      !cursor ||
      cursor.length > MAX_SUI_CURSOR_LENGTH ||
      cursor !== cursor.trim() ||
      /[\u0000-\u001f\u007f]/.test(cursor)
    ) return null;
    query.cursor = cursor;
  }
  return query;
}

function safeSuiPagination(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (
    typeof value.has_next_page !== "boolean" ||
    (value.next_cursor !== null && typeof value.next_cursor !== "string") ||
    (typeof value.next_cursor === "string" &&
      (value.next_cursor.length < 1 ||
        value.next_cursor.length > MAX_SUI_CURSOR_LENGTH ||
        value.next_cursor !== value.next_cursor.trim() ||
        /[\u0000-\u001f\u007f]/.test(value.next_cursor))) ||
    (value.has_next_page && typeof value.next_cursor !== "string")
  ) return null;
  return {
    next_cursor: value.has_next_page ? value.next_cursor : null,
    has_next_page: value.has_next_page,
  };
}

export function safeSuiBalances(payload) {
  if (
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload) ||
    !isSuiAddress(payload.sui_address) ||
    !["mainnet", "testnet", "devnet"].includes(payload.network) ||
    !Array.isArray(payload.balances)
  ) return null;
  const balances = payload.balances.map((balance) => {
    if (!balance || typeof balance !== "object" || Array.isArray(balance)) return null;
    if (
      typeof balance.coin_type !== "string" ||
      !balance.coin_type ||
      typeof balance.raw_balance !== "string" ||
      !/^(0|[1-9][0-9]*)$/.test(balance.raw_balance) ||
      (balance.amount !== null &&
        (typeof balance.amount !== "string" ||
          !/^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(balance.amount))) ||
      (balance.decimals !== null &&
        (!Number.isInteger(balance.decimals) || balance.decimals < 0 || balance.decimals > 255)) ||
      (balance.symbol !== null && typeof balance.symbol !== "string")
    ) return null;
    return {
      coin_type: balance.coin_type,
      raw_balance: balance.raw_balance,
      amount: balance.amount,
      decimals: balance.decimals,
      symbol: balance.symbol,
    };
  });
  if (balances.some((balance) => balance === null)) return null;
  const pagination = safeSuiPagination(payload.pagination);
  if (!pagination) return null;
  return {
    sui_address: payload.sui_address,
    network: payload.network,
    balances,
    pagination,
  };
}

export function safeSuiObjects(payload) {
  if (
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload) ||
    !isSuiAddress(payload.sui_address) ||
    !["mainnet", "testnet", "devnet"].includes(payload.network) ||
    !Array.isArray(payload.objects)
  ) return null;
  const objects = payload.objects.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    if (
      !isSuiAddress(item.object_id) ||
      (item.version !== null &&
        (typeof item.version !== "string" || !/^(0|[1-9][0-9]*)$/.test(item.version))) ||
      (item.digest !== null && typeof item.digest !== "string") ||
      (item.type !== null && typeof item.type !== "string")
    ) return null;
    return {
      object_id: item.object_id,
      version: item.version,
      digest: item.digest,
      type: item.type,
    };
  });
  if (objects.some((item) => item === null)) return null;
  const pagination = safeSuiPagination(payload.pagination);
  if (!pagination) return null;
  return {
    sui_address: payload.sui_address,
    network: payload.network,
    objects,
    pagination,
  };
}

export function safeSuiErrorResponse(status) {
  if (status === 401) {
    return { status: 401, body: { error: "unauthenticated", message: "Your session is invalid or expired." } };
  }
  if (status === 403) {
    return { status: 403, body: { error: "forbidden", message: "This request is not permitted." } };
  }
  if (status === 404) {
    return { status: 404, body: { error: "not_found", message: "The requested Sui data was not found." } };
  }
  if (status === 422) {
    return { status: 422, body: { error: "invalid_request", message: "The Sui request parameters are invalid." } };
  }
  if (status === 429) {
    return { status: 429, body: { error: "rate_limited", message: "Too many requests. Please retry shortly." } };
  }
  if (status === 502) {
    return { status: 502, body: { error: "provider_error", message: "The Sui data provider could not complete the request." } };
  }
  return { status: 503, body: { error: "provider_unavailable", message: "The Sui data provider is temporarily unavailable." } };
}

export async function safeSuiBackendResponse(response, resource) {
  if (!response.ok) return safeSuiErrorResponse(response.status);
  let payload;
  try {
    payload = await response.json();
  } catch {
    return {
      status: 502,
      body: {
        error: "invalid_backend_response",
        message: "The Sui service returned an invalid response.",
      },
    };
  }
  const safePayload = resource === "balances"
    ? safeSuiBalances(payload)
    : safeSuiObjects(payload);
  if (!safePayload) {
    return {
      status: 502,
      body: {
        error: "invalid_backend_response",
        message: "The Sui service returned an invalid response.",
      },
    };
  }
  return { status: response.status, body: safePayload };
}
