"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { useSession } from "@/components/session-provider";
import {
  ApiError,
  getVerifiedWallet,
  requestWalletVerificationChallenge,
  revokeVerifiedWallet,
  submitWalletVerification,
} from "@/lib/api";
import type { VerifiedWalletStatus } from "@/lib/types";
import { createSuiWalletAdapter } from "./sui";
import {
  completeWalletVerification,
  completeWalletConnectionForCurrentAttempt,
  completeWalletDisconnectForCurrentAttempt,
  runWalletVerificationAttempt,
} from "./verification-flow.mjs";
import type {
  WalletConnectionAdapter,
  WalletConnectionViewModel,
  WalletDiagnosticEvent,
} from "./types";

type WalletConnectionState = WalletConnectionViewModel & {
  shortAddress: string | null;
  verified: boolean;
  verifiedAddress: string | null;
  verificationAvailable: boolean;
  verificationLoading: boolean;
  verificationBusy: boolean;
  verificationError: string | null;
  connectionPhase: WalletConnectionPhase;
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
};

export type WalletConnectionPhase =
  | "idle"
  | "connecting"
  | "awaiting_approval"
  | "verifying"
  | "failed";

const WalletConnectionContext = createContext<WalletConnectionState | null>(null);

function useAdapterState(instance: WalletConnectionAdapter) {
  const [viewModel, setViewModel] = useState<WalletConnectionViewModel>(
    () => instance.getViewModel(),
  );

  useEffect(() => {
    const unsubscribeAccount = instance.onAccountChange(() => {
      setViewModel(instance.getViewModel());
    });
    const unsubscribeConnection = instance.onConnectionChange(setViewModel);
    const stop = instance.start();
    setViewModel(instance.getViewModel());

    return () => {
      unsubscribeAccount();
      unsubscribeConnection();
      stop();
    };
  }, [instance]);

  const signPersonalMessage = useCallback(
    (message: Uint8Array) => instance.signPersonalMessage(message),
    [instance],
  );

  return { ...viewModel, signPersonalMessage };
}

function canonicalWalletAddress(value: string | undefined | null): string | null {
  if (!value || !/^0x[0-9a-f]{1,64}$/i.test(value)) return null;
  return `0x${value.slice(2).toLowerCase().padStart(64, "0")}`;
}

function walletIdentity(viewModel: WalletConnectionViewModel): string | null {
  return viewModel.status === "connected" && viewModel.address
    ? `${viewModel.network}:${canonicalWalletAddress(viewModel.address)}`
    : null;
}

function verificationErrorMessage(reason: unknown): string {
  if (reason instanceof ApiError || reason instanceof Error) return reason.message;
  return "Wallet ownership could not be verified. Please retry.";
}

function reportWalletDiagnostic(
  correlationId: string,
  event: WalletDiagnosticEvent,
): void {
  try {
    console.info("ORHEX wallet verification", {
      ...event,
      correlationId,
    });
  } catch {
    // Diagnostics must not change wallet verification behavior.
  }
}

