"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRightIcon, ShieldIcon } from "@/components/icons";
import { PageHeading } from "@/components/ui";
import { ApiError, getSession, logout } from "@/lib/api";
import type { SessionStatus } from "@/lib/types";

export default function SettingsPage() {
  const router = useRouter();
  const [session, setSession] = useState<SessionStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void getSession()
      .then(setSession)
      .catch((reason: unknown) => setError(reason instanceof ApiError ? reason.message : "Session status is unavailable."));
  }, []);

  async function signOut() {
    setBusy(true);
    setError(null);
    try {
      await logout();
      router.replace("/signin");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : "Sign-out could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Settings"
        title="Your space, your rules."
        description="Review the current secure session and sign out of this browser."
      />
      <section className="surface-card p-5 sm:p-7">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><ShieldIcon className="h-[18px] w-[18px]" /></span>
          <div className="min-w-0 flex-1">
            <h2 className="text-[13px] font-semibold text-[#e4eaf0]">Secure browser session</h2>
            <p className="mt-2 text-[11px] leading-5 text-[#8997a5]">
              The backend validates the provisioned bearer token. The browser receives only an HttpOnly, SameSite cookie; the token is not exposed to page scripts.
            </p>
            <div className="mt-5 rounded-xl border border-[#26323e] bg-[#0f151c] p-4">
              {session && (
                <>
                  <p className="text-[11px] font-medium text-[#c5d0d9]">Session active</p>
                  <p className="mt-1 text-[10px] text-[#8493a3]">Backend token expires {new Date(session.expiresAt).toLocaleString()}.</p>
                </>
              )}
              {!session && !error && <p className="text-[11px] text-[#8493a3]">Checking session…</p>}
              {error && <p role="alert" className="text-[11px] text-[#d9a7a3]">{error}</p>}
            </div>
            <button type="button" className="secondary-button mt-4" onClick={() => void signOut()} disabled={busy}>
              {busy ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </div>
      </section>
      <section className="surface-card divide-y divide-[#202a34]">
        <div className="px-5 py-5 sm:px-7">
          <h2 className="text-[13px] font-semibold text-[#e2e8ee]">Connected services</h2>
          <p className="mt-1 text-[11px] leading-5 text-[#8493a3]">The Memory screen shows authenticated Walrus readiness and returns data only from the backend memory service.</p>
          <a href="/memory" className="mt-3 inline-flex items-center gap-2 text-[11px] font-semibold text-[#b8d5e9] hover:text-[#e0edf6]">Open Memory <ArrowRightIcon className="h-3.5 w-3.5" /></a>
        </div>
        <div className="px-5 py-5 sm:px-7">
          <h2 className="text-[13px] font-semibold text-[#e2e8ee]">Access management</h2>
          <p className="mt-1 text-[11px] leading-5 text-[#8493a3]">ORHEX does not issue or provision access tokens in the browser. Contact your trusted operator for access.</p>
        </div>
      </section>
    </div>
  );
}
