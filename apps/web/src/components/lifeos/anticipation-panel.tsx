"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchAnticipations, refreshAnticipations, updateAnticipation } from "@/lib/api";
import type { AnticipationInsight } from "@/lib/types";

function dateLabel(value: string | null) {
  if (!value) return "No date recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function Priority({ value }: { value: AnticipationInsight["priority"] }) {
  const styles = value === "high" ? "border-[#624044] bg-[#281a1d] text-[#e7b0ae]" : value === "medium" ? "border-[#62543a] bg-[#282317] text-[#dfc58f]" : "border-[#344653] bg-[#17232c] text-[#b6ccdc]";
  return <span className={`rounded-full border px-2.5 py-1 text-[9px] font-semibold uppercase tracking-[0.1em] ${styles}`}>{value} priority</span>;
}

export function AnticipationPanel({ mode = "briefing" }: { mode?: "briefing" | "insights" | "timeline" }) {
  const [items, setItems] = useState<AnticipationInsight[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    if (refresh) setRefreshing(true); else setLoading(true);
    setError(null);
    try {
      const result = refresh ? await refreshAnticipations() : await fetchAnticipations();
      setItems(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "LifeOS information is unavailable.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void load(mode === "briefing"); }, [load, mode]);

  const ordered = useMemo(() => [...items].sort((a, b) => {
    const priority = { high: 0, medium: 1, low: 2 };
    return priority[a.priority] - priority[b.priority] || (a.due_at ?? a.created_at).localeCompare(b.due_at ?? b.created_at);
  }), [items]);
  const visible = mode === "briefing" ? ordered.slice(0, 4) : mode === "timeline" ? [...items].sort((a, b) => b.created_at.localeCompare(a.created_at)) : ordered;

  async function dismiss(id: string) {
    setBusyId(id);
    try {
      await updateAnticipation(id, "dismissed");
      setItems((current) => current.filter((item) => item.id !== id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The insight could not be updated.");
    } finally { setBusyId(null); }
  }

  return (
    <div className="space-y-4">
      {mode === "briefing" && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><p className="eyebrow">Your day, in context</p><h2 className="font-display mt-2 text-xl font-semibold tracking-tight text-[#eef2f5]">A clearer next move.</h2><p className="mt-1 text-[11px] text-[#8796a5]">Based on your recorded goals and commitments.</p></div>
          <button className="secondary-button text-[10px]" onClick={() => void load(true)} disabled={refreshing}>{refreshing ? "Updating…" : "Refresh briefing"}</button>
        </div>
      )}
      {mode !== "briefing" && <div className="flex justify-end"><button className="secondary-button text-[10px]" onClick={() => void load(true)} disabled={refreshing}>{refreshing ? "Refreshing…" : "Refresh insights"}</button></div>}
      {error && <p role="alert" className="rounded-xl border border-[#49393a] bg-[#1d1719] px-4 py-3 text-[11px] text-[#d7aaa6]">{error}</p>}
      {loading && <div className="surface-card p-6 text-[12px] text-[#8291a1]">Loading grounded context…</div>}
      {!loading && !error && visible.length === 0 && <div className="surface-card p-7"><p className="text-[13px] font-semibold text-[#e0e7ed]">Nothing needs your attention right now.</p><p className="mt-2 text-[11px] leading-5 text-[#8493a3]">No active deadline signals were found in your recorded goals and commitments. ORHEX will not invent personal events or recommendations.</p><Link href="/workspace" className="mt-4 inline-flex text-[11px] font-semibold text-[#b8d5e9] hover:text-white">Add context in your workspace →</Link></div>}
      {!loading && visible.length > 0 && <div className="space-y-3">{visible.map((item) => <article key={item.id} className="surface-card p-4 sm:p-5">
        <div className="flex flex-wrap items-center justify-between gap-2"><Priority value={item.priority}/><span className="text-[10px] text-[#8291a1]">{item.source_type === "goal" ? "Goal" : "Commitment"} · {dateLabel(item.due_at)}</span></div>
        <h3 className="mt-3 text-[13px] font-semibold leading-5 text-[#e8edf2]">{item.title}</h3>
        <p className="mt-2 text-[11px] leading-5 text-[#98a6b3]">{item.explanation}</p>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3"><span className="text-[9px] text-[#6f8090]">Evidence: recorded {item.source_type}</span>{mode !== "timeline" && <button className="text-[10px] font-semibold text-[#9fb9cd] hover:text-white disabled:opacity-50" onClick={() => void dismiss(item.id)} disabled={busyId === item.id}>{busyId === item.id ? "Updating…" : "Dismiss"}</button>}</div>
      </article>)}</div>}
      {mode === "briefing" && <div className="flex flex-wrap gap-4"><Link href="/tasks" className="inline-flex text-[11px] font-semibold text-[#b8d5e9] hover:text-white">Tasks →</Link><Link href="/insights" className="inline-flex text-[11px] font-semibold text-[#b8d5e9] hover:text-white">View all insights →</Link></div>}
    </div>
  );
}
