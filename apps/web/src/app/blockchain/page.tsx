"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, AlertTriangle, RefreshCw, ShieldCheck, Wallet2 } from "lucide-react";
import { PageHeading } from "@/components/ui";
import { useWalletConnection } from "@/lib/wallet/state";

type Intelligence = {
  wallet_address: string;
  network: string;
  fetched_at: string;
  checkpoint: number;
  snapshot_consistency: "independent_provider_reads";
  portfolio: {
    known_token_count: number;
    holdings: Array<{ coin_type: string; symbol: string | null; amount: string; decimals: number | null }>;
  };
  activity: { sample_size: number; successful: number; failed: number; unknown_status: number; transactions: Array<{ digest: string; status: string; timestamp_ms: number | null }> };
  insights: Array<{ severity: string; code: string; title: string; detail: string }>;
  limitations: string[];
};

export default function BlockchainIntelligencePage() {
  const wallet = useWalletConnection();
  const [data, setData] = useState<Intelligence | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!wallet.address || wallet.status !== "connected") return;
    setLoading(true); setError(null);
    try {
      const response = await fetch(`/api/bff/blockchain/intelligence/${encodeURIComponent(wallet.address)}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(typeof payload.message === "string" ? payload.message : "Wallet intelligence could not be loaded.");
      setData(payload as Intelligence);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Wallet intelligence could not be loaded.");
    } finally { setLoading(false); }
  }, [wallet.address, wallet.status]);

  useEffect(() => { setData(null); void load(); }, [load]);

  return (
    <div className="space-y-8">
      <PageHeading eyebrow="Tatum · Sui · Read-only analysis" title="Advanced Blockchain Intelligence" description="An explainable snapshot of wallet holdings, recent transaction outcomes and the latest indexed checkpoint. No signing or fund movement." action={wallet.status === "connected" ? <button className="secondary-button" onClick={() => void load()} disabled={loading}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />Refresh analysis</button> : undefined} />
      {wallet.status !== "connected" || !wallet.address ? (
        <section className="surface-card p-6"><div className="flex items-center gap-3 text-[#c9d8e5]"><Wallet2 className="h-5 w-5"/><h2 className="font-semibold">Connect a Sui wallet</h2></div><p className="mt-2 text-sm text-[#8796a5]">Connect your wallet from the Sui Wallet page, then return here to analyse its public on-chain data.</p><a className="primary-button mt-5 inline-flex" href="/sui">Open Sui Wallet</a></section>
      ) : loading && !data ? <div className="surface-card p-8 text-sm text-[#9aabba]">Analysing public wallet data…</div> : error ? <div role="alert" className="surface-card p-5 text-sm text-[#e2b0ad]">{error}</div> : data ? <>
        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric label="Network" value={data.network}/><Metric label="Latest checkpoint" value={data.checkpoint.toLocaleString()}/><Metric label="Known token types" value={String(data.portfolio.known_token_count)}/><Metric label="Recent transactions" value={String(data.activity.sample_size)}/>
        </section>
        <section className="grid gap-5 xl:grid-cols-2">
          <div className="surface-card p-5 sm:p-6"><h2 className="text-sm font-semibold text-[#e3ebf2]">Portfolio composition</h2><p className="mt-1 text-xs text-[#8494a4]">Token amounts where provider decimals are available. Not a fiat valuation; amounts are not combined or compared across token types.</p><div className="mt-5 space-y-3">{data.portfolio.holdings.length ? data.portfolio.holdings.map((holding) => <div key={holding.coin_type} className="flex items-center justify-between gap-4 border-b border-[#202a34] pb-3"><div className="min-w-0"><p className="text-sm text-[#dce5ed]">{holding.symbol || "Unknown token"}</p><p className="truncate text-[10px] text-[#718294]">{holding.coin_type}</p></div><p className="shrink-0 font-mono text-sm text-[#c5dced]">{holding.amount}</p></div>) : <p className="text-sm text-[#8292a2]">No token amounts with verified decimal metadata were returned.</p>}</div></div>
          <div className="surface-card p-5 sm:p-6"><h2 className="text-sm font-semibold text-[#e3ebf2]">Network activity</h2><div className="mt-4 grid grid-cols-3 gap-2"><Mini label="Successful" value={data.activity.successful}/><Mini label="Failed" value={data.activity.failed}/><Mini label="Unknown" value={data.activity.unknown_status}/></div><div className="mt-5 space-y-3">{data.activity.transactions.map((tx) => <div key={tx.digest} className="flex items-center justify-between gap-3 border-b border-[#202a34] pb-3"><div className="min-w-0"><p className="truncate font-mono text-xs text-[#c4d3df]">{tx.digest}</p><p className="mt-1 text-[10px] text-[#758697]">{tx.timestamp_ms ? new Date(tx.timestamp_ms).toLocaleString() : "Timestamp unavailable"}</p></div><span className="rounded-lg border border-[#344554] px-2 py-1 text-[10px] capitalize text-[#b8cad9]">{tx.status}</span></div>)}</div></div>
        </section>
        <section className="surface-card p-5 sm:p-6"><div className="flex items-center gap-2"><Activity className="h-4 w-4 text-[#b7d7f0]"/><h2 className="text-sm font-semibold text-[#e3ebf2]">Intelligence signals</h2></div><div className="mt-4 grid gap-3 md:grid-cols-2">{data.insights.map((insight) => <div key={insight.code} className="rounded-xl border border-[#293744] bg-[#101821] p-4"><div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4 text-[#b9a77c]"/><p className="text-sm font-medium text-[#dbe5ed]">{insight.title}</p></div><p className="mt-2 text-xs leading-5 text-[#91a0ae]">{insight.detail}</p></div>)}{!data.insights.length && <p className="text-sm text-[#91a0ae]">No heuristic signals were triggered by this snapshot.</p>}</div></section>
        <section className="surface-card p-5"><div className="flex items-center gap-2 text-[#b9cbd9]"><ShieldCheck className="h-4 w-4"/><h2 className="text-xs font-semibold">Analysis boundaries</h2></div><ul className="mt-3 list-disc space-y-1 pl-5 text-[11px] leading-5 text-[#8292a2]">{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul><p className="mt-3 text-[10px] text-[#68798a]">Snapshot generated {new Date(data.fetched_at).toLocaleString()} · {data.wallet_address}</p></section>
        <p className="surface-card p-4 text-xs leading-5 text-[#aebbc7]">
          {data.snapshot_consistency === "independent_provider_reads"
            ? "Balances, transaction activity, and checkpoint information were fetched independently. They may reflect different chain states and are not guaranteed to form one consistent snapshot."
            : "Snapshot consistency information is unavailable."}
        </p>
      </> : null}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="surface-card p-4"><p className="text-[10px] font-semibold uppercase tracking-wider text-[#77899a]">{label}</p><p className="mt-2 text-xl font-semibold text-[#dce7ef]">{value}</p></div>; }
function Mini({ label, value }: { label: string; value: number }) { return <div className="rounded-xl border border-[#263440] bg-[#101821] p-3"><p className="text-[10px] text-[#8292a2]">{label}</p><p className="mt-1 text-lg font-semibold text-[#dce7ef]">{value}</p></div>; }
