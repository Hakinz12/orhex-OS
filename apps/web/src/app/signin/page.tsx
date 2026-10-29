"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { MarkIcon, ShieldIcon } from "@/components/icons";
import { createSession } from "@/lib/api";

function safeDestination() {
  const candidate = new URLSearchParams(window.location.search).get("next");
  if (!candidate?.startsWith("/") || candidate.startsWith("//")) return "/";
  try {
    const destination = new URL(candidate, window.location.origin);
    if (destination.origin !== window.location.origin || destination.pathname.startsWith("/api/")) return "/";
    return `${destination.pathname}${destination.search}${destination.hash}`;
  } catch {
    return "/";
  }
}

export default function SignInPage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createSession(token.trim());
      setToken("");
      router.replace(safeDestination());
      router.refresh();
    } catch (reason) {
      setToken("");
      setError(reason instanceof Error ? reason.message : "Sign-in could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <section className="surface-card w-full max-w-[460px] p-6 sm:p-9">
        <div className="mb-8 flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-[14px] border border-[#344554] bg-[#17222d] text-[#c5dced]">
            <MarkIcon className="h-6 w-6" />
          </span>
          <div>
            <p className="font-display text-[16px] font-extrabold tracking-[0.16em] text-[#f0f3f6]">ORHEX</p>
            <p className="mt-0.5 text-[10px] tracking-[0.04em] text-[#8392a2]">Your World. Understood.</p>
          </div>
        </div>
        <p className="eyebrow">Secure access</p>
        <h1 className="font-display mt-3 text-[29px] font-semibold tracking-[-0.04em] text-[#f0f3f6]">Sign in to your space.</h1>
        <p className="mt-2 text-[13px] leading-6 text-[#93a1b0]">
          Use the access token provided to you by your trusted ORHEX operator.
        </p>
        <form className="mt-7 space-y-4" onSubmit={submit}>
          <label htmlFor="access-token" className="block text-[12px] font-medium text-[#c4ced7]">Provisioned access token</label>
          <input
            id="access-token"
            name="token"
            type="password"
            autoComplete="off"
            required
            maxLength={104}
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="orh_…"
            className="text-input"
            aria-describedby="token-security-note signin-error"
          />
          <p id="token-security-note" className="inline-flex items-start gap-2 text-[10px] leading-5 text-[#8291a0]">
            <ShieldIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#b8d2e5]" />
            The token is validated by the ORHEX service and stored only in a protected HttpOnly cookie. It is never saved in browser storage.
          </p>
          {error && <p id="signin-error" role="alert" className="rounded-xl border border-[#5a3739] bg-[#26191c] px-3 py-2.5 text-[12px] text-[#e2a8a4]">{error}</p>}
          <button type="submit" className="primary-button w-full" disabled={busy || token.trim().length === 0}>
            {busy ? "Verifying access…" : "Continue securely"}
          </button>
        </form>
        <p className="mt-6 border-t border-[#202a34] pt-5 text-[10px] leading-5 text-[#718092]">
          ORHEX does not issue access tokens from this page. Contact your trusted operator if you need access.
        </p>
      </section>
    </main>
  );
}
