"use client";

import {
  AlertCircle,
  ArrowDownToLine,
  ChevronDown,
  CircleCheck,
  Coins,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Wallet2,
} from "lucide-react";
import { useEffect, useMemo, useSyncExternalStore } from "react";

import { PageHeading, SectionHeading } from "@/components/ui";
import { useWalletConnection } from "@/lib/wallet/state";
import {
  formatCoinBalance,
  formatCoinHolding,
  getCoinDisplayName,
  getNativeSuiBalance,
  SuiWalletDataController,
  type SuiWalletData,
} from "@/lib/sui-data";

function disconnectedData(): SuiWalletData {
  return {
    sui_address: "",
    network: null,
    balances: [],
    objects: [],
    balanceStatus: "idle",
    objectsStatus: "idle",
    balanceError: null,
    objectsError: null,
    balanceCursor: null,
    objectsCursor: null,
    hasMoreBalances: false,
    hasMoreObjects: false,
  };
}

function displayAddress(address: string): string {
  return address.length > 22 ? `${address.slice(0, 10)}…${address.slice(-8)}` : address;
}

function LoadingRows({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-3" aria-label="Loading wallet data">
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className="flex items-center gap-3 rounded-xl border border-[#202a34] bg-[#101720] p-4">
          <div className="skeleton h-9 w-9 rounded-xl" />
          <div className="flex-1 space-y-2">
            <div className="skeleton h-3 w-1/3" />
            <div className="skeleton h-2.5 w-1/2" />
          </div>
          <div className="skeleton h-3 w-20" />
        </div>
      ))}
    </div>
  );
}

function InlineError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="flex flex-col gap-3 rounded-xl border border-[#583739] bg-[#24191d] p-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="flex items-start gap-2 text-[12px] leading-5 text-[#e2b0ad]">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
        {message}
      </p>
      <button type="button" className="secondary-button shrink-0 self-start sm:self-auto" onClick={onRetry}>
        <RefreshCw className="h-3.5 w-3.5" />
        Retry
      </button>
    </div>
  );
}