export function WalletConnectionProvider({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const [adapter] = useState(() => createSuiWalletAdapter());
  const wallet = useAdapterState(adapter);
  const [verification, setVerification] = useState<VerifiedWalletStatus | null>(null);
  const [verificationLoading, setVerificationLoading] = useState(true);
  const [verificationBusy, setVerificationBusy] = useState(false);
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const [connectionPhase, setConnectionPhase] = useState<WalletConnectionPhase>("idle");
  const verificationRequest = useRef(0);
  const verificationInFlight = useRef(false);
  const verificationAttempt = useRef(0);
  const connectionStarting = useRef(false);
  const intentionalDisconnect = useRef(false);
  const initialWallet = adapter.getViewModel();
  const activeWalletIdentity = useRef(walletIdentity(initialWallet));
  const visitorIdentityId =
    session.status === "visitor" && session.session.kind === "visitor"
      ? session.session.visitorIdentityId
      : null;

  useEffect(
    () => {
      activeWalletIdentity.current = walletIdentity(adapter.getViewModel());
      return adapter.onAccountChange(() => {
        const nextIdentity = walletIdentity(adapter.getViewModel());
        if (nextIdentity === activeWalletIdentity.current) return;
        const previousIdentity = activeWalletIdentity.current;
        activeWalletIdentity.current = nextIdentity;
        if (intentionalDisconnect.current) return;
        if (connectionStarting.current && !previousIdentity && nextIdentity) return;
        verificationRequest.current += 1;
        const wasVerifying = verificationInFlight.current;
        verificationAttempt.current += 1;
        verificationInFlight.current = false;
        setVerification(null);
        setVerificationBusy(false);
        setConnectionPhase(wasVerifying ? "failed" : "idle");
        setVerificationError(
          wasVerifying ? "The wallet account changed during verification. Connect again to retry." : null,
        );
        if (!intentionalDisconnect.current && previousIdentity && previousIdentity !== nextIdentity) {
          void revokeVerifiedWallet().catch(() => {
            setVerificationError("The previous wallet link could not be revoked. Retry disconnecting before reconnecting.");
          });
        }
      });
    },
    [adapter],
  );

  useEffect(() => {
    if (session.status === "loading") return;
    const requestId = ++verificationRequest.current;
    let active = true;
    setVerificationLoading(true);
    setVerificationError(null);
    setVerification(null);

    if (!visitorIdentityId) {
      setVerificationLoading(false);
      return () => {
        active = false;
      };
    }

    void getVerifiedWallet()
      .then((result) => {
        if (active && requestId === verificationRequest.current) {
          setVerification(result);
        }
      })
      .catch((reason: unknown) => {
        if (active && requestId === verificationRequest.current) {
          setVerificationError(verificationErrorMessage(reason));
        }
      })
      .finally(() => {
        if (active && requestId === verificationRequest.current) {
          setVerificationLoading(false);
        }
      });

    return () => {
      active = false;
      verificationRequest.current += 1;
    };
  }, [session.status, visitorIdentityId]);

  const connect = useCallback(async () => {
    if (!visitorIdentityId) {
      setVerificationError("Wallet ownership verification requires an active visitor session.");
      throw new Error("Wallet ownership verification requires an active visitor session.");
    }

    await runWalletVerificationAttempt({
      inFlight: verificationInFlight,
      attemptCounter: verificationAttempt,
      createCorrelationId: () => crypto.randomUUID(),
      onStart: () => {
        setVerificationBusy(true);
        setConnectionPhase("connecting");
        setVerificationError(null);
        setVerification(null);
        connectionStarting.current = !walletIdentity(adapter.getViewModel());
      },
      run: async ({ attempt, correlationId, isCurrentAttempt }) => {
        console.info("ORHEX wallet verification", {
          correlationId,
          stage: "connection_started",
        });
        let expectedAddress: string | null = null;
        let expectedNetwork = "";
        const isCurrentWallet = () => {
          const current = adapter.getViewModel();
          return (
            verificationAttempt.current === attempt &&
            current.status === "connected" &&
            current.network === expectedNetwork &&
            canonicalWalletAddress(current.address) === expectedAddress
          );
        };

        await adapter.connect({ silent: false });
        const connected = completeWalletConnectionForCurrentAttempt({
          isCurrentAttempt,
          onConnected: () => {
            connectionStarting.current = false;
          },
        });
        if (!connected) {
          reportWalletDiagnostic(correlationId, {
            stage: "wallet_attempt_invalidated",
            after: "connection_response",
            current_attempt: false,
          });
          return;
        }
        const connectedWallet = adapter.getViewModel();
        if (connectedWallet.status !== "connected" || !connectedWallet.address) {
          throw new Error(connectedWallet.error || "The Sui wallet did not return a connected account.");
        }
        expectedAddress = canonicalWalletAddress(connectedWallet.address);
        expectedNetwork = connectedWallet.network;
        if (!expectedAddress || verificationAttempt.current !== attempt) {
          throw new Error("The wallet account changed during connection. Connect again to retry.");
        }

        const result = await completeWalletVerification({
          address: connectedWallet.address,
          network: expectedNetwork,
          isCurrentWallet,
          requestChallenge: requestWalletVerificationChallenge,
          signPersonalMessage: (message, onDiagnostic) =>
            adapter.signPersonalMessage(message, onDiagnostic),
          submitVerification: submitWalletVerification,
          correlationId,
          onPhase: (phase) => {
            setConnectionPhase(phase);
            console.info("ORHEX wallet verification", {
              correlationId,
              stage: phase,
            });
          },
          onDiagnostic: (event) => reportWalletDiagnostic(correlationId, event),
        });
        if (!result) return;
        setVerification(result);
        setConnectionPhase("idle");
        console.info("ORHEX wallet verification", {
          correlationId,
          stage: "verified",
        });
      },
      onFailure: async ({ correlationId, reason, isCurrentAttempt }) => {
        if (!isCurrentAttempt()) return;
        connectionStarting.current = false;
        console.warn("ORHEX wallet verification", {
          correlationId,
          stage: "failed",
          status: reason instanceof ApiError ? reason.status : null,
          code: reason instanceof ApiError ? reason.code : null,
        });
        setVerification(null);
        setConnectionPhase("failed");
        if (correlationId) {
          setVerificationError(verificationErrorMessage(reason));
          await completeWalletDisconnectForCurrentAttempt({
            isCurrentAttempt,
            disconnect: () => adapter.disconnect(),
            onDisconnectStarted: () => {
              intentionalDisconnect.current = true;
            },
            onDisconnected: () => {
              activeWalletIdentity.current = null;
            },
            onDisconnectFinished: () => {
              intentionalDisconnect.current = false;
            },
          });
        } else {
          setVerificationError("Could not start a secure wallet connection. Please retry.");
          return new Error("Could not start a secure wallet connection. Please retry.");
        }
      },
      onFinally: (attempt) => {
        if (verificationAttempt.current === attempt) {
          setVerificationBusy(false);
        }
      },
    });
  }, [adapter, visitorIdentityId]);

  const disconnect = useCallback(async () => {
    verificationAttempt.current += 1;
    verificationRequest.current += 1;
    verificationInFlight.current = false;
    setVerification(null);
    setVerificationBusy(false);
    setConnectionPhase("idle");
    setVerificationError(null);
    let revokeError: unknown = null;
    try {
      await revokeVerifiedWallet();
    } catch (reason) {
      revokeError = reason;
    } finally {
      intentionalDisconnect.current = true;
      try {
        await adapter.disconnect();
        activeWalletIdentity.current = null;
      } finally {
        intentionalDisconnect.current = false;
      }
    }
    if (revokeError) {
      const message = verificationErrorMessage(revokeError);
      setVerificationError(`Wallet disconnected locally, but server revocation failed: ${message}`);
      throw revokeError;
    }
  }, [adapter]);

  const verified =
    wallet.status === "connected" &&
    verification?.verified === true &&
    verification.chain === "sui" &&
    verification.network === wallet.network &&
    canonicalWalletAddress(verification.address) ===
      canonicalWalletAddress(wallet.address);
  const exposedStatus = verified
    ? "connected"
    : verificationBusy
    ? "connecting"
    : connectionPhase === "failed" || wallet.status === "error"
      ? "error"
      : "disconnected";
  const exposedAddress = verified ? wallet.address : null;

  const value = useMemo<WalletConnectionState>(
    () => ({
    ...wallet,
    status: exposedStatus,
    address: exposedAddress,
    error: verificationError || wallet.error,
    shortAddress: exposedAddress ? formatAddress(exposedAddress) : null,
    verified,
    verifiedAddress: verified ? verification?.address ?? null : null,
    verificationAvailable: Boolean(visitorIdentityId),
    verificationLoading,
    verificationBusy,
    verificationError,
    connectionPhase,
    connect,
    disconnect,
    }),
    [
    connect,
    connectionPhase,
    disconnect,
    exposedAddress,
    exposedStatus,
    verification?.address,
    verificationBusy,
    verificationError,
    verificationLoading,
    visitorIdentityId,
    verified,
    wallet,
    ],
  );

  return (
    <WalletConnectionContext.Provider value={value}>
      {children}
    </WalletConnectionContext.Provider>
  );
}

export function useWalletConnection(): WalletConnectionState {
  const value = useContext(WalletConnectionContext);
  if (!value) {
    throw new Error("useWalletConnection must be used within WalletConnectionProvider.");
  }
  return value;
}

export function formatAddress(address: string, visible = 4): string {
  if (!address) return "";
  if (address.length <= visible * 2 + 2) return address;
  const prefix = address.slice(0, visible);
  const suffix = address.slice(-visible);
  return `${prefix}\u2026${suffix}`;
}
