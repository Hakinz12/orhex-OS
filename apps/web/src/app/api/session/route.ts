import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  readJsonBody,
  sameOriginRequest,
  clearedSessionCookieOptions,
  sessionCookieName,
  sessionCookieOptions,
  validateBackendSession,
  validateBackendVisitorSession,
} from "@/lib/server/bff-core.mjs";
import { jsonError } from "@/lib/server/bff";

const visitorCookieName = (env = process.env) =>
  env.NODE_ENV === "production" ? "__Host-orhrex_visitor_session" : "orhrex_visitor_session";

async function validateVisitorSession(token: string) {
  return validateBackendVisitorSession(token);
}

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function sessionJson(body: Record<string, unknown>, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

function clearSession(response: NextResponse) {
  response.cookies.set(sessionCookieName(), "", clearedSessionCookieOptions());
  return response;
}

function clearVisitorSession(response: NextResponse) {
  response.cookies.set(visitorCookieName(), "", clearedSessionCookieOptions());
  return response;
}

function visitorSessionPayload(session: {
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

export async function POST(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  const parsed = await readJsonBody(request, 4096);
  if (!parsed.ok) return jsonError(parsed.status, "invalid_request", "A valid session token is required.");
  const body = parsed.value;
  if (
    !body ||
    typeof body !== "object" ||
    Array.isArray(body) ||
    Object.keys(body).length !== 1 ||
    !("token" in body) ||
    typeof body.token !== "string"
  ) {
    return jsonError(400, "invalid_request", "A valid session token is required.");
  }
  const token = body.token;
  const session = await validateBackendSession(token);
  if (!session.ok) {
    if (session.status === 503) {
      return jsonError(503, "service_unavailable", "Sign-in is temporarily unavailable.");
    }
    return clearSession(
      jsonError(401, "unauthenticated", "The supplied token is invalid or expired."),
    );
  }

  const response = sessionJson({
    authenticated: true,
    kind: "pat",
    expiresAt: session.expiresAt.toISOString(),
  });
  response.cookies.set(
    sessionCookieName(),
    token,
    sessionCookieOptions(session.expiresAt),
  );
  response.cookies.set(visitorCookieName(), "", clearedSessionCookieOptions());
  return response;
}

export async function GET() {
  const cookieStore = await cookies();
  const patToken = cookieStore.get(sessionCookieName())?.value;
  if (patToken) {
    const patSession = await validateBackendSession(patToken);
    if (patSession.ok) {
      return sessionJson({
        authenticated: true,
        kind: "pat",
        expiresAt: patSession.expiresAt.toISOString(),
      });
    }
    if (patSession.status === 503) {
      return jsonError(503, "service_unavailable", "Session status is temporarily unavailable.");
    }
    return clearSession(
      jsonError(401, "unauthenticated", "Your session is invalid or expired."),
    );
  }

  const visitorToken = cookieStore.get(visitorCookieName())?.value;
  if (!visitorToken) return jsonError(401, "unauthenticated", "Sign-in is required.");
  const visitorSession = await validateVisitorSession(visitorToken);
  if (visitorSession.ok) return sessionJson(visitorSessionPayload(visitorSession));
  if (visitorSession.status === 503) {
    return jsonError(503, "service_unavailable", "Session status is temporarily unavailable.");
  }
  return clearVisitorSession(
    jsonError(401, "unauthenticated", "Your session is invalid or expired."),
  );
}

export async function DELETE(request: Request) {
  if (!sameOriginRequest(request)) {
    return jsonError(403, "cross_origin_request", "This request must come from this application.");
  }
  return clearSession(sessionJson({ authenticated: false }));
}
