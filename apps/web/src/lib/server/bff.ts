import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  callBackend,
  clearedSessionCookieOptions,
  safeErrorResponse,
  safeMemoryList,
  safeMemoryOperation,
  safeReadiness,
  safeRecall,
  parseSuiPagination,
  safeSuiBackendResponse,
  safeConversation,
  sessionCookieName,
  visitorCookieName,
  validateSuiWalletAddress,
} from "./bff-core.mjs";

type ResponseKind = "status" | "operation" | "recall" | "memory_list";

export function jsonError(status: number, error: string, message: string) {
  return NextResponse.json(
    { error, message },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

export function clearSessionCookie(response: NextResponse, token?: string) {
  if (token?.startsWith("orv_")) {
    response.cookies.set(visitorCookieName(), "", clearedSessionCookieOptions());
  } else {
    response.cookies.set(sessionCookieName(), "", clearedSessionCookieOptions());
  }
  return response;
}

export async function readSessionToken() {
  const cookieStore = await cookies();
  const patToken = cookieStore.get(sessionCookieName())?.value;
  const visitorToken = cookieStore.get(visitorCookieName())?.value;
  if (patToken && visitorToken) return null;
  return patToken ?? visitorToken ?? null;
}

export async function proxyMemoryOperation(
  token: string,
  path: string,
  method: "GET" | "POST" | "DELETE",
  responseKind: ResponseKind,
  body?: Record<string, unknown>,
) {
  const result = await callBackend(path, { token, method, body });
  if (result.kind === "unavailable") {
    return jsonError(503, "service_unavailable", "The memory service is temporarily unavailable.");
  }
  if (result.kind === "rejected") {
    return jsonError(404, "not_found", "The requested memory operation is unavailable.");
  }

  const { response } = result;
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return jsonError(502, "invalid_backend_response", "The memory service returned an invalid response.");
  }

  let safePayload: unknown;
  if (responseKind === "status") safePayload = safeReadiness(payload);
  else if (responseKind === "operation") safePayload = safeMemoryOperation(payload);
  else if (responseKind === "memory_list") safePayload = safeMemoryList(payload);
  else safePayload = safeRecall(payload);

  if (response.status === 401) {
    return clearSessionCookie(
      jsonError(401, "unauthenticated", "Your session is invalid or expired."),
      token,
    );
  }

  if (response.status === 429) {
    const mapped = safeErrorResponse(429);
    return NextResponse.json(mapped.body, {
      status: mapped.status,
      headers: {
        "Cache-Control": "no-store",
        "Retry-After": response.headers.get("retry-after") || "60",
      },
    });
  }

  const operationFailure = responseKind === "operation" && response.status === 502;
  if (!response.ok && !operationFailure) {
    const mapped = safeErrorResponse(response.status);
    return NextResponse.json(mapped.body, {
      status: mapped.status,
      headers: { "Cache-Control": "no-store" },
    });
  }
  if (!safePayload) {
    return jsonError(502, "invalid_backend_response", "The memory service returned an invalid response.");
  }
  return NextResponse.json(safePayload, {
    status: response.status,
    headers: { "Cache-Control": "no-store" },
  });
}

export async function proxySuiWalletData(
  token: string,
  request: Request,
  address: string,
  resource: "balances" | "objects",
) {
  if (!validateSuiWalletAddress(address)) {
    return jsonError(422, "invalid_request", "The Sui wallet address is invalid.");
  }
  const query = parseSuiPagination(new URL(request.url).searchParams);
  if (!query) {
    return jsonError(422, "invalid_request", "The Sui pagination parameters are invalid.");
  }

  const path = `/api/v1/sui/wallet/${encodeURIComponent(address)}/${resource}`;
  const result = await callBackend(path, { token, method: "GET", query });
  if (result.kind === "unavailable") {
    return jsonError(503, "service_unavailable", "The Sui service is temporarily unavailable.");
  }
  if (result.kind === "rejected") {
    return jsonError(400, "invalid_request", "The Sui request is invalid.");
  }
  const safeResult = await safeSuiBackendResponse(result.response, resource);
  const response = NextResponse.json(safeResult.body, {
    status: safeResult.status,
    headers: { "Cache-Control": "no-store" },
  });
  if (result.response.status === 401) return clearSessionCookie(response, token);
  return response;
}

export async function proxyConversationRequest(
  token: string,
  path: string,
  method: "GET" | "POST",
  body?: Record<string, unknown>,
) {
  const result = await callBackend(path, { token, method, body });
  if (result.kind === "unavailable") {
    return jsonError(503, "service_unavailable", "The assistant service is temporarily unavailable.");
  }
  if (result.kind === "rejected") {
    return jsonError(400, "invalid_request", "The conversation request is invalid.");
  }

  if (result.response.status === 401) {
    return clearSessionCookie(
      jsonError(401, "unauthenticated", "Your session is invalid or expired."),
      token,
    );
  }
  if (!result.response.ok) {
    const errors: Record<number, { error: string; message: string }> = {
      404: { error: "not_found", message: "The conversation was not found." },
      409: { error: "conversation_conflict", message: "The conversation changed. Reload it and retry." },
      422: { error: "invalid_request", message: "The conversation request is invalid." },
      429: { error: "rate_limited", message: "Too many requests. Please retry shortly." },
      502: { error: "assistant_error", message: "The assistant returned an unusable response. Please retry." },
      503: { error: "assistant_unavailable", message: "The assistant is temporarily unavailable." },
      504: { error: "assistant_timeout", message: "The assistant did not respond in time. Please retry." },
    };
    const mapped = errors[result.response.status] ?? errors[503];
    return NextResponse.json(mapped, {
      status: result.response.status in errors ? result.response.status : 503,
      headers: { "Cache-Control": "no-store" },
    });
  }

  let payload: unknown;
  try {
    payload = await result.response.json();
  } catch {
    return jsonError(502, "invalid_backend_response", "The assistant service returned an invalid response.");
  }
  const safePayload = safeConversation(payload);
  if (!safePayload) {
    return jsonError(502, "invalid_backend_response", "The assistant service returned an invalid response.");
  }
  return NextResponse.json(safePayload, {
    status: result.response.status,
    headers: { "Cache-Control": "no-store" },
  });
}
