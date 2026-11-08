"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRightIcon, ShieldIcon } from "@/components/icons";
import { useSession } from "@/components/session-provider";
import { PageHeading } from "@/components/ui";
import { ApiError } from "@/lib/api";

export default function SettingsPage() {
  const router = useRouter();
  const {
    session,
    refresh,
    rotateVisitor,
    resetVisitor,
    endVisitor,
    signOutPat,
  } = useSession();
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const actionInFlight = useRef(false);

  async function runSessionAction(
    actionName: string,
    action: () => Promise<void>,
    successMessage: string,
    destination?: string,
  ) {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setBusyAction(actionName);
    setError(null);
    setSuccess(null);
    try {
      await action();
      if (destination) {
        router.replace(destination);
        router.refresh();
      } else {
        setSuccess(successMessage);
      }
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        await refresh();
      }
      setError(
        reason instanceof ApiError || reason instanceof Error
          ? reason.message
          : "The session update could not be completed.",
      );
    } finally {
      actionInFlight.current = false;
      setBusyAction(null);
    }
  }

  function confirmReset() {
    if (
      window.confirm(
        "Start a new isolated visitor identity? Existing memories remain with the old identity and are not deleted.",
      )
    ) {
      void runSessionAction(
        "reset",
        resetVisitor,
        "A new isolated visitor identity is active. The previous identity and its data were not deleted.",
      );
    }
  }

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Settings"
        title="Your space, your rules."
        description="Review your session and manage its credentials or visitor identity."
      />
      <section className="surface-card p-5 sm:p-7">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><ShieldIcon className="h-[18px] w-[18px]" /></span>
          <div className="min-w-0 flex-1">
            <h2 className="text-[13px] font-semibold text-[#e4eaf0]">Secure browser session</h2>
            <p className="mt-2 text-[11px] leading-5 text-[#8997a5]">
              Credentials are kept in HttpOnly, SameSite cookies and are never exposed to page scripts.
            </p>
            <div className="mt-5 rounded-xl border border-[#26323e] bg-[#0f151c] p-4">
              {session.status === "pat" && session.session.kind === "pat" && (
                <>
                  <p className="text-[11px] font-medium text-[#c5d0d9]">PAT session active</p>
                  <p className="mt-1 text-[10px] text-[#8493a3]">Backend token expires {new Date(session.session.expiresAt).toLocaleString()}.</p>
                </>
              )}
              {session.status === "visitor" && session.session.kind === "visitor" && (
                <>
                  <p className="text-[11px] font-medium text-[#c5d0d9]">Visitor session active</p>
                  <p className="mt-1 break-all text-[10px] text-[#8493a3]">Visitor identity: {session.session.visitorIdentityId}</p>
                  <p className="mt-1 text-[10px] text-[#8493a3]">Identity created {new Date(session.session.createdAt).toLocaleString()}.</p>
                  <p className="mt-1 text-[10px] text-[#8493a3]">
                    Credential {session.session.credentialExpiresAt
                      ? `expires ${new Date(session.session.credentialExpiresAt).toLocaleString()}.`
                      : "has no scheduled expiry."}
                  </p>
                </>
              )}
              {session.status === "loading" && <p className="text-[11px] text-[#8493a3]">Checking session…</p>}
              {session.status === "unauthenticated" && <p className="text-[11px] text-[#8493a3]">No session is currently active.</p>}
              {session.status === "error" && <p role="alert" className="text-[11px] text-[#d9a7a3]">{session.message}</p>}
              {error && <p role="alert" className="text-[11px] text-[#d9a7a3]">{error}</p>}
              {success && <p role="status" className="mt-2 text-[11px] text-[#a8c8b3]">{success}</p>}
            </div>
            {session.status === "visitor" && (
              <div className="mt-4 flex flex-wrap gap-2">
                <button type="button" className="secondary-button" onClick={() => void runSessionAction("rotate", rotateVisitor, "Your visitor credential was rotated.")} disabled={busyAction !== null}>
                  {busyAction === "rotate" ? "Rotating…" : "Rotate credential"}
                </button>
                <button type="button" className="secondary-button" onClick={confirmReset} disabled={busyAction !== null}>
                  {busyAction === "reset" ? "Resetting…" : "Reset visitor identity"}
                </button>
                <button type="button" className="secondary-button" onClick={() => void runSessionAction("end", endVisitor, "", "/signin")} disabled={busyAction !== null}>
                  {busyAction === "end" ? "Ending session…" : "End visitor session"}
                </button>
              </div>
            )}
            {session.status === "pat" && (
              <button type="button" className="secondary-button mt-4" onClick={() => void runSessionAction("signout", signOutPat, "", "/signin")} disabled={busyAction !== null}>
                {busyAction === "signout" ? "Signing out…" : "Sign out"}
              </button>
            )}
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
