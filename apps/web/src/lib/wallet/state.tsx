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
  submitWalletVerification,
} from "@/lib/api";
import type { VerifiedWalletStatus, WalletVerificationChallenge } from "@/lib/types";
import { createSuiWalletAdapter } from "./sui";
import type {
  WalletConnectionAdapter,
  WalletConnectionViewModel,
} from "./types";

type WalletConnectionState = WalletConnectionViewModel & {
  shortAddress: string | null;
  verified: boolean;
  verifiedAddress: string | null;
  verificationAvailable: boolean;
  verificationLoading: boolean;
  verificationBusy: boolean;
  verificationError: string | null;
  connect: () => Promise<void>;
  disconnect: () => Promise<void>;
  verify: () => Promise<void>;
};

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

  const connect = useCallback(async () => {
    await instance.connect({ silent: false });
    setViewModel(instance.getViewModel());
  }, [instance]);

  const disconnect = useCallback(async () => {
    await instance.disconnect();
    setViewModel(instance.getViewModel());
  }, [instance]);

  const signPersonalMessage = useCallback(
    (message: Uint8Array) => instance.signPersonalMessage(message),
    [instance],
  );

  return { ...viewModel, connect, disconnect, signPersonalMessage };
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

export function WalletConnectionProvider({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const [adapter] = useState(() => createSuiWalletAdapter());
  const wallet = useAdapterState(adapter);
  const [verification, setVerification] = useState<VerifiedWalletStatus | null>(null);
  const [verificationLoading, setVerificationLoading] = useState(true);
  const [verificationBusy, setVerificationBusy] = useState(false);
  const [verificationError, setVerificationError] = useState<string | null>(null);
  const verificationRequest = useRef(0);
  const verificationInFlight = useRef(false);
  const verificationAttempt = useRef(0);
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
        activeWalletIdentity.current = nextIdentity;
        verificationAttempt.current += 1;
        verificationInFlight.current = false;
        setVerification(null);
        setVerificationBusy(false);
        setVerificationError(null);
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

  const verify = useCallback(async () => {
    if (verificationInFlight.current) return;
    if (wallet.status !== "connected" || !wallet.address) {
      setVerificationError("Connect a Sui wallet before verifying ownership.");
      return;
    }
    if (!visitorIdentityId) {
      setVerificationError("Wallet ownership verification requires an active visitor session.");
      return;
    }

    verificationInFlight.current = true;
    const attempt = ++verificationAttempt.current;
    const expectedAddress = canonicalWalletAddress(wallet.address);
    const expectedNetwork = wallet.network;
    setVerificationBusy(true);
    setVerificationError(null);
    const isCurrentWallet = () => {
      const current = adapter.getViewModel();
      return (
        verificationAttempt.current === attempt &&
        current.status === "connected" &&
        current.network === expectedNetwork &&
        canonicalWalletAddress(current.address) === expectedAddress
      );
    };
    try {
      const challenge: WalletVerificationChallenge =
        await requestWalletVerificationChallenge({
          network: wallet.network,
          address: wallet.address,
        });
      if (!isCurrentWallet()) return;
      if (
        challenge.chain !== "sui" ||
        challenge.network !== wallet.network ||
        canonicalWalletAddress(challenge.address) !==
          canonicalWalletAddress(wallet.address) ||
        !Number.isFinite(Date.parse(challenge.expires_at)) ||
        Date.parse(challenge.expires_at) <= Date.now() ||
        !/^[A-Za-z0-9+/]+={0,2}$/.test(challenge.message)
      ) {
        throw new Error("The verification challenge is invalid or expired. Request a new one.");
      }
      const message = Uint8Array.from(
        atob(challenge.message),
        (character) => character.charCodeAt(0),
      );
      const signature = await wallet.signPersonalMessage(message);
      if (!isCurrentWallet()) return;
      const result = await submitWalletVerification({
        challenge_id: challenge.challenge_id,
        signature,
      });
      if (!isCurrentWallet()) return;
      if (
        !result.verified ||
        result.chain !== "sui" ||
        result.network !== wallet.network ||
        canonicalWalletAddress(result.address) !==
          canonicalWalletAddress(wallet.address)
      ) {
        throw new Error("The wallet signature did not verify for this account and network.");
      }
      setVerification(result);
    } catch (reason) {
      if (verificationAttempt.current !== attempt) return;
      setVerificationError(verificationErrorMessage(reason));
      throw reason;
    } finally {
      if (verificationAttempt.current === attempt) {
        verificationInFlight.current = false;
        setVerificationBusy(false);
      }
    }
  }, [adapter, visitorIdentityId, wallet]);

  const verified =
    wallet.status === "connected" &&
    verification?.verified === true &&
    verification.chain === "sui" &&
    verification.network === wallet.network &&
    canonicalWalletAddress(verification.address) ===
      canonicalWalletAddress(wallet.address);

  const value = useMemo<WalletConnectionState>(
    () => ({
      ...wallet,
      shortAddress: wallet.address ? formatAddress(wallet.address) : null,
      verified,
      verifiedAddress: verified ? verification?.address ?? null : null,
      verificationAvailable: Boolean(visitorIdentityId),
      verificationLoading,
      verificationBusy,
      verificationError,
      verify,
    }),
    [
      verification?.address,
      verificationBusy,
      verificationError,
      verificationLoading,
      visitorIdentityId,
      verify,
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
