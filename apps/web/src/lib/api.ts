import { HealthResponse } from "./types";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch(`${API_BASE}/api/v1/health`, {
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
    headers: {
      "Accept": "application/json",
    },
  });
  if (!res.ok) {
    throw new Error(`System status is unavailable (HTTP ${res.status}).`);
  }
  return res.json();
}

export const authIntegrationNotice =
  "Sign-in and authenticated memory access will be connected through a same-origin backend-for-frontend using secure HttpOnly cookies. No bearer token is stored in this browser.";
