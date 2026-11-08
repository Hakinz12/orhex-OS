"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, AlertTriangle, RefreshCw, ShieldCheck, Wallet2 } from "lucide-react";
import { PageHeading } from "@/components/ui";
import { useSession } from "@/components/session-provider";
import { useWalletConnection } from "@/lib/wallet/state";

type Intelligence = {
  wallet_address: string;
  network: string;
  fetched_at: string;
  checkpoint: number | null;
  snapshot_consistency: "independent_provider_reads";
  data_status: "complete" | "partial" | "unavailable";
  unavailable_sections: Array<"balances" | "activity" | "checkpoint">;
  provider_errors: Array<{ section: string; code: string }>;
  portfolio: {
    known_token_count: number;
    holdings: Array<{ coin_type: string; symbol: string | null; amount: string; decimals: number | null }>;
  } | null;
  activity: { sample_size: number; successful: number; failed: number; unknown_status: number; transactions: Array<{ digest: string; status: string; timestamp_ms: number | null }> } | null;
  insights: Array<{ severity: string; code: string; title: string; detail: string }>;
  limitations: string[];
};

type LoadIssue = {
  kind: "authentication" | "invalid_address" | "rate_limit" | "provider" | "service" | "network";
  message: string;
};

function isIntelligence(value: unknown): value is Intelligence {
  if (!value || typeof value !== "object") return false;
  const result = value as Partial<Intelligence>;
  return typeof result.wallet_address === "string"
    && typeof result.network === "string"
    && typeof result.fetched_at === "string"
    && (result.checkpoint === null || Number.isInteger(result.checkpoint))
    && result.snapshot_consistency === "independent_provider_reads"
    && ["complete", "partial", "unavailable"].includes(String(result.data_status))
    && Array.isArray(result.unavailable_sections)
    && Array.isArray(result.provider_errors)
    && (result.portfolio === null || (!!result.portfolio
      && Number.isInteger(result.portfolio.known_token_count)
      && Array.isArray(result.portfolio.holdings)
      && result.portfolio.holdings.every((holding) =>
      !!holding
      && typeof holding.coin_type === "string"
      && typeof holding.amount === "string"
      && (holding.symbol === null || typeof holding.symbol === "string")
      && (holding.decimals === null || Number.isInteger(holding.decimals)))))
    && (result.activity === null || (!!result.activity
      && Number.isInteger(result.activity.sample_size)
      && Number.isInteger(result.activity.successful)
      && Number.isInteger(result.activity.failed)
      && Number.isInteger(result.activity.unknown_status)
      && Array.isArray(result.activity.transactions)
      && result.activity.transactions.every((transaction) =>
      !!transaction
      && typeof transaction.digest === "string"
      && typeof transaction.status === "string"
      && (transaction.timestamp_ms === null || Number.isFinite(transaction.timestamp_ms)))))
    && Array.isArray(result.insights)
    && result.insights.every((insight) =>
      !!insight
      && typeof insight.code === "string"
      && typeof insight.title === "string"
      && typeof insight.detail === "string")
    && Array.isArray(result.limitations)
    && result.limitations.every((limitation) => typeof limitation === "string");
}

function issueFor(status: number, message: string): LoadIssue {
  if (status === 401) return { kind: "authentication", message: "Your session is no longer authorized. Refresh the page or start a new session." };
  if (status === 422) return { kind: "invalid_address", message: "The connected wallet address is not a valid Sui address." };
  if (status === 429) return { kind: "rate_limit", message: "Wallet analysis is temporarily rate limited. Wait a moment, then retry." };
  if (status === 502) return { kind: "provider", message: "The provider rejected the request or returned invalid data. Check provider configuration, then retry." };
  if (status === 503) return { kind: "service", message: "Blockchain intelligence is not configured or is temporarily unavailable." };
  return { kind: "network", message: message || "A network error prevented wallet analysis." };
}

