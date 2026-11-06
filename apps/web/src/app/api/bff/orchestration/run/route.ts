import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { callBackend, memoryRouteAccess, sessionCookieName, visitorCookieName } from "@/lib/server/bff-core.mjs";

async function token() {
  const jar = await cookies();
  return jar.get(sessionCookieName())?.value ?? jar.get(visitorCookieName())?.value ?? null;
}

export async function POST(request: Request) {
  const access = memoryRouteAccess(request, await token(), true);
  if (!access.ok) return NextResponse.json({ message: "Request not allowed." }, { status: access.status });
  let body: Record<string, unknown>;
  try { body = await request.json() as Record<string, unknown>; } catch { return NextResponse.json({ message: "Invalid JSON." }, { status: 400 }); }
  const result = await callBackend("/api/v1/orchestration/run", { token: access.token, method: "POST", body });
  if (result.kind !== "response") return NextResponse.json({ message: "Orchestration service unavailable." }, { status: 503 });
  return new NextResponse(result.response.body, { status: result.response.status, headers: { "Content-Type": "application/json" } });
}
