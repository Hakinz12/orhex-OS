import assert from "node:assert/strict";
import test from "node:test";
import {
  backendBaseUrl,
  callBackend,
  clearedSessionCookieOptions,
  memoryRouteAccess,
  safeErrorResponse,
  safeMemoryList,
  safeMemoryOperation,
  safeReadiness,
  safeRecall,
  sameOriginRequest,
  sessionCookieName,
  sessionCookieOptions,
  validateBackendSession,
  validateRecallBody,
  validateRememberBody,
} from "../src/lib/server/bff-core.mjs";

const validToken = `orh_${"a".repeat(43)}`;
const backendEnv = {
  ORHEX_API_BASE_URL: "http://127.0.0.1:8000",
  NODE_ENV: "test",
};

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("valid and invalid backend login validation", async () => {
  const expiresAt = new Date(Date.now() + 60_000).toISOString();
  const valid = await validateBackendSession(validToken, {
    env: backendEnv,
    fetchImpl: async (url, init) => {
      assert.equal(url, `${backendEnv.ORHEX_API_BASE_URL}/api/v1/auth/session`);
      assert.equal(init.headers.get("authorization"), ["Bearer", validToken].join(" "));
      return jsonResponse({ expires_at: expiresAt });
    },
  });
  assert.equal(valid.ok, true);
  assert.equal(valid.expiresAt.toISOString(), expiresAt);

  const invalid = await validateBackendSession("invalid", {
    env: backendEnv,
    fetchImpl: async () => {
      throw new Error("Invalid input must not call the backend");
    },
  });
  assert.deepEqual(invalid, { ok: false, status: 401 });
});

test("session cookie is HttpOnly, SameSite, secure in production, and expires with backend token", () => {
  const expiry = new Date(Date.now() + 90_000);
  const options = sessionCookieOptions(expiry, { NODE_ENV: "production" });
  assert.equal(options.httpOnly, true);
  assert.equal(options.secure, true);
  assert.equal(options.sameSite, "lax");
  assert.equal(options.path, "/");
  assert.equal(options.expires.toISOString(), expiry.toISOString());
  assert.ok(options.maxAge > 0 && options.maxAge <= 90);
  assert.equal(sessionCookieName({ NODE_ENV: "production" }), "__Host-orhrex_session");
  assert.equal(sessionCookieName({ NODE_ENV: "development" }), "orhrex_session");
  const cleared = clearedSessionCookieOptions({ NODE_ENV: "production" });
  assert.equal(cleared.maxAge, 0);
  assert.equal(cleared.httpOnly, true);
  assert.equal(cleared.secure, true);
});

test("backend 401 invalidates session while backend errors remain unavailable", async () => {
  const invalid = await validateBackendSession(validToken, {
    env: backendEnv,
    fetchImpl: async () => jsonResponse({ error: "unauthenticated" }, 401),
  });
  const unavailable = await validateBackendSession(validToken, {
    env: backendEnv,
    fetchImpl: async () => jsonResponse({ error: "failure" }, 503),
  });
  assert.deepEqual(invalid, { ok: false, status: 401 });
  assert.deepEqual(unavailable, { ok: false, status: 503 });

  const expired = await validateBackendSession(validToken, {
    env: backendEnv,
    fetchImpl: async () => jsonResponse({
      expires_at: new Date(Date.now() - 60_000).toISOString(),
    }),
  });
  assert.deepEqual(expired, { ok: false, status: 401 });

  const malformed = await validateBackendSession(validToken, {
    env: backendEnv,
    fetchImpl: async () => jsonResponse({ expires_at: "not-a-date" }),
  });
  assert.deepEqual(malformed, { ok: false, status: 401 });

  const invalidResponse = await validateBackendSession(validToken, {
    env: backendEnv,
    fetchImpl: async () => new Response("not-json", { status: 200 }),
  });
  assert.deepEqual(invalidResponse, { ok: false, status: 503 });
});

test("backend downtime and insecure production configuration fail closed", async () => {
  const result = await callBackend("/api/v1/memory/status", {
    token: validToken,
    env: { NODE_ENV: "production", ORHEX_API_BASE_URL: "http://api.example.test" },
    fetchImpl: async () => {
      throw new Error("No request expected");
    },
  });
  assert.deepEqual(result, { kind: "unavailable" });
  assert.throws(
    () => backendBaseUrl({ NODE_ENV: "production", ORHEX_API_BASE_URL: "http://api.example.test" }),
    /backend_https_required/,
  );
  assert.throws(
    () => backendBaseUrl({ NODE_ENV: "production" }),
    /backend_not_configured/,
  );
});

