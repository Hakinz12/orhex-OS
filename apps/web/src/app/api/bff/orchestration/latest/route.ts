import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { callBackend, memoryRouteAccess, sessionCookieName, visitorCookieName } from "@/lib/server/bff-core.mjs";

async function token() {
  const jar = await cookies();
  return jar.get(sessionCookieName())?.value ?? jar.get(visitorCookieName())?.value ?? null;
}

export async function GET() {
  const access = memoryRouteAccess(new Request("http://localhost/api/bff/orchestration/latest"), await token());
  if (!access.ok) return NextResponse.json({ message: "Authentication required." }, { status: access.status });
  const result = await callBackend("/api/v1/orchestration/latest", { token: access.token });
  if (result.kind !== "response") return NextResponse.json({ message: "Orchestration service unavailable." }, { status: 503 });
  return new NextResponse(result.response.body, { status: result.response.status, headers: { "Content-Type": "application/json" } });
}
