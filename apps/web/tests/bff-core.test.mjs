import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import test from "node:test";
import ts from "typescript";
import {
  backendBaseUrl,
  callBackend,
  clearedSessionCookieOptions,
  clearedVisitorCookieOptions,
  memoryRouteAccess,
  safeErrorResponse,
  safeMemoryList,
  safeMemoryOperation,
  safeReadiness,
  safeRecall,
  parseSuiPagination,
  safeSuiBackendResponse,
  safeSuiBalances,
  safeSuiObjects,
  sameOriginRequest,
  sessionCookieName,
  sessionCookieOptions,
  visitorCookieName,
  visitorCookieOptions,
  validateSuiWalletAddress,
  validateBackendSession,
  validateBackendVisitorSession,
  validateRecallBody,
  validateConversationRequest,
  safeConversation,
  validateRememberBody,
} from "../src/lib/server/bff-core.mjs";

const validToken = `orh_${"a".repeat(43)}`;
const validVisitorCredential = `orv_${"v".repeat(43)}`;
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

test("visitor session client sends a bodyless POST to the BFF", async () => {
  const source = await readFile(new URL("../src/lib/api.ts", import.meta.url), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const client = await import(
    `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
  );
  const originalFetch = globalThis.fetch;
  let observed;
  globalThis.fetch = async (input, init) => {
    observed = { input: String(input), init };
    return jsonResponse({ authenticated: true, kind: "visitor" });
  };
  try {
    await client.bootstrapVisitorSession();
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(observed.input, "/api/session/visitor");
  assert.equal(observed.init.method, "POST");
  assert.equal(observed.init.body, undefined);
  assert.equal(observed.init.headers.Accept, "application/json");
  assert.equal("Content-Type" in observed.init.headers, false);
});

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const { port } = server.address();
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
  return port;
}

async function waitForRoute(url, child) {
  for (let attempt = 0; attempt < 600; attempt += 1) {
    if (child.exitCode !== null) {
      throw new Error("Next.js test server exited before becoming ready.");
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(2_000) });
      if (response.status === 401) return;
    } catch {
      // The server is still starting or compiling the route.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Next.js test server did not become ready.");
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

test("visitor cookie is separate, host-only, HttpOnly, SameSite=Lax, and secure in production", () => {
  const options = visitorCookieOptions({ NODE_ENV: "production" });
  assert.equal(visitorCookieName({ NODE_ENV: "production" }), "__Host-orhrex_visitor_session");
  assert.equal(visitorCookieName({ NODE_ENV: "development" }), "orhrex_visitor_session");
  assert.equal(options.httpOnly, true);
  assert.equal(options.secure, true);
  assert.equal(options.sameSite, "lax");
  assert.equal(options.path, "/");
  assert.equal("domain" in options, false);
  assert.equal(options.maxAge, 31_536_000);
  assert.ok(options.expires.getTime() > Date.now());
  const development = visitorCookieOptions({ NODE_ENV: "development" });
  assert.equal(development.secure, false);
  assert.equal(development.httpOnly, true);
  assert.equal(clearedVisitorCookieOptions({ NODE_ENV: "production" }).maxAge, 0);
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

  test("server-side default fetch sends authenticated BFF requests without an injected client", async () => {
    let observedAuthorization = null;
    const server = createServer((request, response) => {
      observedAuthorization = request.headers.authorization;
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ status: "ready" }));
    });
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
    try {
      const port = server.address().port;
      const result = await callBackend("/api/v1/memory/status", {
        token: validToken,
        env: { ...backendEnv, ORHEX_API_BASE_URL: `http://127.0.0.1:${port}` },
      });
      assert.equal(result.kind, "response");
      assert.equal(observedAuthorization, ["Bearer", validToken].join(" "));
      assert.equal(result.response.headers.get("content-type"), "application/json");
    } finally {
      await new Promise((resolve, reject) => {
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }
  });

  test("conversation BFF validates requests, forwards the session credential, and sanitizes responses", async () => {
    const conversationId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const idempotencyKey = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    const createdAt = new Date().toISOString();
    const validBody = validateConversationRequest({
      message: "  Hello ORHEX  ",
      idempotency_key: idempotencyKey,
      include_wallet_context: true,
    });
    assert.deepEqual(validBody, {
      message: "Hello ORHEX",
      idempotency_key: idempotencyKey,
      include_wallet_context: true,
    });
    assert.deepEqual(validateConversationRequest({
      message: "Hello ORHEX",
      idempotency_key: idempotencyKey,
    }), {
      message: "Hello ORHEX",
      idempotency_key: idempotencyKey,
    });
    assert.equal(validateConversationRequest({
      message: "Check my wallet",
      idempotency_key: idempotencyKey,
      include_wallet_context: "true",
    }), null);
    assert.equal(validateConversationRequest({
      message: "Hi",
      idempotency_key: idempotencyKey,
      user_id: "attacker-controlled",
    }), null);
    assert.equal(validateConversationRequest({
      message: "  ",
      idempotency_key: idempotencyKey,
    }), null);

    let observed;
    const forwarded = await callBackend("/api/v1/conversations", {
      token: validVisitorCredential,
      method: "POST",
      body: validBody,
      env: backendEnv,
      fetchImpl: async (url, init) => {
        observed = { url, init };
        return jsonResponse({
          conversation_id: conversationId,
          version: 1,
          messages: [{
            id: "message-1",
            role: "assistant",
            content: "Hello.",
            created_at: createdAt,
            internal_user_id: "must-not-reach-browser",
          }],
          memory_context: {
            status: "retrieved",
            count: 1,
            memory_id: "must-not-reach-browser",
            namespace: "must-not-reach-browser",
          },
          internal_user_id: "must-not-reach-browser",
        });
      },
    });
    assert.equal(forwarded.kind, "response");
    assert.equal(observed.url, `${backendEnv.ORHEX_API_BASE_URL}/api/v1/conversations`);
    assert.equal(observed.init.method, "POST");
    assert.equal(
      observed.init.headers.get("authorization"),
      `Bearer ${validVisitorCredential}`,
    );
    assert.equal(JSON.parse(observed.init.body).message, "Hello ORHEX");
    assert.equal(JSON.parse(observed.init.body).include_wallet_context, true);

    const sanitized = safeConversation(await forwarded.response.json());
    assert.deepEqual(sanitized, {
      conversation_id: conversationId,
      version: 1,
      messages: [{
        id: "message-1",
        role: "assistant",
        content: "Hello.",
        created_at: createdAt,
      }],
      memory_context: { status: "retrieved", count: 1 },
    });
    const rejectedPath = await callBackend("/api/v1/conversations/not-a-uuid", {
        token: validToken,
        method: "GET",
        env: backendEnv,
        fetchImpl: async () => {
          throw new Error("Unapproved conversation path must not be sent");
        },
      });
    assert.equal(rejectedPath.kind, "rejected");
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

test("visitor session validation strips internal user data and never returns credentials", async () => {
  const createdAt = new Date(Date.now() - 60_000).toISOString();
  const credentialCreatedAt = new Date(Date.now() - 30_000).toISOString();
  const valid = await validateBackendVisitorSession(validVisitorCredential, {
    env: backendEnv,
    fetchImpl: async (url, init) => {
      assert.equal(url, `${backendEnv.ORHEX_API_BASE_URL}/api/v1/visitor/session`);
      assert.equal(init.headers.get("authorization"), `Bearer ${validVisitorCredential}`);
      return jsonResponse({
        id: "visitor-123",
        user_id: "internal-user-456",
        created_at: createdAt,
        credential_created_at: credentialCreatedAt,
        credential_expires_at: null,
        status: "active",
      });
    },
  });
  assert.deepEqual(valid, {
    ok: true,
    visitorIdentityId: "visitor-123",
    createdAt: new Date(createdAt),
    credentialCreatedAt: new Date(credentialCreatedAt),
    credentialExpiresAt: null,
  });
  assert.equal("user_id" in valid, false);
  assert.equal("credential" in valid, false);

  const invalid = await validateBackendVisitorSession("orv_bad", {
    env: backendEnv,
    fetchImpl: async () => {
      throw new Error("Invalid visitor credential must not reach backend");
    },
  });
  assert.deepEqual(invalid, { ok: false, status: 401 });
});

test("visitor lifecycle allowlist accepts only approved methods and paths", async () => {
  const paths = [
    ["/api/v1/visitor/bootstrap", "POST", undefined],
    ["/api/v1/visitor/session", "GET", validVisitorCredential],
    ["/api/v1/visitor/session/rotate", "POST", validVisitorCredential],
    ["/api/v1/visitor/session/reset", "POST", validVisitorCredential],
    ["/api/v1/visitor/session", "DELETE", validVisitorCredential],
  ];
  const calls = [];
  for (const [path, method, token] of paths) {
    const result = await callBackend(path, {
      token,
      method,
      env: backendEnv,
      fetchImpl: async (url, init) => {
        calls.push({ url, init });
        return jsonResponse({});
      },
    });
    assert.equal(result.kind, "response");
  }
  assert.equal(calls.length, paths.length);
  assert.equal(calls[0].init.headers.has("authorization"), false);
  for (const call of calls.slice(1)) {
    assert.equal(call.init.headers.get("authorization"), `Bearer ${validVisitorCredential}`);
  }
  for (const [path, method] of [
    ["/api/v1/visitor/bootstrap", "GET"],
    ["/api/v1/visitor/session/reset-all", "POST"],
    ["/api/v1/visitor/session/rotate", "GET"],
  ]) {
    const result = await callBackend(path, { method, env: backendEnv });
    assert.deepEqual(result, { kind: "rejected" });
  }
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

test("orchestration BFF allowlist forwards only authenticated decision-cycle endpoints", async () => {
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url, init });
    return jsonResponse({ ok: true });
  };
  const run = await callBackend("/api/v1/orchestration/run", {
    token: validToken, method: "POST", body: { idempotency_key: "cycle-key" },
    env: backendEnv, fetchImpl,
  });
  const latest = await callBackend("/api/v1/orchestration/latest", {
    token: validToken, method: "GET", env: backendEnv, fetchImpl,
  });
  const forbidden = await callBackend("/api/v1/orchestration/run", {
    token: validToken, method: "GET", env: backendEnv, fetchImpl,
  });
  assert.equal(run.kind, "response");
  assert.equal(latest.kind, "response");
  assert.deepEqual(forbidden, { kind: "rejected" });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].init.method, "POST");
  assert.equal(requests[1].init.method, "GET");
  assert.equal(requests[0].init.headers.get("authorization"), `Bearer ${validToken}`);
});