test("local development defaults to loopback FastAPI and validates the expected session contract", async () => {
  assert.equal(backendBaseUrl({ NODE_ENV: "development" }), "http://127.0.0.1:8000");
  assert.equal(backendBaseUrl({ NODE_ENV: "test" }), "http://127.0.0.1:8000");

  let observed;
  const result = await callBackend("/api/v1/auth/session", {
    token: validToken,
    env: { NODE_ENV: "development" },
    fetchImpl: async (url, init) => {
      observed = { url, init };
      return jsonResponse({ expires_at: new Date(Date.now() + 60_000).toISOString() });
    },
  });
  assert.equal(result.kind, "response");
  assert.equal(observed.url, "http://127.0.0.1:8000/api/v1/auth/session");
  assert.equal(observed.init.method, "GET");
  assert.equal(observed.init.headers.get("authorization"), `Bearer ${validToken}`);

  const validated = await validateBackendSession(validToken, {
    env: { NODE_ENV: "development" },
    fetchImpl: async (url, init) => {
      assert.equal(url, "http://127.0.0.1:8000/api/v1/auth/session");
      assert.equal(init.method, "GET");
      assert.equal(init.headers.get("authorization"), `Bearer ${validToken}`);
      return jsonResponse({ expires_at: new Date(Date.now() + 60_000).toISOString() });
    },
  });
  assert.equal(validated.ok, true);
});

test("legacy backend URL configuration remains supported", () => {
  assert.equal(
    backendBaseUrl({
      NODE_ENV: "production",
      ORHREX_API_BASE_URL: "https://api.example.test",
    }),
    "https://api.example.test",
  );
  assert.equal(
    backendBaseUrl({
      NODE_ENV: "production",
      ORHEX_API_BASE_URL: "https://new-api.example.test",
      ORHREX_API_BASE_URL: "https://legacy-api.example.test",
    }),
    "https://new-api.example.test",
  );
});

test("only approved backend paths can be called", async () => {
  let called = false;
  const result = await callBackend("https://attacker.example", {
    token: validToken,
    env: backendEnv,
    fetchImpl: async () => {
      called = true;
      return jsonResponse({});
    },
  });
  assert.deepEqual(result, { kind: "rejected" });
  assert.equal(called, false);
});

test("authenticated BFF forwarding supports real memory save and recall contracts", async () => {
  const requests = [];
  const idempotencyKey = "00000000-0000-4000-8000-000000000001";
  const fetchImpl = async (url, init) => {
    requests.push({ url, init });
    return jsonResponse(url.endsWith("/remember")
      ? { status: "confirmed", operation_id: "op-1", namespace: "private" }
      : { query: "saved context", total: 0, results: [], namespace: "private" }, url.endsWith("/remember") ? 201 : 200);
  };

  const saved = await callBackend("/api/v1/memory/remember", {
    token: validToken,
    method: "POST",
    body: { text: "A real saved fact", idempotency_key: idempotencyKey },
    env: backendEnv,
    fetchImpl,
  });
  const recalled = await callBackend("/api/v1/memory/recall", {
    token: validToken,
    method: "POST",
    body: { query: "saved context" },
    env: backendEnv,
    fetchImpl,
  });
  assert.equal(saved.kind, "response");
  assert.equal(saved.response.status, 201);
  assert.equal(recalled.kind, "response");
  assert.equal(requests.length, 2);
  for (const { init } of requests) {
    assert.equal(init.headers.get("authorization"), `Bearer ${validToken}`);
    assert.equal(init.cache, "no-store");
    assert.equal(init.redirect, "error");
  }
  assert.deepEqual(JSON.parse(requests[0].init.body), {
    text: "A real saved fact",
    idempotency_key: idempotencyKey,
  });
  assert.deepEqual(JSON.parse(requests[1].init.body), { query: "saved context" });
});

