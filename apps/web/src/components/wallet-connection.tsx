"use client";

import { useRef, useState } from "react";
import { BadgeCheck, LoaderCircle, LogOut, ShieldAlert, Wallet2 } from "lucide-react";

import { useWalletConnection } from "@/lib/wallet/state";

export function WalletConnectionPanel() {
  const {
    status,
    address,
    shortAddress,
    network,
    error,
    verified,
    verificationAvailable,
    verificationLoading,
    verificationBusy,
    verificationError,
    connect,
    disconnect,
    verify,
  } = useWalletConnection();
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const actionInFlight = useRef(false);
  const connected = status === "connected" && !!address;
  const pending = status === "connecting" || verificationLoading;

  async function runWalletAction(action: () => Promise<void>) {
    if (actionInFlight.current) return;
    actionInFlight.current = true;
    setActionBusy(true);
    setActionError(null);
    try {
      await action();
    } catch (reason) {
      setActionError(
        reason instanceof Error
          ? reason.message
          : "The wallet action could not be completed.",
      );
    } finally {
      actionInFlight.current = false;
      setActionBusy(false);
    }
  }

  return (
    <div className="flex min-w-0 flex-col items-start gap-1.5 sm:items-end">
      <div className="flex max-w-full flex-wrap items-center justify-start gap-2 sm:justify-end">
        {!connected ? (
          <button
            type="button"
            className="primary-button min-h-10"
            onClick={() => void runWalletAction(connect)}
            disabled={pending || actionBusy}
          >
            {pending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Wallet2 className="h-4 w-4" />}
            {status === "connecting" || actionBusy ? "Connecting…" : "Connect Wallet"}
          </button>
        ) : verified ? (
          <div
            className="inline-flex min-h-10 max-w-full items-center gap-2 rounded-xl border border-[#355244] bg-[#14231d] px-3 py-2 text-left"
            aria-label={`Wallet ${shortAddress} authenticated`}
          >
            <BadgeCheck className="h-4 w-4 shrink-0 text-[#9bc6a7]" />
            <span className="min-w-0">
              <span className="block truncate text-[11px] font-semibold text-[#d3e8d8]">{shortAddress}</span>
              <span className="block text-[9px] text-[#99b7a2]">Authenticated on {network}</span>
            </span>
          </div>
        ) : (
          <button
            type="button"
            className="primary-button min-h-10"
            onClick={() => void runWalletAction(verify)}
            disabled={verificationBusy || actionBusy || verificationLoading || !verificationAvailable}
            title={!verificationAvailable ? "Wallet verification requires an active visitor session." : undefined}
          >
            {verificationBusy || actionBusy ? (
              <LoaderCircle className="h-4 w-4 animate-spin" />
            ) : (
              <ShieldAlert className="h-4 w-4" />
            )}
            {verificationBusy || actionBusy ? "Verifying…" : "Verify Wallet"}
          </button>
        )}
        {connected && (
          <button
            type="button"
            className="secondary-button min-h-10 px-3"
            onClick={() => void runWalletAction(disconnect)}
            disabled={actionBusy || verificationBusy}
            aria-label="Disconnect wallet"
            title="Disconnect wallet"
          >
            {actionBusy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <LogOut className="h-4 w-4" />}
            <span className="hidden sm:inline">{actionBusy ? "Disconnecting…" : "Disconnect"}</span>
          </button>
        )}
      </div>
      {connected && !verified && (
        <p className="max-w-[280px] text-[10px] leading-4 text-[#8998a7] sm:text-right">
          Connected as {shortAddress}; connection alone does not verify ownership.
          {!verificationAvailable && " An active visitor session is required to verify."}
        </p>
      )}
      {(actionError || verificationError || error) && (
        <p role="alert" className="max-w-[320px] text-[10px] leading-4 text-[#e2a8a4] sm:text-right">
          {actionError || verificationError || error}
        </p>
      )}
    </div>
  );
}