test("Sui BFF allowlist forwards only approved read endpoints with the session bearer", async () => {
  const address = `0x${"a".repeat(64)}`;
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url, init });
    return jsonResponse({});
  };
  const balancePath = `/api/v1/sui/wallet/${address}/balances`;
  const objectsPath = `/api/v1/sui/wallet/${address}/objects`;
  const intelligencePath = `/api/v1/sui/intelligence/${address}`;

  const balances = await callBackend(balancePath, {
    token: validToken,
    method: "GET",
    query: { limit: 7, cursor: "opaque/cursor-1" },
    env: backendEnv,
    fetchImpl,
  });
  const objects = await callBackend(objectsPath, {
    token: validToken,
    method: "GET",
    query: { limit: 20 },
    env: backendEnv,
    fetchImpl,
  });
  const intelligence = await callBackend(intelligencePath, {
    token: validToken,
    method: "GET",
    env: backendEnv,
    fetchImpl,
  });

  assert.equal(balances.kind, "response");
  assert.equal(objects.kind, "response");
  assert.equal(intelligence.kind, "response");
  assert.equal(requests[0].url, `${backendEnv.ORHEX_API_BASE_URL}${balancePath}?limit=7&cursor=opaque%2Fcursor-1`);
  assert.equal(requests[1].url, `${backendEnv.ORHEX_API_BASE_URL}${objectsPath}?limit=20`);
  assert.equal(requests[2].url, `${backendEnv.ORHEX_API_BASE_URL}${intelligencePath}`);
  for (const { init } of requests) {
    assert.equal(init.method, "GET");
    assert.equal(init.headers.get("authorization"), ["Bearer", validToken].join(" "));
    assert.equal(init.cache, "no-store");
    assert.equal(init.redirect, "error");
  }
  assert.ok(requests[2].init.signal instanceof AbortSignal);
});