test("memory save forwarding waits long enough to return pending SDK operations", async () => {
  const originalTimeout = AbortSignal.timeout;
  let requestedTimeout;
  AbortSignal.timeout = (milliseconds) => {
    requestedTimeout = milliseconds;
    return originalTimeout(Math.ceil(milliseconds / 1000));
  };

  try {
    const result = await callBackend("/api/v1/memory/remember", {
      token: validToken,
      method: "POST",
      body: { text: "A delayed but accepted write", idempotency_key: "00000000-0000-4000-8000-000000000001" },
      env: backendEnv,
      fetchImpl: (_url, { signal }) => new Promise((resolve, reject) => {
        const timer = setTimeout(
          () => resolve(jsonResponse({ status: "reconciliation_required", operation_id: "operation-123" }, 202)),
          25,
        );
        signal.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(signal.reason);
        }, { once: true });
      }),
    });

    assert.equal(requestedTimeout, 160_000);
    assert.equal(result.kind, "response");
    assert.equal(result.response.status, 202);
    assert.deepEqual(await result.response.json(), {
      status: "reconciliation_required",
      operation_id: "operation-123",
    });

    await callBackend("/api/v1/memory/status", {
      token: validToken,
      env: backendEnv,
      fetchImpl: async () => jsonResponse({ status: "ready" }),
    });
    assert.equal(requestedTimeout, 8_000);
  } finally {
    AbortSignal.timeout = originalTimeout;
  }
});

test("backend failure diagnostics include only a sanitized error type", async () => {
  const originalWarn = console.warn;
  const warnings = [];
  console.warn = (...args) => warnings.push(args);

  try {
    const result = await callBackend("/api/v1/memory/status", {
      token: validToken,
      env: backendEnv,
      fetchImpl: async () => {
        throw new TypeError("private memory text and credentials");
      },
    });

    assert.deepEqual(result, { kind: "unavailable" });
    assert.deepEqual(warnings, [["Backend request failed: %s", "TypeError"]]);
  } finally {
    console.warn = originalWarn;
  }
});

test("authenticated operation lookup and reconciliation use only approved routes", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse({ status: "pending", operation_id: "operation-123" });
  };
  const lookup = await callBackend("/api/v1/memory/operations/operation-123", {
    token: validToken,
    env: backendEnv,
    fetchImpl,
  });
  const reconcile = await callBackend("/api/v1/memory/operations/operation-123/reconcile", {
    token: validToken,
    method: "POST",
    env: backendEnv,
    fetchImpl,
  });
  assert.equal(lookup.kind, "response");
  assert.equal(reconcile.kind, "response");
  assert.deepEqual(calls.map(({ url, init }) => [url, init.method, init.headers.get("authorization")]), [
    [`${backendEnv.ORHEX_API_BASE_URL}/api/v1/memory/operations/operation-123`, "GET", `Bearer ${validToken}`],
    [`${backendEnv.ORHEX_API_BASE_URL}/api/v1/memory/operations/operation-123/reconcile`, "POST", `Bearer ${validToken}`],
  ]);
});

test("authenticated BFF forwarding covers owner-scoped Trash actions only", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return jsonResponse({
      status: "confirmed",
      operation_id: "operation-123",
      trashed_at: "2026-10-29T18:00:00+00:00",
    });
  };
  const operations = [
    ["/api/v1/memory/records/active", "GET"],
    ["/api/v1/memory/records/trash", "GET"],
    ["/api/v1/memory/operations/operation-123", "DELETE"],
    ["/api/v1/memory/operations/operation-123/restore", "POST"],
    ["/api/v1/memory/operations/operation-123/permanent-delete", "POST"],
  ];
  for (const [path, method] of operations) {
    const result = await callBackend(path, {
      token: validToken,
      method,
      env: backendEnv,
      fetchImpl,
    });
    assert.equal(result.kind, "response");
  }
  assert.equal(calls.length, operations.length);
  for (let index = 0; index < calls.length; index += 1) {
    const { url, init } = calls[index];
    assert.equal(url, `${backendEnv.ORHEX_API_BASE_URL}${operations[index][0]}`);
    assert.equal(init.method, operations[index][1]);
    assert.equal(init.headers.get("authorization")?.endsWith(validToken.slice(-8)), true);
    assert.equal(init.body, undefined);
  }
  const arbitrary = await callBackend(
    "/api/v1/memory/operations/operation-123/permanent-delete-all",
    { token: validToken, method: "POST", env: backendEnv, fetchImpl },
  );
  assert.deepEqual(arbitrary, { kind: "rejected" });
});

