"use client";

import { Wallet2Icon, LogOutIcon, RefreshCwIcon } from "lucide-react";

import { useWalletConnection } from "@/lib/wallet/state";

export function WalletConnectionPanel() {
  const { status, address, shortAddress, providerId, network, capabilities, error, connect, disconnect } = useWalletConnection();
  const connected = status === "connected" && !!address;
  const pending = status === "connecting";

  return (
    <section className="surface-card p-5 sm:p-7">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]">
            <Wallet2Icon className="h-[18px] w-[18px]" />
          </span>
          <div>
            <p className="eyebrow">Wallet access</p>
            <h2 className="mt-2 text-[15px] font-semibold text-[#e3ebf3]">Sui connection</h2>
            {connected ? (
              <p className="mt-2 text-[12px] text-[#a2b4c4]">
                Connected to {network} as <span className="font-medium text-[#dfeaf6]">{shortAddress}</span>
              </p>
            ) : (
              <p className="mt-2 text-[12px] text-[#a2b4c4]">Connect an installed Sui wallet to authorize read-only public address access.</p>
            )}
          </div>
        </div>
        {connected ? (
          <button type="button" className="secondary-button" onClick={() => void disconnect()}>
            <LogOutIcon className="h-4 w-4" />
            Disconnect
          </button>
        ) : (
          <button type="button" className="primary-button" onClick={() => void connect()} disabled={pending}>
            {pending ? <RefreshCwIcon className="h-4 w-4 animate-spin" /> : <Wallet2Icon className="h-4 w-4" />}
            {pending ? "Connecting…" : "Connect Wallet"}
          </button>
        )}
      </div>

      {connected && (
        <div className="mt-5 rounded-xl border border-[#26323e] bg-[#0f151c] p-3.5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-[#8fa4b8]">Provider</p>
              <p className="mt-1 text-[12px] text-[#dfeaf6]">{providerId}</p>
            </div>
            <div>
              <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-[#8fa4b8]">Network</p>
              <p className="mt-1 text-[12px] text-[#dfeaf6]">{network}</p>
            </div>
          </div>
          <p className="mt-3 text-[11px] text-[#97a8b8]">Address: {address}</p>
          <p className="mt-2 text-[10px] text-[#7f8d9d]">Supported capabilities: {capabilities.length ? capabilities.join(", ") : "none"}</p>
        </div>
      )}

      <p className="mt-4 text-[10px] leading-5 text-[#7f8d9d]">
        Disconnecting here clears ORHEX&apos;s local connection state. It may not revoke this site&apos;s authorization in your wallet.
        A connected address is not proof of ownership.
      </p>

      {error && (
        <p role="alert" className="mt-4 rounded-xl border border-[#5a3739] bg-[#26191c] px-3 py-2.5 text-[12px] text-[#e2a8a4]">
          {error}
        </p>
      )}
    </section>
  );
}