test("Sui BFF rejects malformed addresses, query parameters, and arbitrary upstream paths", async () => {
  const unauthenticated = new Request("https://app.example/api/sui/wallet/0x2/balances");
  assert.deepEqual(memoryRouteAccess(unauthenticated, null), { ok: false, status: 401 });

  assert.equal(validateSuiWalletAddress("0x2"), true);
  assert.equal(validateSuiWalletAddress(`0x${"b".repeat(64)}`), true);
  assert.equal(validateSuiWalletAddress("0x"), false);
  assert.equal(validateSuiWalletAddress("0x12xz"), false);
  assert.deepEqual(parseSuiPagination(new URLSearchParams("limit=5&cursor=opaque%2Fnext")), {
    limit: 5,
    cursor: "opaque/next",
  });
  assert.equal(parseSuiPagination(new URLSearchParams("limit=51")), null);
  assert.equal(parseSuiPagination(new URLSearchParams("limit=2&other=x")), null);
  assert.equal(parseSuiPagination(new URLSearchParams("cursor=a&cursor=b")), null);

  let called = false;
  for (const path of [
    "/api/v1/sui/network",
    "/api/v1/sui/wallet/0x2/transaction",
    "/api/v1/sui/wallet/0x2/balances/extra",
    "/api/v1/sui/intelligence/0x2/extra",
    "/api/v1/sui/wallet/0x2%2F..%2Fauth/session/balances",
  ]) {
    const result = await callBackend(path, {
      token: validToken,
      method: "GET",
      env: backendEnv,
      fetchImpl: async () => {
        called = true;
        return jsonResponse({});
      },
    });
    assert.deepEqual(result, { kind: "rejected" });
  }
  const arbitraryQuery = await callBackend("/api/v1/sui/wallet/0x2/objects", {
    token: validToken,
    method: "GET",
    query: { limit: 5, upstream: "https://attacker.example" },
    env: backendEnv,
    fetchImpl: async () => {
      called = true;
      return jsonResponse({});
    },
  });
  assert.deepEqual(arbitraryQuery, { kind: "rejected" });
  assert.equal(called, false);
});

