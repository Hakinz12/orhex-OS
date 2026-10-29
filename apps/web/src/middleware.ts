import { NextRequest, NextResponse } from "next/server";
import {
  clearedSessionCookieOptions,
  sessionCookieName,
  validateBackendSession,
} from "@/lib/server/bff-core.mjs";

export async function middleware(request: NextRequest) {
  if (request.nextUrl.pathname === "/signin") return NextResponse.next();
  const cookieName = sessionCookieName();
  const token = request.cookies.get(cookieName)?.value;
  if (!token) return redirectToSignIn(request);

  const session = await validateBackendSession(token);
  if (session.ok) return NextResponse.next();
  if (session.status === 503) return NextResponse.next();

  const response = redirectToSignIn(request);
  response.cookies.set(cookieName, "", clearedSessionCookieOptions());
  return response;
}

function redirectToSignIn(request: NextRequest) {
  const destination = request.nextUrl.clone();
  destination.pathname = "/signin";
  destination.search = "";
  destination.searchParams.set(
    "next",
    `${request.nextUrl.pathname}${request.nextUrl.search}`,
  );
  return NextResponse.redirect(destination);
}

export const config = {
  matcher: ["/((?!api/|_next/static|_next/image|favicon.ico|signin).*)"],
};
