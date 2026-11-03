"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname } from "next/navigation";
import {
  ApiError,
  bootstrapVisitorSession,
  endVisitorSession,
  getSession,
  logout,
  resetVisitorSession,
  rotateVisitorSession,
} from "@/lib/api";
import type { SessionStatus } from "@/lib/types";

type SessionView =
  | { status: "loading" }
  | { status: "unauthenticated" }
  | { status: "error"; message: string }
  | { status: "pat" | "visitor"; session: SessionStatus };

type SessionContextValue = {
  session: SessionView;
  refresh: () => Promise<void>;
  startVisitor: () => Promise<void>;
  rotateVisitor: () => Promise<void>;
  resetVisitor: () => Promise<void>;
  endVisitor: () => Promise<void>;
  signOutPat: () => Promise<void>;
};

const SessionContext = createContext<SessionContextValue | null>(null);
let localBootstrap: Promise<SessionStatus> | null = null;

async function bootstrapWithLock(): Promise<SessionStatus> {
  const run = () => bootstrapVisitorSession();
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request("orhex-visitor-bootstrap", run);
  }
  if (!localBootstrap) {
    localBootstrap = run().finally(() => {
      localBootstrap = null;
    });
  }
  return localBootstrap;
}

function sessionView(session: SessionStatus): SessionView {
  return { status: session.kind, session };
}

function errorMessage(reason: unknown) {
  return reason instanceof ApiError
    ? reason.message
    : "Your session could not be verified. Please try again.";
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const previousPath = useRef(pathname);
  const initialized = useRef(false);
  const endedByUser = useRef(false);
  const [session, setSession] = useState<SessionView>({ status: "loading" });

  const refresh = useCallback(async () => {
    setSession({ status: "loading" });
    try {
      const current = await getSession();
      endedByUser.current = false;
      initialized.current = true;
      setSession(sessionView(current));
    } catch (reason) {
      initialized.current = true;
      if (reason instanceof ApiError && reason.status === 401) {
        setSession({ status: "unauthenticated" });
      } else {
        setSession({ status: "error", message: errorMessage(reason) });
      }
    }
  }, []);

  const initialize = useCallback(async () => {
    if (endedByUser.current) {
      initialized.current = true;
      setSession({ status: "unauthenticated" });
      return;
    }
    setSession({ status: "loading" });
    try {
      const current = await getSession();
      endedByUser.current = false;
      initialized.current = true;
      setSession(sessionView(current));
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        try {
          const created = await bootstrapWithLock();
          initialized.current = true;
          setSession(sessionView(created));
          return;
        } catch (bootstrapError) {
          initialized.current = true;
          setSession({ status: "error", message: errorMessage(bootstrapError) });
          return;
        }
      }
      initialized.current = true;
      setSession({ status: "error", message: errorMessage(reason) });
    }
  }, []);

  useEffect(() => {
    const wasSignIn = previousPath.current === "/signin";
    previousPath.current = pathname;
    if (pathname === "/signin") {
      if (!initialized.current) setSession({ status: "unauthenticated" });
      return;
    }
    if (!initialized.current || wasSignIn) void initialize();
  }, [initialize, pathname]);

  const startVisitor = useCallback(async () => {
    setSession({ status: "loading" });
    try {
      const created = await bootstrapWithLock();
      endedByUser.current = false;
      initialized.current = true;
      setSession(sessionView(created));
    } catch (reason) {
      initialized.current = true;
      setSession({ status: "error", message: errorMessage(reason) });
      throw reason;
    }
  }, []);

  const rotateVisitor = useCallback(async () => {
    if (session.status !== "visitor") throw new Error("A visitor session is required.");
    setSession({ status: "loading" });
    try {
      const updated = await rotateVisitorSession();
      setSession(sessionView(updated));
    } catch (reason) {
      setSession({ status: "error", message: errorMessage(reason) });
      throw reason;
    }
  }, [session]);

  const resetVisitor = useCallback(async () => {
    if (session.status !== "visitor") throw new Error("A visitor session is required.");
    setSession({ status: "loading" });
    try {
      const updated = await resetVisitorSession();
      setSession(sessionView(updated));
    } catch (reason) {
      setSession({ status: "error", message: errorMessage(reason) });
      throw reason;
    }
  }, [session]);

  const endVisitor = useCallback(async () => {
    if (session.status !== "visitor") throw new Error("A visitor session is required.");
    await endVisitorSession();
    endedByUser.current = true;
    initialized.current = true;
    setSession({ status: "unauthenticated" });
  }, [session]);

  const signOutPat = useCallback(async () => {
    await logout();
    endedByUser.current = true;
    initialized.current = true;
    setSession({ status: "unauthenticated" });
  }, []);

  const value = useMemo(
    () => ({
      session,
      refresh,
      startVisitor,
      rotateVisitor,
      resetVisitor,
      endVisitor,
      signOutPat,
    }),
    [endVisitor, refresh, resetVisitor, rotateVisitor, session, signOutPat, startVisitor],
  );
  const isSignIn = pathname === "/signin";

  return (
    <SessionContext.Provider value={value}>
      {isSignIn || session.status === "visitor" || session.status === "pat" ? (
        children
      ) : session.status === "error" ? (
        <main className="flex min-h-screen items-center justify-center px-4">
          <section className="surface-card w-full max-w-md p-6 text-center">
            <h1 className="font-display text-xl font-semibold text-[#f0f3f6]">Session unavailable</h1>
            <p role="alert" className="mt-3 text-sm leading-6 text-[#a2afbb]">{session.message}</p>
            <button type="button" className="primary-button mt-5" onClick={() => void initialize()}>
              Try again
            </button>
          </section>
        </main>
      ) : session.status === "unauthenticated" ? (
        <main className="flex min-h-screen items-center justify-center px-4">
          <section className="surface-card w-full max-w-md p-6 text-center">
            <h1 className="font-display text-xl font-semibold text-[#f0f3f6]">No active session</h1>
            <p className="mt-3 text-sm leading-6 text-[#a2afbb]">Continue with a new private visitor space or sign in with a provisioned PAT.</p>
            <button type="button" className="primary-button mt-5" onClick={() => void startVisitor()}>
              Continue without a PAT
            </button>
          </section>
        </main>
      ) : (
        <main className="flex min-h-screen items-center justify-center px-4">
          <p role="status" className="text-sm text-[#a2afbb]">Preparing your private ORHEX space…</p>
        </main>
      )}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used within SessionProvider.");
  return value;
}