test("Sui BFF sanitizes balance and object payloads and drops sensitive fields", () => {
  const balances = safeSuiBalances({
    sui_address: "0x2",
    network: "mainnet",
    balances: [{
      coin_type: "0x2::sui::SUI",
      raw_balance: "1500000000",
      amount: "1.5",
      decimals: 9,
      symbol: "SUI",
      private_key: "must-not-forward",
    }],
    pagination: { next_cursor: null, has_next_page: false },
    access_token: validToken,
  });
  assert.deepEqual(balances, {
    sui_address: "0x2",
    network: "mainnet",
    balances: [{
      coin_type: "0x2::sui::SUI",
      raw_balance: "1500000000",
      amount: "1.5",
      decimals: 9,
      symbol: "SUI",
    }],
    pagination: { next_cursor: null, has_next_page: false },
  });

  const objects = safeSuiObjects({
    sui_address: "0x2",
    network: "mainnet",
    objects: [{
      object_id: "0x3",
      version: "12",
      digest: "1".repeat(32),
      type: "0x2::example::Item",
      api_key: "must-not-forward",
    }],
    pagination: { next_cursor: "opaque-next", has_next_page: true },
    provider_error: "must-not-forward",
  });
  assert.deepEqual(objects, {
    sui_address: "0x2",
    network: "mainnet",
    objects: [{
      object_id: "0x3",
      version: "12",
      digest: "1".repeat(32),
      type: "0x2::example::Item",
    }],
    pagination: { next_cursor: "opaque-next", has_next_page: true },
  });
});