test("cross-origin state-changing requests are rejected", () => {
  const same = new Request("https://app.example/api/bff/memory/remember", {
    method: "POST",
    headers: { origin: "https://app.example" },
  });
  const cross = new Request("https://app.example/api/bff/memory/remember", {
    method: "POST",
    headers: { origin: "https://attacker.example" },
  });
  const missing = new Request("https://app.example/api/bff/memory/remember", { method: "POST" });
  assert.equal(sameOriginRequest(same), true);
  assert.equal(sameOriginRequest(cross), false);
  assert.equal(sameOriginRequest(missing), false);

  const normalizedRequestUrl = new Request("http://localhost:3199/api/session", {
    method: "POST",
    headers: {
      host: "127.0.0.1:3199",
      origin: "http://127.0.0.1:3199",
    },
  });
  assert.equal(sameOriginRequest(normalizedRequestUrl), true);

  const spoofedHost = new Request("http://localhost:3199/api/session", {
    method: "POST",
    headers: {
      host: "127.0.0.1:3199",
      origin: "http://attacker.example",
    },
  });
  assert.equal(sameOriginRequest(spoofedHost), false);
});

test("memory BFF route access rejects unauthenticated and cross-origin requests", () => {
  const sameOrigin = new Request("https://app.example/api/bff/memory/operations/id/restore", {
    method: "POST",
    headers: { origin: "https://app.example" },
  });
  const crossOrigin = new Request("https://app.example/api/bff/memory/operations/id/restore", {
    method: "POST",
    headers: { origin: "https://attacker.example" },
  });
  const read = new Request("https://app.example/api/bff/memory/records/trash");

  assert.deepEqual(memoryRouteAccess(read, null), { ok: false, status: 401 });
  assert.deepEqual(memoryRouteAccess(sameOrigin, null, true), { ok: false, status: 401 });
  assert.deepEqual(memoryRouteAccess(crossOrigin, validToken, true), { ok: false, status: 403 });
  assert.deepEqual(memoryRouteAccess(sameOrigin, validToken, true), {
    ok: true,
    token: validToken,
  });
});

test("memory bodies reject extra identity fields and invalid limits", () => {
  assert.equal(validateRememberBody({ text: "A useful memory", user_id: "other" }), null);
  assert.equal(validateRememberBody({ text: "A useful memory", namespace: "other" }), null);
  assert.deepEqual(validateRememberBody({ text: " A useful memory " }), { text: "A useful memory" });
  assert.deepEqual(
    validateRememberBody({
      text: "A useful memory",
      idempotency_key: "00000000-0000-4000-8000-000000000001",
    }),
    {
      text: "A useful memory",
      idempotency_key: "00000000-0000-4000-8000-000000000001",
    },
  );
  assert.equal(validateRecallBody({ query: "useful context", limit: 21 }), null);
  assert.equal(validateRecallBody({ query: "useful context", user_id: "other" }), null);
  assert.deepEqual(validateRecallBody({ query: " useful context ", limit: 5 }), { query: "useful context", limit: 5 });
});

test("backend responses are reduced to approved memory fields", () => {
  assert.deepEqual(
    safeReadiness({ status: "ready", write_ready: true, private_key: "not-forwarded" }),
    { status: "ready", write_ready: true },
  );
  assert.deepEqual(
    safeMemoryOperation({ status: "confirmed", operation_id: "op-1", namespace: "hidden", user_id: "hidden" }),
    { status: "confirmed", operation_id: "op-1" },
  );
  assert.deepEqual(
    safeMemoryList({
      folder: "trash",
      total: 1,
      memories: [{
        operation_id: "op-1",
        text: "A private memory",
        status: "confirmed",
        blob_id: "blob-1",
        category: "personal",
        memory_type: "semantic",
        created_at: "2026-10-29T18:00:00+00:00",
        user_id: "must-not-forward",
      }],
    }),
    {
      folder: "trash",
      total: 1,
      memories: [{
        operation_id: "op-1",
        text: "A private memory",
        status: "confirmed",
        blob_id: "blob-1",
        category: "personal",
        memory_type: "semantic",
        created_at: "2026-10-29T18:00:00+00:00",
        confirmed_at: undefined,
        trashed_at: undefined,
        permanent_delete_started_at: undefined,
      }],
    },
  );
  assert.equal(safeMemoryList({ folder: "trash", total: 1, memories: [] }), null);
  assert.deepEqual(
    safeRecall({
      query: "context",
      total: 1,
      namespace: "hidden",
      results: [{ blob_id: "blob-1", text: "Real memory", distance: 0.1, namespace: "hidden" }],
    }),
    { query: "context", total: 1, results: [{ blob_id: "blob-1", text: "Real memory", distance: 0.1 }] },
  );
  assert.equal(safeErrorResponse(503).body.message, "The memory service is temporarily unavailable.");
});
