import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  callBackend,
  clearedSessionCookieOptions,
  safeErrorResponse,
  sameOriginRequest,
  sessionCookieName,
  validateBackendVisitorSession,
  validateBackendSession,
} from "@/lib/server/bff-core.mjs";
import { jsonError } from "@/lib/server/bff";

const visitorCookieName = (env = process.env) =>
  env.NODE_ENV === "production" ? "__Host-orhrex_visitor_session" : "orhrex_visitor_session";
const visitorCookieOptions = (env = process.env) => ({
  httpOnly: true,
  secure: env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  expires: new Date(Date.now() + 31_536_000 * 1000),
  maxAge: 31_536_000,
});

async function validateVisitorSession(token: string) {
  return validateBackendVisitorSession(token);
}

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type BootstrapResult = { credential: string } | { errorStatus: number };
let bootstrapInFlight: Promise<BootstrapResult> | null = null;

const visitorCredentialPattern = /^orv_[A-Za-z0-9_-]{43,100}$/;

async function hasRequestBody(request: Request) {
  const contentLength = request.headers.get("content-length");
  if (contentLength !== null && contentLength !== "0") return true;
  if (!request.body) return false;

  const reader = request.body.getReader();
  while (true) {
    const { done, value } = await reader.read();
    if (done) return false;
    if (value.byteLength > 0) {
      await reader.cancel();
      return true;
    }
  }
}

function sessionJson(body: Record<string, unknown>, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function sessionPayload(session: {
  visitorIdentityId: string;
  createdAt: Date;
  credentialCreatedAt: Date;
  credentialExpiresAt: Date | null;
}) {
  return {
    authenticated: true,
    kind: "visitor",
    visitorIdentityId: session.visitorIdentityId,
    createdAt: session.createdAt.toISOString(),
    credentialCreatedAt: session.credentialCreatedAt.toISOString(),
    credentialExpiresAt: session.credentialExpiresAt?.toISOString() ?? null,
  };
}

function clearPatCookie(response: NextResponse) {
  response.cookies.set(sessionCookieName(), "", clearedSessionCookieOptions());
  return response;
}

function clearVisitorCookie(response: NextResponse) {
  response.cookies.set(visitorCookieName(), "", clearedSessionCookieOptions());
  return response;
}

function visitorCredentialError(status: number): NextResponse {
  if (status === 401) {
    return jsonError(401, "unauthenticated", "Your visitor session is invalid or expired.");
  }
  const mapped = safeErrorResponse(status);
  return NextResponse.json(mapped.body, {
    status: mapped.status,
    headers: { "Cache-Control": "no-store" },
  });
}

async function provisionCredential(): Promise<BootstrapResult> {
  if (!bootstrapInFlight) {
    bootstrapInFlight = (async () => {
      const result = await callBackend("/api/v1/visitor/bootstrap", {
        method: "POST",
      });
      if (result.kind !== "response") return { errorStatus: 503 };
      if (!result.response.ok) return { errorStatus: result.response.status };
      try {
        const payload = await result.response.json();
        if (
          !payload ||
          typeof payload !== "object" ||
          Array.isArray(payload) ||
          Object.keys(payload).length !== 1 ||
          !visitorCredentialPattern.test(payload.visitor_credential)
        ) {
          return { errorStatus: 502 };
        }
        return { credential: payload.visitor_credential };
      } catch {
        return { errorStatus: 502 };
      }
    })();
  }
  try {
    return await bootstrapInFlight;
  } finally {
    bootstrapInFlight = null;
  }
}

async function visitorResponse(
  token: string,
  options: { setCookie?: boolean; clearCookieOnInvalid?: boolean } = {},
) {
  const session = await validateVisitorSession(token);
  if (!session.ok) {
    let response = visitorCredentialError(session.status);
    if (session.status === 401 && options.clearCookieOnInvalid) {
      response = clearVisitorCookie(response);
    } else if (options.setCookie && session.status !== 401) {
      response.cookies.set(visitorCookieName(), token, visitorCookieOptions());
    }
    return response;
  }
  const response = sessionJson(sessionPayload(session));
  if (options.setCookie) {
    response.cookies.set(visitorCookieName(), token, visitorCookieOptions());
  }
  return response;
}

async function existingPatSession() {
  const store = await cookies();
  const token = store.get(sessionCookieName())?.value;
  if (!token) return { kind: "none" as const };
  const session = await validateBackendSession(token);
  if (session.ok) {
    return {
      kind: "valid" as const,
      response: sessionJson({
        authenticated: true,
        kind: "pat",
        expiresAt: session.expiresAt.toISOString(),
      }),
    };
  }
  if (session.status === 503) return { kind: "unavailable" as const };
  return { kind: "invalid" as const };
}

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  if (await hasRequestBody(request)) {
    return jsonError(400, "invalid_request", "The visitor session request is invalid.");
  }

  const pat = await existingPatSession();
  if (pat.kind === "valid") return pat.response;
  if (pat.kind === "unavailable") {
    return jsonError(503, "service_unavailable", "Session status is temporarily unavailable.");
  }
  if (pat.kind === "invalid") {
    return clearPatCookie(
      jsonError(401, "unauthenticated", "Your session is invalid or expired."),
    );
  }

  const store = await cookies();
  const existingVisitor = store.get(visitorCookieName())?.value;
  let invalidVisitorCookie = false;
  if (existingVisitor) {
    const session = await validateVisitorSession(existingVisitor);
    if (session.ok) return sessionJson(sessionPayload(session));
    if (session.status === 503) {
      return jsonError(503, "service_unavailable", "Session status is temporarily unavailable.");
    }
    invalidVisitorCookie = true;
  }

  const issued = await provisionCredential();
  if (!("credential" in issued)) {
    const response = visitorCredentialError(issued.errorStatus);
    return invalidVisitorCookie ? clearVisitorCookie(response) : response;
  }
  return visitorResponse(issued.credential, {
    setCookie: true,
    clearCookieOnInvalid: invalidVisitorCookie,
  });
}