test("Sui BFF maps upstream failures and network errors without returning secrets", async () => {
  for (const [upstreamStatus, expectedStatus, expectedError] of [
    [503, 503, "provider_unavailable"],
    [502, 502, "provider_error"],
  ]) {
    const safe = await safeSuiBackendResponse(
      jsonResponse({
        error: "provider_error",
        message: "provider response contains credentials",
        api_key: "tatum-secret",
        token: validToken,
      }, upstreamStatus),
      "balances",
    );
    assert.equal(safe.status, expectedStatus);
    assert.equal(safe.body.error, expectedError);
    assert.equal(JSON.stringify(safe).includes("tatum-secret"), false);
    assert.equal(JSON.stringify(safe).includes(validToken), false);
  }

  const failed = await callBackend("/api/v1/sui/wallet/0x2/objects", {
    token: validToken,
    env: backendEnv,
    fetchImpl: async () => {
      throw new TypeError("private token and upstream details");
    },
  });
  assert.deepEqual(failed, { kind: "unavailable" });
});

test("public visitor session and Sui routes preserve cookie and authentication boundaries", { timeout: 120_000 }, async () => {
  const patCredential = `orh_${"p".repeat(43)}`;
  const visitorCredentials = [
    `orv_${"v".repeat(43)}`,
    `orv_${"w".repeat(43)}`,
    `orv_${"x".repeat(43)}`,
    `orv_${"y".repeat(43)}`,
    `orv_${"z".repeat(43)}`,
  ];
  const bootstrapCredentials = [
    visitorCredentials[0],
    visitorCredentials[3],
    visitorCredentials[4],
  ];
  const forwardedKinds = [];
  const activeVisitors = new Map();
  const backendRequests = [];
  let bootstrapCalls = 0;
  let failBootstrap = false;
  let failSessionValidation = false;
  let delayBootstrap = false;
  const backend = createServer(async (request, response) => {
    const authorization = request.headers.authorization;
    if (request.url.startsWith("/api/v1/sui/")) {
      const currentVisitorCredential = visitorCredentials[3];
      if (authorization === `Bearer ${patCredential}`) forwardedKinds.push("pat");
      else if (authorization === `Bearer ${currentVisitorCredential}`) forwardedKinds.push("visitor");
      else forwardedKinds.push("other");
    }

    if (request.url === "/api/v1/visitor/bootstrap") {
      bootstrapCalls += 1;
      backendRequests.push({
        path: request.url,
        method: request.method,
        hasAuthorization: Boolean(authorization),
        hasContentType: Boolean(request.headers["content-type"]),
      });
      if (delayBootstrap) await new Promise((resolve) => setTimeout(resolve, 50));
      if (failBootstrap) {
        response.writeHead(503, { "content-type": "application/json" });
        response.end(JSON.stringify({ message: "provider secret must not escape" }));
        return;
      }
      const visitorCredential = bootstrapCredentials[bootstrapCalls - 1];
      const identityId = `visitor-test-id-${bootstrapCalls}`;
      activeVisitors.set(visitorCredential, { identityId, active: true });
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ visitor_credential: visitorCredential }));
      return;
    }
    if (request.url === "/api/v1/visitor/session" && request.method === "GET") {
      if (failSessionValidation) {
        response.writeHead(503, { "content-type": "application/json" });
        response.end(JSON.stringify({ message: "provider secret must not escape" }));
        return;
      }
      const token = authorization?.replace(/^Bearer /, "");
      const visitor = activeVisitors.get(token);
      if (!visitor?.active) {
        response.writeHead(401, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "unauthenticated" }));
        return;
      }
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({
        id: visitor.identityId,
        user_id: "internal-test-user-id",
        created_at: "2026-01-01T00:00:00Z",
        credential_created_at: "2026-01-01T00:00:00Z",
        credential_expires_at: null,
        status: "active",
      }));
      return;
    }
    if (request.url === "/api/v1/visitor/session/rotate") {
      const token = authorization?.replace(/^Bearer /, "");
      const current = activeVisitors.get(token);
      if (!current?.active) {
        response.writeHead(401, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "unauthenticated" }));
        return;
      }
      current.active = false;
      const replacement = visitorCredentials[1];
      activeVisitors.set(replacement, { identityId: current.identityId, active: true });
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ visitor_credential: replacement }));
      return;
    }
    if (request.url === "/api/v1/visitor/session/reset") {
      const token = authorization?.replace(/^Bearer /, "");
      const current = activeVisitors.get(token);
      if (!current?.active) {
        response.writeHead(401, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "unauthenticated" }));
        return;
      }
      current.active = false;
      const replacement = visitorCredentials[2];
      activeVisitors.set(replacement, { identityId: "visitor-test-id-reset", active: true });
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({
        visitor_identity_id: "visitor-test-id-reset",
        visitor_credential: replacement,
      }));
      return;
    }
    if (request.url === "/api/v1/visitor/session" && request.method === "DELETE") {
      const token = authorization?.replace(/^Bearer /, "");
      const current = activeVisitors.get(token);
      if (!current?.active) {
        response.writeHead(401, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "unauthenticated" }));
        return;
      }
      current.active = false;
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ status: "revoked" }));
      return;
    }
    if (request.url === "/api/v1/auth/session") {
      if (authorization === `Bearer ${patCredential}`) {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({
          expires_at: new Date(Date.now() + 60_000).toISOString(),
        }));
      } else {
        response.writeHead(401, { "content-type": "application/json" });
        response.end(JSON.stringify({ error: "unauthenticated" }));
      }
      return;
    }

    const payload = request.url.endsWith("/balances")
      ? {
          sui_address: `0x${"a".repeat(64)}`,
          network: "mainnet",
          balances: [],
          pagination: { next_cursor: null, has_next_page: false },
        }
      : {
          sui_address: `0x${"a".repeat(64)}`,
          network: "mainnet",
          objects: [],
          pagination: { next_cursor: null, has_next_page: false },
        };
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(payload));
  });
  await new Promise((resolve, reject) => {
    backend.once("error", reject);
    backend.listen(0, "127.0.0.1", resolve);
  });

  const backendPort = backend.address().port;
  const nextPort = await availablePort();
  const origin = `http://127.0.0.1:${nextPort}`;
  const child = spawn(
    process.execPath,
    [
      "node_modules/next/dist/bin/next",
      "dev",
      "--hostname",
      "127.0.0.1",
      "--port",
      String(nextPort),
    ],
    {
      cwd: process.cwd(),
      env: {
        ...process.env,
        NODE_ENV: "development",
        ORHEX_API_BASE_URL: `http://127.0.0.1:${backendPort}`,
        ORHEX_APP_ORIGIN: `http://127.0.0.1:${nextPort}`,
        NEXT_TELEMETRY_DISABLED: "1",
      },
      stdio: "ignore",
    },
  );

  try {
    await waitForRoute(
      `${origin}/api/sui/wallet/${"0x" + "a".repeat(64)}/balances`,
      child,
    );

    const bootstrapResponse = await fetch(`${origin}/api/session/visitor`, {
      method: "POST",
      headers: { Origin: origin },
    });
    const bootstrapBody = await bootstrapResponse.text();
    assert.equal(
      bootstrapResponse.status,
      200,
      "first-time visitor bootstrap should succeed without a request body",
    );
    const firstVisitorCredential = visitorCredentials[0];
    assert.equal(bootstrapBody.includes(firstVisitorCredential), false);
    assert.equal(bootstrapBody.includes("internal-test-user-id"), false);
    const setCookie = bootstrapResponse.headers.get("set-cookie") ?? "";
    assert.equal(setCookie.startsWith("orhrex_visitor_session="), true);
    assert.equal(setCookie.includes("HttpOnly"), true);
    assert.equal(setCookie.includes("Path=/"), true);
    const visitorCookie = setCookie.split(";", 1)[0];
    assert.equal(visitorCookie.endsWith(firstVisitorCredential), true);
    assert.equal(bootstrapCalls, 1);
    assert.deepEqual(backendRequests[0], {
      path: "/api/v1/visitor/bootstrap",
      method: "POST",
      hasAuthorization: false,
      hasContentType: false,
    });

    const invalidBody = await fetch(`${origin}/api/session/visitor`, {
      method: "POST",
      headers: { Origin: origin, "Content-Type": "application/json" },
      body: "{}",
    });
    assert.equal(invalidBody.status, 400);
    assert.equal(bootstrapCalls, 1);

    const reloadResponse = await fetch(`${origin}/api/session`, {
      headers: { Cookie: visitorCookie },
    });
    const reloadBody = await reloadResponse.text();
    assert.equal(reloadResponse.status, 200);
    assert.equal(reloadBody.includes('"kind":"visitor"'), true);
    assert.equal(reloadBody.includes(firstVisitorCredential), false);

    const existingCookieInitialization = await fetch(`${origin}/api/session/visitor`, {
      method: "POST",
      headers: { Origin: origin, Cookie: visitorCookie },
    });
    assert.equal(existingCookieInitialization.status, 200);
    assert.equal(existingCookieInitialization.headers.has("set-cookie"), false);
    assert.equal(bootstrapCalls, 1);

    failSessionValidation = true;
    const unavailableSession = await fetch(`${origin}/api/session`, {
      headers: { Cookie: visitorCookie },
    });
    failSessionValidation = false;
    const unavailableBody = await unavailableSession.text();
    assert.equal(unavailableSession.status, 503);
    assert.equal(unavailableBody.includes("provider secret"), false);
    assert.equal(unavailableBody.includes(firstVisitorCredential), false);
    assert.equal(bootstrapCalls, 1);

    const patSession = await fetch(`${origin}/api/session`, {
      headers: { Cookie: `orhrex_session=${patCredential}` },
    });
    const patSessionBody = await patSession.text();
    assert.equal(patSession.status, 200);
    assert.equal(patSessionBody.includes('"kind":"pat"'), true);
    assert.equal(bootstrapCalls, 1);

    const rotated = await fetch(`${origin}/api/session/visitor`, {
      method: "PATCH",
      headers: { Origin: origin, Cookie: visitorCookie },
    });
    assert.equal(rotated.status, 200);
    const rotatedBody = await rotated.text();
    assert.equal(rotatedBody.includes(visitorCredentials[1]), false);
    const rotatedCookie = (rotated.headers.get("set-cookie") ?? "").split(";", 1)[0];
    assert.equal(rotatedCookie.endsWith(visitorCredentials[1]), true);
    assert.equal(rotated.headers.get("set-cookie")?.includes("HttpOnly"), true);

    const reset = await fetch(`${origin}/api/session/visitor`, {
      method: "PUT",
      headers: { Origin: origin, Cookie: rotatedCookie },
    });
    assert.equal(reset.status, 200);
    const resetBody = await reset.text();
    assert.equal(resetBody.includes(visitorCredentials[2]), false);
    assert.equal(resetBody.includes("visitor-test-id-reset"), true);
    const resetCookie = (reset.headers.get("set-cookie") ?? "").split(";", 1)[0];
    assert.equal(resetCookie.endsWith(visitorCredentials[2]), true);

    const revoked = await fetch(`${origin}/api/session/visitor`, {
      method: "DELETE",
      headers: { Origin: origin, Cookie: resetCookie },
    });
    assert.equal(revoked.status, 200);
    assert.equal((await revoked.text()).includes(visitorCredentials[2]), false);
    assert.equal(revoked.headers.get("set-cookie")?.includes("Max-Age=0"), true);

    const expiredSession = await fetch(`${origin}/api/session`, {
      headers: { Cookie: resetCookie },
    });
    assert.equal(expiredSession.status, 401);
    assert.equal(expiredSession.headers.get("set-cookie")?.includes("Max-Age=0"), true);

    delayBootstrap = true;
    const concurrent = await Promise.all([
      fetch(`${origin}/api/session/visitor`, {
        method: "POST",
        headers: { Origin: origin },
      }),
      fetch(`${origin}/api/session/visitor`, {
        method: "POST",
        headers: { Origin: origin },
      }),
    ]);
    delayBootstrap = false;
    assert.deepEqual(concurrent.map((item) => item.status), [200, 200]);
    assert.equal(bootstrapCalls, 2);
    const concurrentCookies = concurrent.map((item) =>
      (item.headers.get("set-cookie") ?? "").split(";", 1)[0],
    );
    assert.equal(concurrentCookies[0], concurrentCookies[1]);
    const concurrentBody = await Promise.all(concurrent.map((item) => item.text()));
    assert.equal(concurrentBody.every((body) => !body.includes(visitorCredentials[3])), true);

    failBootstrap = true;
    const failedBootstrap = await fetch(`${origin}/api/session/visitor`, {
      method: "POST",
      headers: { Origin: origin },
    });
    failBootstrap = false;
    const failedBody = await failedBootstrap.text();
    assert.equal(failedBootstrap.status, 503);
    assert.equal(failedBody.includes("provider secret"), false);
    assert.equal(failedBody.includes(visitorCredentials[4]), false);
    assert.equal(bootstrapCalls, 3);

    const currentVisitorCredential = visitorCredentials[3];

    for (const resource of ["balances", "objects"]) {
      const url = `${origin}/api/sui/wallet/${"0x" + "a".repeat(64)}/${resource}`;
      const baseCount = forwardedKinds.length;
      const patOnly = await fetch(url, {
        headers: { Cookie: `orhrex_session=${patCredential}` },
      });
      assert.equal(patOnly.status, 200);
      assert.equal(forwardedKinds.at(-1), "pat");

      const visitorOnly = await fetch(url, {
        headers: { Cookie: `orhrex_visitor_session=${currentVisitorCredential}` },
      });
      assert.equal(
        visitorOnly.status,
        200,
        (await visitorOnly.clone().json()).error ?? "unexpected Sui visitor response",
      );
      assert.equal(forwardedKinds.at(-1), "visitor");

      const noCookies = await fetch(url);
      assert.equal(noCookies.status, 401);
      assert.equal(forwardedKinds.length, baseCount + 2);

      const bothCookies = await fetch(url, {
        headers: {
          Cookie: `orhrex_session=${patCredential}; orhrex_visitor_session=${currentVisitorCredential}`,
        },
      });
      assert.equal(bothCookies.status, 401);
      assert.equal(forwardedKinds.length, baseCount + 2);

      for (const response of [patOnly, visitorOnly, noCookies, bothCookies]) {
        const body = await response.text();
        assert.equal(response.url.includes(patCredential), false);
        assert.equal(response.url.includes(currentVisitorCredential), false);
        assert.equal(body.includes(patCredential), false);
        assert.equal(body.includes(currentVisitorCredential), false);
        assert.equal(body.toLowerCase().includes("authorization"), false);
      }
    }

    assert.deepEqual(forwardedKinds, ["pat", "visitor", "pat", "visitor"]);
  } finally {
    child.kill();
    await new Promise((resolve) => {
      if (child.exitCode !== null) resolve();
      else child.once("exit", resolve);
    });
    await new Promise((resolve, reject) => {
      backend.close((error) => (error ? reject(error) : resolve()));
    });
  }
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
  assert.equal(sameOriginRequest(normalizedRequestUrl), false);
  assert.equal(
    sameOriginRequest(normalizedRequestUrl, { ORHEX_APP_ORIGIN: "http://127.0.0.1:3199" }),
    true,
  );

  const spoofedHost = new Request("http://localhost:3199/api/session", {
    method: "POST",
    headers: {
      host: "127.0.0.1:3199",
      origin: "http://attacker.example",
    },
  });
  assert.equal(sameOriginRequest(spoofedHost), false);

  const forwardedSpoof = new Request("https://app.example/api/session", {
    method: "POST",
    headers: {
      origin: "https://attacker.example",
      "x-forwarded-host": "app.example",
      "x-forwarded-proto": "https",
    },
  });
  assert.equal(sameOriginRequest(forwardedSpoof), false);
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