export default function BlockchainIntelligencePage() {
  const wallet = useWalletConnection();
  const { refresh: refreshSession } = useSession();
  const [data, setData] = useState<Intelligence | null>(null);
  const [loading, setLoading] = useState(false);
  const [issue, setIssue] = useState<LoadIssue | null>(null);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    if (!wallet.address || wallet.status !== "connected") return;
    const request = ++requestId.current;
    setLoading(true);
    setIssue(null);
    try {
      const response = await fetch(
        `/api/bff/blockchain/intelligence/${encodeURIComponent(wallet.address)}`,
        { cache: "no-store", signal: AbortSignal.timeout(18_000) },
      );
      let payload: unknown;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok) {
        const message = payload && typeof payload === "object" && "message" in payload && typeof payload.message === "string"
          ? payload.message
          : "";
        throw Object.assign(new Error(message), { status: response.status });
      }
      if (!isIntelligence(payload) || payload.wallet_address !== wallet.address) {
        throw Object.assign(new Error("The intelligence service returned an invalid response."), { status: 502 });
      }
      if (request === requestId.current) setData(payload);
    } catch (reason) {
      if (request !== requestId.current) return;
      const status = reason && typeof reason === "object" && "status" in reason && typeof reason.status === "number"
        ? reason.status
        : 0;
      setIssue(issueFor(status, reason instanceof Error ? reason.message : ""));
    } finally {
      if (request === requestId.current) setLoading(false);
    }
  }, [wallet.address, wallet.status]);

  useEffect(() => {
    setData(null);
    setIssue(null);
    if (wallet.address && wallet.status === "connected") void load();
    return () => {
      requestId.current += 1;
    };
  }, [load, wallet.address, wallet.status]);

  const connected = wallet.status === "connected" && !!wallet.address;
  const connectionFailed = wallet.status === "error";
  const displayData = connected && data?.wallet_address === wallet.address ? data : null;
  const retryable = issue?.kind === "rate_limit"
    || issue?.kind === "provider"
    || issue?.kind === "service"
    || issue?.kind === "network";

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Tatum · Sui · Read-only analysis"
        title="Advanced Blockchain Intelligence"
        description="An explainable snapshot of wallet holdings, recent transaction outcomes and the latest indexed checkpoint. No signing or fund movement."
        action={connected ? (
          <button className="secondary-button" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            {loading ? "Refreshing…" : "Refresh analysis"}
          </button>
        ) : undefined}
      />
      {connectionFailed && (
        <div role="alert" className="surface-card flex flex-wrap items-center justify-between gap-3 p-5 text-sm text-[#e2b0ad]">
          <p>Wallet connection failed: {wallet.error || "Reconnect your Sui wallet to continue."}</p>
          <button type="button" className="secondary-button" onClick={() => void wallet.connect()}>
            Retry connection
          </button>
        </div>
      )}
      {!connected && !connectionFailed && (
        <section className="surface-card p-6">
          <div className="flex items-center gap-3 text-[#c9d8e5]"><Wallet2 className="h-5 w-5" /><h2 className="font-semibold">Connect a Sui wallet</h2></div>
          <p className="mt-2 text-sm text-[#8796a5]">Connect your wallet from the Sui Wallet page, then return here to analyse its public on-chain data.</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <a className="primary-button inline-flex" href="/sui">Open Sui Wallet</a>
          </div>
        </section>
      )}
      {issue && (
        <div role="alert" className="surface-card flex flex-col gap-3 p-5 text-sm text-[#e2b0ad] sm:flex-row sm:items-center sm:justify-between">
          <p>{issue.message}</p>
          {issue.kind === "authentication" ? (
            <button type="button" className="secondary-button shrink-0" onClick={() => void refreshSession()}>
              Check session
            </button>
          ) : retryable && (
            <button type="button" className="secondary-button shrink-0" onClick={() => void load()} disabled={loading}>
              <RefreshCw className="h-4 w-4" />Retry
            </button>
          )}
        </div>
      )}
      {displayData && displayData.data_status !== "complete" && (
        <div role="status" className="surface-card flex flex-col gap-2 p-5 text-sm text-[#ddbd86]">
          <p>
            {displayData.data_status === "partial"
              ? `Some independent provider reads are unavailable: ${displayData.unavailable_sections.join(", ")}.`
              : "Blockchain provider data is currently unavailable."}
          </p>
          {displayData.provider_errors.map((error) => (
            <p key={`${error.section}:${error.code}`} className="text-xs text-[#aebbc7]">
              {error.section}: {error.code.replaceAll("_", " ")}
            </p>
          ))}
          <button type="button" className="secondary-button self-start" onClick={() => void load()} disabled={loading}>
            <RefreshCw className="h-4 w-4" />Retry analysis
          </button>
        </div>
      )}
      {loading && !displayData && (
        <div className="surface-card animate-pulse p-8 text-sm text-[#9aabba]" role="status">Analysing public wallet data…</div>
      )}
      {displayData && (
        <>
          <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Metric label="Network" value={displayData.network} />
            <Metric label="Latest checkpoint" value={displayData.checkpoint?.toLocaleString() ?? "Unavailable"} />
            <Metric label="Known token types" value={displayData.portfolio ? String(displayData.portfolio.known_token_count) : "Unavailable"} />
            <Metric label="Recent transactions" value={displayData.activity ? String(displayData.activity.sample_size) : "Unavailable"} />
          </section>
          <section className="grid gap-5 xl:grid-cols-2">
            <div className="surface-card p-5 sm:p-6">
              <h2 className="text-sm font-semibold text-[#e3ebf2]">Portfolio composition</h2>
              <p className="mt-1 text-xs text-[#8494a4]">Token amounts where provider decimals are available. Not a fiat valuation; amounts are not combined or compared across token types.</p>
              <div className="mt-5 space-y-3">
                {!displayData.portfolio && <p className="text-sm text-[#ddbd86]">Token balances are unavailable; no holdings are inferred.</p>}
                {displayData.portfolio?.holdings.length === 0 && <p className="text-sm text-[#8292a2]">No token balances were returned for this address.</p>}
                {displayData.portfolio?.holdings.map((holding) => (
                  <div key={holding.coin_type} className="flex items-center justify-between gap-4 border-b border-[#202a34] pb-3">
                    <div className="min-w-0">
                      <p className="text-sm text-[#dce5ed]">{holding.symbol || "Unknown token"}</p>
                      <p className="truncate text-[10px] text-[#718294]">{holding.coin_type}</p>
                    </div>
                    <p className="shrink-0 font-mono text-sm text-[#c5dced]">{holding.amount}</p>
                  </div>
                ))}
              </div>
            </div>
            <div className="surface-card p-5 sm:p-6">
              <h2 className="text-sm font-semibold text-[#e3ebf2]">Network activity</h2>
              {displayData.activity ? (
                <div className="mt-4 grid grid-cols-3 gap-2">
                  <Mini label="Successful" value={displayData.activity.successful} />
                  <Mini label="Failed" value={displayData.activity.failed} />
                  <Mini label="Unknown" value={displayData.activity.unknown_status} />
                </div>
              ) : <p className="mt-4 text-sm text-[#ddbd86]">Recent activity is unavailable.</p>}
              <div className="mt-5 space-y-3">
                {displayData.activity?.transactions.map((tx) => (
                  <div key={tx.digest} className="flex items-center justify-between gap-3 border-b border-[#202a34] pb-3">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-xs text-[#c4d3df]">{tx.digest}</p>
                      <p className="mt-1 text-[10px] text-[#758697]">{tx.timestamp_ms ? new Date(tx.timestamp_ms).toLocaleString() : "Timestamp unavailable"}</p>
                    </div>
                    <span className="rounded-lg border border-[#344554] px-2 py-1 text-[10px] capitalize text-[#b8cad9]">{tx.status}</span>
                  </div>
                ))}
                {displayData.activity?.transactions.length === 0 && <p className="text-sm text-[#8292a2]">No recent transaction activity was returned.</p>}
              </div>
            </div>
          </section>
          <section className="surface-card p-5 sm:p-6">
            <div className="flex items-center gap-2"><Activity className="h-4 w-4 text-[#b7d7f0]" /><h2 className="text-sm font-semibold text-[#e3ebf2]">Intelligence signals</h2></div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {displayData.insights.map((insight) => (
                <div key={insight.code} className="rounded-xl border border-[#293744] bg-[#101821] p-4">
                  <div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-[#b9a77c]" /><p className="text-sm font-medium text-[#dbe5ed]">{insight.title}</p></div>
                  <p className="mt-2 text-xs leading-5 text-[#91a0ae]">{insight.detail}</p>
                </div>
              ))}
              {!displayData.insights.length && <p className="text-sm text-[#91a0ae]">{displayData.activity ? "No heuristic signals were triggered by this snapshot." : "Signals are unavailable because transaction activity could not be read."}</p>}
            </div>
          </section>
          <section className="surface-card p-5">
            <div className="flex items-center gap-2 text-[#b9cbd9]"><ShieldCheck className="h-4 w-4" /><h2 className="text-xs font-semibold">Analysis boundaries</h2></div>
            <ul className="mt-3 list-disc space-y-1 pl-5 text-[11px] leading-5 text-[#8292a2]">{displayData.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
            <p className="mt-3 text-[10px] text-[#68798a]">Analysis generated {new Date(displayData.fetched_at).toLocaleString()} · {displayData.wallet_address}</p>
          </section>
          <p className="surface-card p-4 text-xs leading-5 text-[#aebbc7]">
            Balances, transaction activity, and checkpoint information were fetched independently. They may reflect different chain states and are not guaranteed to form one consistent snapshot.
          </p>
        </>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="surface-card p-4"><p className="text-[10px] font-semibold uppercase tracking-wider text-[#77899a]">{label}</p><p className="mt-2 text-xl font-semibold text-[#dce7ef]">{value}</p></div>;
}

function Mini({ label, value }: { label: string; value: number }) {
  return <div className="rounded-xl border border-[#263440] bg-[#101821] p-3"><p className="text-[10px] text-[#8292a2]">{label}</p><p className="mt-1 text-lg font-semibold text-[#dce7ef]">{value}</p></div>;
}