export default function SuiPage() {
  const wallet = useWalletConnection();
  const controller = useMemo(() => new SuiWalletDataController(), []);
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
    controller.getSnapshot,
  );
  const connected = wallet.status === "connected" && !!wallet.address;
  const visibleState = connected && state.sui_address === wallet.address
    ? state
    : connected
      ? {
          ...disconnectedData(),
          sui_address: wallet.address ?? "",
          balanceStatus: "loading" as const,
          objectsStatus: "loading" as const,
        }
      : disconnectedData();
  const nativeBalance = getNativeSuiBalance(visibleState.balances);

  useEffect(() => {
    controller.setAddress(connected ? wallet.address : null);
  }, [controller, connected, wallet.address]);

  useEffect(() => () => controller.dispose(), [controller]);

  const walletStatus = connected
    ? "Connected"
    : wallet.status === "connecting"
      ? "Connecting"
      : wallet.status === "error"
        ? "Connection error"
        : "Disconnected";

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Sui · Read-only intelligence"
        title="Your Sui wallet, in context."
        description="Review public token balances and owned objects. ORHEX never requests signing or transaction approval here."
        action={connected ? (
          <button
            type="button"
            className="secondary-button"
            onClick={() => void controller.refresh()}
            disabled={visibleState.balanceStatus === "loading" || visibleState.objectsStatus === "loading"}
          >
            <RefreshCw className={`h-4 w-4 ${visibleState.balanceStatus === "loading" || visibleState.objectsStatus === "loading" ? "animate-spin" : ""}`} />
            Refresh data
          </button>
        ) : undefined}
      />

      <section className="surface-card overflow-hidden">
        <div className="flex flex-col gap-5 p-5 sm:p-7 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-start gap-4">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-[#344554] bg-[#17222d] text-[#c5dced]">
              <Wallet2 className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="eyebrow">Wallet connection</p>
              <h2 className="mt-2 text-[15px] font-semibold text-[#e4ebf1]">{walletStatus}</h2>
              {connected && wallet.address ? (
                <p className="mt-2 break-all font-mono text-[12px] text-[#a6b8c8]" title={wallet.address}>
                  {displayAddress(wallet.address)}
                </p>
              ) : (
                <p className="mt-2 max-w-xl text-[12px] leading-5 text-[#8796a5]">
                  Connect your existing Sui wallet to view public chain data.
                </p>
              )}
            </div>
          </div>

          {connected ? (
            <button type="button" className="secondary-button self-start lg:self-auto" onClick={() => void wallet.disconnect()}>
              Disconnect from ORHEX
            </button>
          ) : (
            <button
              type="button"
              className="primary-button self-start lg:self-auto"
              onClick={() => void wallet.connect()}
              disabled={wallet.status === "connecting"}
            >
              {wallet.status === "connecting" ? (
                <LoaderCircle className="h-4 w-4 animate-spin" />
              ) : (
                <Wallet2 className="h-4 w-4" />
              )}
              {wallet.status === "connecting" ? "Connecting…" : "Connect Wallet"}
            </button>
          )}
        </div>

        <div className="grid gap-px border-t border-[#202a34] bg-[#202a34] sm:grid-cols-3">
          <div className="bg-[#111821] px-5 py-4 sm:px-7">
            <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-[#7f91a2]">Wallet network</p>
            <p className="mt-2 text-[12px] capitalize text-[#d3dde6]">{connected ? wallet.network : "—"}</p>
          </div>
          <div className="bg-[#111821] px-5 py-4 sm:px-7">
            <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-[#7f91a2]">Data source network</p>
            <p className="mt-2 text-[12px] capitalize text-[#d3dde6]">
              {connected
                ? visibleState.network ?? (visibleState.balanceStatus === "loading" ? "Checking…" : "Unavailable")
                : "—"}
            </p>
          </div>
          <div className="bg-[#111821] px-5 py-4 sm:px-7">
            <p className="text-[10px] font-semibold uppercase tracking-[0.13em] text-[#7f91a2]">Connection</p>
            <p className={`mt-2 inline-flex items-center gap-2 text-[12px] ${connected ? "text-[#b8d7c6]" : "text-[#aab5bf]"}`}>
              <span className="status-dot" data-status={connected ? "connected" : wallet.status === "error" ? "error" : "unknown"} />
              {walletStatus}
            </p>
          </div>
        </div>

        {wallet.error && (
          <div role="alert" className="border-t border-[#503437] bg-[#21181b] px-5 py-3 text-[12px] text-[#e0aaa7] sm:px-7">
            {wallet.error}
          </div>
        )}
      </section>

      {!connected ? (
        <section className="surface-card flex flex-col items-center px-6 py-12 text-center">
          <ShieldCheck className="h-8 w-8 text-[#8fa6b9]" />
          <h2 className="mt-4 text-[14px] font-semibold text-[#dfe7ed]">Read-only by design</h2>
          <p className="mt-2 max-w-md text-[12px] leading-6 text-[#8795a3]">
            A connected public address lets ORHEX request public Sui data. It does not prove ownership, and no wallet signature is requested.
          </p>
        </section>
      ) : (
        <>
          <section className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(280px,0.7fr)]">
            <article className="surface-card relative overflow-hidden p-5 sm:p-7">
              <div className="pointer-events-none absolute -right-10 -top-20 h-56 w-56 rounded-full bg-[#41617b]/10 blur-3xl" />
              <div className="relative flex items-start justify-between gap-4">
                <div>
                  <p className="eyebrow">Native balance</p>
                  <h2 className="mt-2 text-[14px] font-semibold text-[#dce5ec]">Sui (SUI)</h2>
                </div>
                <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-[#344554] bg-[#17222d] text-[#bdd4e5]">
                  <Coins className="h-[18px] w-[18px]" />
                </span>
              </div>
              {visibleState.balanceStatus === "loading" ? (
                <div className="skeleton mt-8 h-9 w-48" />
              ) : visibleState.balanceStatus === "error" && !nativeBalance ? (
                <p className="mt-6 text-[12px] text-[#dca9a6]">Native balance is unavailable.</p>
              ) : nativeBalance ? (
                <p className="relative mt-6 break-all font-display text-[32px] font-semibold tracking-[-0.04em] text-[#f0f3f6] sm:text-[38px]">
                  {formatCoinBalance(nativeBalance)}
                  {nativeBalance.amount !== null && <span className="text-[15px] font-medium tracking-normal text-[#9cafbf]"> SUI</span>}
                </p>
              ) : (
                <p className="mt-6 text-[12px] text-[#8796a5]">
                  {visibleState.balanceStatus === "ready" && visibleState.hasMoreBalances
                    ? "SUI is not on this page of balances yet."
                    : visibleState.balanceStatus === "ready"
                      ? "No native SUI balance was returned."
                      : "Loading balance…"}
                </p>
              )}
              <p className="relative mt-3 text-[10px] leading-5 text-[#78899a]">
                {nativeBalance?.amount === null
                  ? "Provider metadata did not include decimals; value is shown in raw units."
                  : "Public chain balance · no price or USD estimate"}
              </p>
            </article>

            <article className="surface-card flex flex-col justify-between p-5 sm:p-7">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="eyebrow">Connected account</p>
                  <p className="mt-2 text-[13px] font-semibold text-[#e0e8ee]">{wallet.providerId}</p>
                </div>
                <CircleCheck className="h-5 w-5 text-[#91bea3]" />
              </div>
              <div className="mt-6">
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#7f91a2]">Public address</p>
                <p className="mt-2 break-all font-mono text-[11px] leading-5 text-[#a9bac8]">{wallet.address}</p>
              </div>
              <p className="mt-4 text-[10px] leading-5 text-[#78899a]">Connection is not cryptographic proof of address ownership.</p>
            </article>
          </section>

          <section className="surface-card p-5 sm:p-7">
            <SectionHeading
              title="Token holdings"
              detail="Public balances from the current page. No token prices or fiat values are estimated."
            />
            <div className="mt-5">
              {visibleState.balanceStatus === "loading" ? (
                <LoadingRows />
              ) : visibleState.balanceStatus === "error" && visibleState.balances.length === 0 ? (
                <InlineError message={visibleState.balanceError ?? "Token balances could not be loaded."} onRetry={() => void controller.refresh()} />
              ) : visibleState.balances.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[#2b3743] px-5 py-10 text-center">
                  <ArrowDownToLine className="mx-auto h-5 w-5 text-[#8195a8]" />
                  <p className="mt-3 text-[13px] font-semibold text-[#d8e1e8]">No token balances on this page</p>
                  <p className="mt-1 text-[11px] text-[#8392a1]">
                    {visibleState.hasMoreBalances ? "Continue to the next page to view more holdings." : "This address has no returned coin balances."}
                  </p>
                </div>
              ) : (
                <div className="divide-y divide-[#202a34]">
                  {visibleState.balances.map((balance, index) => {
                    return (
                      <article key={`${balance.coin_type}-${index}`} className="flex flex-col gap-3 py-4 first:pt-1 last:pb-1 sm:flex-row sm:items-center sm:justify-between">
                        <div className="flex min-w-0 items-center gap-3">
                          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[#293744] bg-[#17212b] text-[#afc8dc]">
                            <Coins className="h-4 w-4" />
                          </span>
                          <div className="min-w-0">
                            <p className="text-[12px] font-semibold text-[#e0e7ed]">{getCoinDisplayName(balance)}</p>
                            <p className="mt-1 break-all font-mono text-[10px] text-[#778899]">{balance.coin_type}</p>
                          </div>
                        </div>
                        <div className="pl-12 text-left sm:pl-0 sm:text-right">
                          <p className="break-all text-[13px] font-semibold text-[#dce5ec]">
                            {formatCoinHolding(balance)}
                          </p>
                          <p className="mt-1 text-[10px] text-[#7f8e9c]">
                            {balance.symbol ?? "Symbol unavailable"}
                            {balance.decimals === null ? " · decimals unavailable" : ` · ${balance.decimals} decimals`}
                          </p>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
              {visibleState.balanceStatus === "error" && visibleState.balances.length > 0 && (
                <div className="mt-4">
                  <InlineError message={visibleState.balanceError ?? "The next balance page could not be loaded."} onRetry={() => void controller.loadMore("balances")} />
                </div>
              )}
              {visibleState.hasMoreBalances && visibleState.balanceStatus !== "error" && (
                <button
                  type="button"
                  className="secondary-button mt-5 w-full sm:w-auto"
                  onClick={() => void controller.loadMore("balances")}
                  disabled={visibleState.balanceStatus === "loading-more"}
                >
                  {visibleState.balanceStatus === "loading-more" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ChevronDown className="h-4 w-4" />}
                  {visibleState.balanceStatus === "loading-more" ? "Loading…" : "Load more tokens"}
                </button>
              )}
            </div>
          </section>

          <section className="surface-card p-5 sm:p-7">
            <SectionHeading
              title="Owned objects"
              detail={`${visibleState.objects.length} object${visibleState.objects.length === 1 ? "" : "s"} on the current page`}
            />
            <div className="mt-5">
              {visibleState.objectsStatus === "loading" ? (
                <LoadingRows count={2} />
              ) : visibleState.objectsStatus === "error" && visibleState.objects.length === 0 ? (
                <InlineError message={visibleState.objectsError ?? "Owned objects could not be loaded."} onRetry={() => void controller.refresh()} />
              ) : visibleState.objects.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[#2b3743] px-5 py-10 text-center">
                  <p className="text-[13px] font-semibold text-[#d8e1e8]">No owned objects on this page</p>
                  <p className="mt-1 text-[11px] text-[#8392a1]">
                    {visibleState.hasMoreObjects ? "Continue to the next page to view more objects." : "No objects were returned for this address."}
                  </p>
                </div>
              ) : (
                <div className="grid gap-3 md:grid-cols-2">
                  {visibleState.objects.map((object) => (
                    <article key={object.object_id} className="min-w-0 rounded-xl border border-[#25313c] bg-[#101720] p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-[12px] font-semibold text-[#dde5eb]">{object.type ?? "Object type unavailable"}</p>
                          <p className="mt-2 break-all font-mono text-[10px] leading-5 text-[#8b9aaa]">{object.object_id}</p>
                        </div>
                        {object.version && (
                          <span className="shrink-0 rounded-full border border-[#2b3945] bg-[#17212b] px-2.5 py-1 text-[9px] text-[#9aabba]">
                            v{object.version}
                          </span>
                        )}
                      </div>
                      {object.digest && (
                        <p className="mt-3 truncate border-t border-[#202a34] pt-3 font-mono text-[9px] text-[#68798a]" title={object.digest}>
                          Digest · {object.digest}
                        </p>
                      )}
                    </article>
                  ))}
                </div>
              )}
              {visibleState.objectsStatus === "error" && visibleState.objects.length > 0 && (
                <div className="mt-4">
                  <InlineError message={visibleState.objectsError ?? "The next object page could not be loaded."} onRetry={() => void controller.loadMore("objects")} />
                </div>
              )}
              {visibleState.hasMoreObjects && visibleState.objectsStatus !== "error" && (
                <button
                  type="button"
                  className="secondary-button mt-5 w-full sm:w-auto"
                  onClick={() => void controller.loadMore("objects")}
                  disabled={visibleState.objectsStatus === "loading-more"}
                >
                  {visibleState.objectsStatus === "loading-more" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ChevronDown className="h-4 w-4" />}
                  {visibleState.objectsStatus === "loading-more" ? "Loading…" : "Load more objects"}
                </button>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
