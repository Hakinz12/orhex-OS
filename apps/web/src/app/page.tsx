"use client";

import { useEffect, useState } from "react";
import { fetchHealth } from "@/lib/api";
import { HealthResponse } from "@/lib/types";

export default function Home() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadHealth() {
      try {
        setLoading(true);
        const data = await fetchHealth();
        setHealth(data);
        setError(null);
      } catch (err: any) {
        setError(err.message || "Failed to reach ORHREX API");
      } finally {
        setLoading(false);
      }
    }
    loadHealth();
  }, []);

  return (
    <div className="space-y-8">
      {/* Hero Section */}
      <section className="relative overflow-hidden rounded-2xl border border-slate-800 bg-gradient-to-b from-slate-900/80 via-slate-900/40 to-slate-950 p-6 sm:p-10 shadow-2xl">
        <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-indigo-500/10 blur-3xl pointer-events-none"></div>
        <div className="absolute -left-20 -bottom-20 h-64 w-64 rounded-full bg-sky-500/10 blur-3xl pointer-events-none"></div>

        <div className="relative z-10 max-w-3xl space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-500/30 bg-indigo-500/10 px-3 py-1 text-xs font-medium text-indigo-300">
            <span>Tatum x Walrus Total Recall 2026</span>
            <span className="text-slate-500">&bull;</span>
            <span>Smart Agents for Real Life</span>
          </div>

          <h1 className="text-3xl font-extrabold tracking-tight sm:text-5xl text-white">
            Intelligence That <span className="text-transparent bg-clip-text bg-gradient-to-r from-indigo-400 via-purple-300 to-sky-400">Anticipates.</span>
          </h1>

          <p className="text-base sm:text-lg text-slate-300 leading-relaxed">
            ORHREX OS solves context fragmentation. Instead of treating every conversation as a blank slate, 
            ORHREX carries an authorized, durable memory graph across sessions using <strong className="text-sky-300">Walrus Memory Mainnet</strong>, 
            monitors on-chain signals via <strong className="text-purple-300">Tatum Sui RPC</strong>, and synthesizes grounded, explainable next steps.
          </p>

          <div className="pt-2 flex flex-wrap gap-3">
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-mono bg-slate-800 text-slate-300 border border-slate-700">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"></span> Zero Silent Storage
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-mono bg-slate-800 text-slate-300 border border-slate-700">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"></span> Backend-Secured Keys
            </span>
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-mono bg-slate-800 text-slate-300 border border-slate-700">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"></span> Sourced Evidence Citations
            </span>
          </div>
        </div>
      </section>

      {/* Integration Diagnostic Dashboard */}
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-white">System Health & Adapter Diagnostics</h2>
            <p className="text-xs text-slate-400">Live operational status queried from the FastAPI orchestrator service</p>
          </div>
          <button
            onClick={() => {
              setLoading(true);
              fetchHealth()
                .then((data) => {
                  setHealth(data);
                  setError(null);
                })
                .catch((err) => setError(err.message))
                .finally(() => setLoading(false));
            }}
            disabled={loading}
            className="text-xs font-medium px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors border border-slate-700 disabled:opacity-50"
          >
            {loading ? "Checking..." : "Refresh Status"}
          </button>
        </div>

        {error && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-300">
            <strong>Backend Connection Note:</strong> {error}
            <p className="mt-1 text-slate-400">
              Ensure the FastAPI backend is running via <code className="text-amber-200">uvicorn app.main:app --port 8000</code>.
            </p>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Card 1: App Database */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wider text-slate-400 font-semibold">App Database</span>
              <span className={`h-2 w-2 rounded-full ${health?.adapters?.database?.status === 'connected' ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]' : 'bg-amber-400'}`}></span>
            </div>
            <p className="text-lg font-bold text-white capitalize">
              {health?.adapters?.database?.type || "SQLite Async"}
            </p>
            <p className="text-xs text-slate-400">
              Status: <span className="font-mono text-emerald-400">{health?.adapters?.database?.status || (loading ? "connecting..." : "offline")}</span>
            </p>
          </div>

          {/* Card 2: AI Provider (Gemini) */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wider text-slate-400 font-semibold">AI Provider</span>
              <span className={`h-2 w-2 rounded-full ${health?.adapters?.gemini?.status === 'ready' ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.5)]' : 'bg-sky-400'}`}></span>
            </div>
            <p className="text-lg font-bold text-white">Google Gemini</p>
            <p className="text-xs text-slate-400">
              Model: <span className="font-mono text-sky-300">{health?.adapters?.gemini?.model || "gemini-2.5-flash"}</span>
            </p>
          </div>

          {/* Card 3: Walrus Memory */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wider text-slate-400 font-semibold">Decentralized Memory</span>
              <span className={`h-2 w-2 rounded-full ${health?.adapters?.walrus?.status === 'ready' ? 'bg-emerald-400' : 'bg-sky-400'}`}></span>
            </div>
            <p className="text-lg font-bold text-white">Walrus Memory</p>
            <p className="text-xs text-slate-400">
              Target: <span className="font-mono text-sky-400 uppercase">{health?.adapters?.walrus?.network || "mainnet"}</span>
            </p>
          </div>

          {/* Card 4: Tatum Sui RPC */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase tracking-wider text-slate-400 font-semibold">Blockchain RPC</span>
              <span className={`h-2 w-2 rounded-full ${health?.adapters?.tatum?.status === 'ready' ? 'bg-emerald-400' : 'bg-purple-400'}`}></span>
            </div>
            <p className="text-lg font-bold text-white">Tatum Sui RPC</p>
            <p className="text-xs text-slate-400">
              Network: <span className="font-mono text-purple-400 uppercase">{health?.adapters?.tatum?.network || "mainnet"}</span>
            </p>
          </div>
        </div>
      </section>

      {/* Mandatory Hackathon Deliverables Tracker */}
      <section className="space-y-4">
        <h2 className="text-xl font-bold tracking-tight text-white">Hackathon Submission Progress</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="rounded-xl border border-slate-800/80 bg-slate-900/30 p-5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-sky-400">1. Walrus Mainnet Memory</span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-sky-500/10 text-sky-300 font-mono">Adapter Ready</span>
            </div>
            <p className="text-sm text-slate-300">
              Durable Ed25519-signed memory blobs written directly to Walrus storage nodes.
            </p>
          </div>

          <div className="rounded-xl border border-slate-800/80 bg-slate-900/30 p-5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-purple-400">2. Tatum Sui Intelligence</span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-purple-500/10 text-purple-300 font-mono">Adapter Ready</span>
            </div>
            <p className="text-sm text-slate-300">
              Read-only wallet balance, checkpoint sync, and transaction digest synthesis.
            </p>
          </div>

          <div className="rounded-xl border border-slate-800/80 bg-slate-900/30 p-5 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-indigo-400">3. Explainable Anticipation</span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 font-mono">Milestone 1 Built</span>
            </div>
            <p className="text-sm text-slate-300">
              Deterministic rule triggers that explicitly cite source memory IDs and timestamps.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}

