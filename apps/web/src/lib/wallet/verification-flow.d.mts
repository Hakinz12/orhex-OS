import type { VerifiedWalletStatus, WalletVerificationChallenge } from "../types";
import type { SignedPersonalMessage, WalletDiagnosticEvent } from "./types";

type WalletVerificationPhase = "awaiting_approval" | "verifying";

export function runWalletVerificationAttempt<T>(input: {
  inFlight: { current: boolean };
  attemptCounter: { current: number };
  createCorrelationId: () => string;
  onStart?: (attempt: number) => void;
  run: (input: {
    attempt: number;
    correlationId: string;
    isCurrentAttempt: () => boolean;
  }) => Promise<T>;
  onFailure?: (input: {
    attempt: number;
    correlationId: string | null;
    reason: unknown;
    isCurrentAttempt: () => boolean;
  }) => Error | void | Promise<Error | void>;
  onFinally?: (attempt: number) => void;
}): Promise<T | undefined>;

export function completeWalletConnectionForCurrentAttempt(input: {
  isCurrentAttempt: () => boolean;
  onConnected: () => void;
}): boolean;

export function completeWalletDisconnectForCurrentAttempt(input: {
  isCurrentAttempt: () => boolean;
  disconnect: () => Promise<void>;
  onDisconnectStarted: () => void;
  onDisconnected: () => void;
  onDisconnectFinished: () => void;
}): Promise<boolean>;

export function completeWalletVerification(input: {
  address: string;
  network: string;
  isCurrentWallet: () => boolean;
  correlationId: string;
  requestChallenge: (input: {
    address: string;
    network: string;
    correlationId: string;
  }) => Promise<WalletVerificationChallenge>;
  signPersonalMessage: (
    message: Uint8Array,
    onDiagnostic?: (event: WalletDiagnosticEvent) => void,
  ) => Promise<SignedPersonalMessage>;
  submitVerification: (input: {
    challenge_id: string;
    signature: string;
    signed_message: string;
    correlation_id: string;
  }) => Promise<VerifiedWalletStatus>;
  onPhase: (phase: WalletVerificationPhase) => void;
  onDiagnostic?: (event: WalletDiagnosticEvent) => void;
  now?: () => number;
}): Promise<VerifiedWalletStatus | null>;
