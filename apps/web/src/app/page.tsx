"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ArrowRightIcon,
  ArrowUpRightIcon,
  CompassIcon,
  MemoryIcon,
  MessageIcon,
  ShieldIcon,
  SparkIcon,
  WalrusIcon,
} from "@/components/icons";
import { EmptyState, PageHeading, SectionHeading, StatusBadge } from "@/components/ui";
import { fetchMemoryStatus, recallMemories } from "@/lib/api";
import type { MemoryRecallResponse, MemoryServiceStatus } from "@/lib/types";

export default function Home() {
  const [status, setStatus] = useState<MemoryServiceStatus | null>(null);
  const [memories, setMemories] = useState<MemoryRecallResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [statusResult, memoryResult] = await Promise.allSettled([
      fetchMemoryStatus(),
      recallMemories("What personal context and memories have I saved?", 3),
    ]);
    setStatus(statusResult.status === "fulfilled" ? statusResult.value : null);
    setMemories(memoryResult.status === "fulfilled" ? memoryResult.value : null);
    const failed = [statusResult, memoryResult].find((result) => result.status === "rejected");
    if (failed?.status === "rejected") {
      setError(failed.reason instanceof Error ? failed.reason.message : "Memory service information is unavailable.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="space-y-9 sm:space-y-11">
      <PageHeading
        eyebrow="Your personal operating system"
        title="A clearer view of your world."
        description="ORHEX brings your context together, so the important things stay connected and the next step feels easier to see."
        action={
          <button type="button" className="secondary-button text-[11px]" onClick={() => void refresh()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        }
      />

      <section className="relative overflow-hidden rounded-[24px] border border-[#2a3946] bg-[#111a24] px-5 py-7 shadow-panel sm:px-8 sm:py-9 lg:px-10">
        <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-[43%] opacity-70 md:block" aria-hidden="true">
          <div className="absolute right-8 top-[-135px] h-[390px] w-[390px] rounded-full border border-[#263747]" />
          <div className="absolute right-[70px] top-[-95px] h-[310px] w-[310px] rounded-full border border-[#2a3d4d]" />
          <div className="absolute right-[130px] top-[-35px] h-[190px] w-[190px] rounded-full border border-[#34495a]" />
          <div className="absolute right-[226px] top-[61px] h-2 w-2 rounded-full bg-[#a8c8df]" />
        </div>
        <div className="relative max-w-[660px]">
          <div className="inline-flex items-center gap-2 rounded-full border border-[#344553] bg-[#192531] px-3 py-1.5 text-[10px] font-medium text-[#bed4e5]">
            <CompassIcon className="h-3.5 w-3.5" />
            A more connected way to move through your day
          </div>
          <h2 className="font-display mt-5 max-w-[580px] text-[25px] font-semibold leading-[1.2] tracking-[-0.04em] text-[#f0f3f6] sm:text-[34px]">
            Your World. <span className="text-[#b8d5e9]">Understood.</span>
          </h2>
          <p className="mt-3 max-w-[510px] text-[13px] leading-6 text-[#a2afbb] sm:text-sm">
            A personal intelligence system designed to remember what matters, connect the dots, and help turn understanding into action.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link href="/workspace" className="primary-button"><MessageIcon className="h-4 w-4" />Open AI workspace<ArrowRightIcon className="h-4 w-4" /></Link>
            <Link href="/memory" className="secondary-button">Explore memory<ArrowUpRightIcon className="h-4 w-4" /></Link>
          </div>
          <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-[10px] text-[#8696a5]">
            <span className="inline-flex items-center gap-2"><ShieldIcon className="h-3.5 w-3.5 text-[#abc6d9]" /> You stay in control</span>
            <span className="inline-flex items-center gap-2"><MemoryIcon className="h-3.5 w-3.5 text-[#abc6d9]" /> Context with continuity</span>
          </div>
        </div>
      </section>

      {error && <p role="status" className="rounded-xl border border-[#49393a] bg-[#1d1719] px-4 py-3 text-[12px] text-[#d7aaa6]">{error}</p>}

      <section className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
        <article className="surface-card p-5 sm:p-6">
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-[#2b3946] bg-[#17212b] text-[#b8d2e5]"><WalrusIcon className="h-5 w-5" /></span>
              <div>
                <h2 className="text-[13px] font-semibold text-[#e8edf2]">Memory service</h2>
                <p className="mt-1 text-[11px] text-[#8291a1]">Authenticated live readiness</p>
              </div>
            </div>
            <StatusBadge status={status?.status} />
          </div>
          <div className="mt-5 border-t border-[#202a34] pt-4">
            {loading && <p className="text-[12px] text-[#8291a1]">Checking memory service…</p>}
            {!loading && status && (
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-[11px] text-[#9ba9b6]">
                <span>Write readiness: {status.write_ready === true ? "Ready" : status.write_ready === false ? "Not ready" : "Not reported"}</span>
                {status.relayer_status && <span>Relayer: {status.relayer_status}</span>}
                {status.mode && <span>Mode: {status.mode}</span>}
              </div>
            )}
            {!loading && !status && <p className="text-[12px] text-[#8291a1]">Memory service status is unavailable.</p>}
          </div>
        </article>
        <article className="surface-card p-5 sm:p-6">
          <SectionHeading title="Saved context" detail="Real semantic recall from your account." />
          {loading && <p className="mt-5 text-[12px] text-[#8291a1]">Searching your saved memories…</p>}
          {!loading && memories && memories.results.length > 0 && (
            <>
              <p className="mt-4 text-[10px] text-[#8291a1]">{memories.total} relevant result{memories.total === 1 ? "" : "s"} returned</p>
              <ul className="mt-3 space-y-3">
                {memories.results.slice(0, 3).map((item) => (
                  <li key={item.blob_id} className="border-l border-[#52687b] pl-3 text-[11px] leading-5 text-[#c1cbd4]">{item.text}</li>
                ))}
              </ul>
            </>
          )}
          {!loading && memories?.results.length === 0 && <p className="mt-5 text-[12px] text-[#8291a1]">No matching memories were returned.</p>}
          {!loading && !memories && <p className="mt-5 text-[12px] text-[#8291a1]">Saved context is unavailable right now.</p>}
          <Link href="/memory" className="mt-4 inline-flex items-center gap-2 text-[11px] font-semibold text-[#b8d5e9] hover:text-[#e0edf6]">Open Memory <ArrowRightIcon className="h-3.5 w-3.5" /></Link>
        </article>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <article className="surface-card p-5 sm:p-6">
          <SectionHeading title="Start with what’s on your mind" detail="Save something meaningful or recall context when you need it." />
          <div className="surface-inset mt-5 p-4 sm:p-5">
            <p className="text-[12px] leading-5 text-[#aab6c1]">Your workspace connects directly to the real memory service. It does not generate fabricated AI answers.</p>
            <Link href="/workspace" className="primary-button mt-4"><SparkIcon className="h-4 w-4" />Open workspace</Link>
          </div>
        </article>
        <article className="surface-card p-5 sm:p-6">
          <SectionHeading title="Your next steps" detail="Recommendations require a connected intelligence workflow." />
          <EmptyState title="No grounded recommendations yet">
            No recommendations are generated until real contextual intelligence is available.
          </EmptyState>
        </article>
      </section>
    </div>
  );
}