export async function PATCH(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  if (await hasRequestBody(request)) {
    return jsonError(400, "invalid_request", "The visitor session request is invalid.");
  }
  const store = await cookies();
  const token = store.get(visitorCookieName())?.value;
  if (!token || !visitorCredentialPattern.test(token)) {
    return clearVisitorCookie(
      jsonError(401, "unauthenticated", "Your visitor session is invalid or expired."),
    );
  }
  const result = await callBackend("/api/v1/visitor/session/rotate", {
    token,
    method: "POST",
  });
  if (result.kind !== "response") {
    return jsonError(503, "service_unavailable", "Visitor session is temporarily unavailable.");
  }
  if (result.response.status === 401) {
    return clearVisitorCookie(
      jsonError(401, "unauthenticated", "Your visitor session is invalid or expired."),
    );
  }
  if (!result.response.ok) return visitorCredentialError(result.response.status);

  let replacement: string;
  try {
    const payload = await result.response.json();
    if (
      !payload ||
      typeof payload !== "object" ||
      Array.isArray(payload) ||
      Object.keys(payload).length !== 1 ||
      !visitorCredentialPattern.test(payload.visitor_credential)
    ) {
      return jsonError(502, "invalid_backend_response", "The visitor service returned an invalid response.");
    }
    replacement = payload.visitor_credential;
  } catch {
    return jsonError(502, "invalid_backend_response", "The visitor service returned an invalid response.");
  }
  return visitorResponse(replacement, { setCookie: true });
}

export async function PUT(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  if (await hasRequestBody(request)) {
    return jsonError(400, "invalid_request", "The visitor session request is invalid.");
  }
  const store = await cookies();
  const token = store.get(visitorCookieName())?.value;
  if (!token || !visitorCredentialPattern.test(token)) {
    return jsonError(401, "unauthenticated", "Your visitor session is invalid or expired.");
  }
  const result = await callBackend("/api/v1/visitor/session/reset", {
    token,
    method: "POST",
  });
  if (result.kind !== "response") {
    return jsonError(503, "service_unavailable", "Visitor session is temporarily unavailable.");
  }
  if (!result.response.ok) return visitorCredentialError(result.response.status);

  let replacement: string;
  try {
    const payload = await result.response.json();
    if (
      !payload ||
      typeof payload !== "object" ||
      Array.isArray(payload) ||
      Object.keys(payload).length !== 2 ||
      typeof payload.visitor_identity_id !== "string" ||
      !visitorCredentialPattern.test(payload.visitor_credential)
    ) {
      return jsonError(502, "invalid_backend_response", "The visitor service returned an invalid response.");
    }
    replacement = payload.visitor_credential;
  } catch {
    return jsonError(502, "invalid_backend_response", "The visitor service returned an invalid response.");
  }
  return visitorResponse(replacement, { setCookie: true });
}

export async function DELETE(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  if (await hasRequestBody(request)) {
    return jsonError(400, "invalid_request", "The visitor session request is invalid.");
  }
  const store = await cookies();
  const token = store.get(visitorCookieName())?.value;
  if (!token || !visitorCredentialPattern.test(token)) {
    return clearVisitorCookie(
      jsonError(401, "unauthenticated", "Your visitor session is invalid or expired."),
    );
  }
  const result = await callBackend("/api/v1/visitor/session", {
    token,
    method: "DELETE",
  });
  if (result.kind !== "response") {
    return jsonError(503, "service_unavailable", "Visitor session is temporarily unavailable.");
  }
  if (!result.response.ok) {
    if (result.response.status === 401) {
      return clearVisitorCookie(
        jsonError(401, "unauthenticated", "Your visitor session is invalid or expired."),
      );
    }
    return visitorCredentialError(result.response.status);
  }
  return clearVisitorCookie(sessionJson({ authenticated: false }));
}
