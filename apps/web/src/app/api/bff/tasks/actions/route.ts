import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import {
  callBackend,
  memoryRouteAccess,
  sessionCookieName,
  visitorCookieName,
} from "@/lib/server/bff-core.mjs";

export async function GET(request: Request) {
  const cookieStore = await cookies();
  const token =
    cookieStore.get(sessionCookieName())?.value ??
    cookieStore.get(visitorCookieName())?.value ??
    null;
  const access = memoryRouteAccess(request, token);
  if (!access.ok) {
    return NextResponse.json(
      { message: "Authentication required." },
      { status: access.status },
    );
  }

  const result = await callBackend("/api/v1/tasks/actions", {
    token: access.token,
    method: "GET",
  });
  if (result.kind !== "response") {
    return NextResponse.json(
      { message: "Action service unavailable." },
      { status: 503 },
    );
  }

  return new NextResponse(result.response.body, {
    status: result.response.status,
    headers: { "Content-Type": "application/json" },
  });
}
