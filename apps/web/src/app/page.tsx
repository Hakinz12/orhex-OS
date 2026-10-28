"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ArrowRightIcon,
  ArrowUpRightIcon,
  ChainIcon,
  ClockIcon,
  CompassIcon,
  MemoryIcon,
  MessageIcon,
  PlusIcon,
  ShieldIcon,
  SparkIcon,
  WalrusIcon,
} from "@/components/icons";
import { EmptyState, IntegrationStatus, PageHeading, SectionHeading } from "@/components/ui";
import { fetchHealth } from "@/lib/api";
import type { HealthResponse } from "@/lib/types";

function DiagnosticCard({ loading, error, health }: {
  loading: boolean;
  error: string | null;
  health: HealthResponse | null;
}) {
  if (loading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Loading integration status">
        {Array.from({ length: 4 }, (_, index) => (
          <div className="surface-card h-[146px] p-5" key={index}>
            <div className="skeleton h-9 w-9" />
            <div className="skeleton mt-5 h-3 w-28" />
            <div className="skeleton mt-2 h-3 w-36" />
          </div>
        ))}
      </div>
    );
  }

  if (error || !health) {
    return (
      <div className="surface-card flex flex-col items-start justify-between gap-4 p-5 sm:flex-row sm:items-center">
        <div>
          <p className="text-[13px] font-semibold text-[#e7edf2]">System status could not be loaded</p>
          <p className="mt-1 text-[12px] text-[#8594a4]">{error || "The API did not return a status report."}</p>
        </div>
        <span className="text-[10px] text-[#788798]">No integration status is assumed.</span>
      </div>
    );
  }

  const adapters = health.adapters;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <IntegrationStatus
        name="Personal database"
        provider={adapters.database.type || "Application"}
        status={adapters.database.status}
        detail="Local context store"
        icon={<ShieldIcon className="h-[18px] w-[18px]" />}
      />
      <IntegrationStatus
        name="AI reasoning"
        provider="Gemini"
        status={adapters.gemini.status}
        detail={adapters.gemini.model || "Model not reported"}
        icon={<SparkIcon className="h-[18px] w-[18px]" />}
      />
      <IntegrationStatus
        name="Long-term memory"
        provider="Walrus"
        status={adapters.walrus.status}
        detail={adapters.walrus.network || "Network not reported"}
        icon={<WalrusIcon className="h-[18px] w-[18px]" />}
      />
      <IntegrationStatus
        name="On-chain context"
        provider="Tatum · Sui"
        status={adapters.tatum.status}
        detail={adapters.tatum.network || "Network not reported"}
        icon={<ChainIcon className="h-[18px] w-[18px]" />}
      />
    </div>
  );
}

export default function Home() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadHealth = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setHealth(await fetchHealth());
    } catch (reason) {
      setHealth(null);
      setError(reason instanceof Error ? reason.message : "System status is unavailable.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHealth();
  }, [loadHealth]);

  return (
    <div className="space-y-9 sm:space-y-11">
      <PageHeading
        eyebrow="Your personal operating system"
        title="A clearer view of your world."
        description="ORHREX brings your context together, so the important things stay connected and the next step feels easier to see."
        action={
          <div className="flex items-center gap-2 self-start sm:self-auto">
            <span className="hidden text-[10px] text-[#718092] sm:inline">System status</span>
            <button type="button" className="secondary-button text-[11px]" onClick={() => void loadHealth()} disabled={loading}>
              {loading ? "Checking…" : "Refresh"}
            </button>
          </div>
        }
      />

      <section className="relative overflow-hidden rounded-[24px] border border-[#2a3946] bg-[#111a24] px-5 py-7 shadow-panel sm:px-8 sm:py-9 lg:px-10">
        <div className="pointer-events-none absolute inset-y-0 right-0 hidden w-[43%] opacity-70 md:block" aria-hidden="true">
          <div className="absolute right-8 top-[-135px] h-[390px] w-[390px] rounded-full border border-[#263747]" />
          <div className="absolute right-[70px] top-[-95px] h-[310px] w-[310px] rounded-full border border-[#2a3d4d]" />
          <div className="absolute right-[130px] top-[-35px] h-[190px] w-[190px] rounded-full border border-[#34495a]" />
          <div className="absolute right-[226px] top-[61px] h-2 w-2 rounded-full bg-[#a8c8df]" />
          <div className="absolute right-[90px] top-[244px] h-1.5 w-1.5 rounded-full bg-[#839cb0]" />
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
            <Link href="/workspace" className="primary-button">
              <MessageIcon className="h-4 w-4" />
              Open AI workspace
              <ArrowRightIcon className="ml-1 h-4 w-4" />
            </Link>
            <Link href="/memory" className="secondary-button">
              Explore memory
              <ArrowUpRightIcon className="h-4 w-4" />
            </Link>
          </div>
          <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-[10px] text-[#8696a5]">
            <span className="inline-flex items-center gap-2"><ShieldIcon className="h-3.5 w-3.5 text-[#abc6d9]" /> You stay in control</span>
            <span className="inline-flex items-center gap-2"><MemoryIcon className="h-3.5 w-3.5 text-[#abc6d9]" /> Context with continuity</span>
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[1.45fr_0.85fr]">
        <article className="surface-card flex min-h-[245px] flex-col p-5 sm:p-6">
          <SectionHeading title="Start with what’s on your mind" detail="A workspace for context-rich thinking and clearer next steps." />
          <div className="surface-inset mt-5 flex flex-1 flex-col justify-between p-4 sm:p-5">
            <p className="max-w-[530px] text-[13px] leading-6 text-[#b3bec8]">
              Bring a question, a decision, or a thought you want to make sense of. Your workspace is ready to take shape.
            </p>
            <div className="mt-5 flex flex-col gap-3 sm:flex-row">
              <Link href="/workspace" className="primary-button w-full sm:w-auto">
                <SparkIcon className="h-4 w-4" />
                Enter AI workspace
              </Link>
              <p className="self-center text-[10px] leading-4 text-[#718092]">
                Sign-in and conversations are not connected in this preview.
              </p>
            </div>
          </div>
        </article>

        <article className="surface-card p-5 sm:p-6">
          <SectionHeading title="Memory overview" detail="Your context, kept with intention." />
          <div className="mt-5 rounded-[14px] border border-dashed border-[#34414d] bg-[#0e141b] px-4 py-5">
            <div className="flex items-start gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[#1a2732] text-[#afc8db]">
                <MemoryIcon className="h-[18px] w-[18px]" />
              </span>
              <div>
                <p className="text-[12px] font-semibold text-[#dfe5eb]">No personal memory data shown</p>
                <p className="mt-1.5 text-[11px] leading-5 text-[#8795a4]">
                  Memory access requires an authenticated session. ORHREX does not guess or display sample memories.
                </p>
              </div>
            </div>
            <Link href="/memory" className="mt-4 inline-flex items-center gap-2 text-[11px] font-semibold text-[#b8d5e9] hover:text-[#e0edf6]">
              Go to Memory <ArrowRightIcon className="h-3.5 w-3.5" />
            </Link>
          </div>
        </article>
      </section>

      <section className="grid gap-7 lg:grid-cols-[1.3fr_0.7fr]">
        <div>
          <SectionHeading title="Your next steps" detail="Personal recommendations will appear when grounded in your context." />
          <div className="surface-card mt-4">
            <EmptyState title="Nothing anticipated yet" icon={<CompassIcon className="h-6 w-6" />}>
              Recommendations need your connected account and real personal context. There are no suggestions to show right now.
            </EmptyState>
          </div>
        </div>
        <div>
          <SectionHeading title="Recent activity" detail="A timeline of meaningful moments." />
          <div className="surface-card mt-4">
            <EmptyState title="Your story starts when you do" icon={<ClockIcon className="h-6 w-6" />}>
              Activity will appear here once your account and connected experiences are available.
            </EmptyState>
          </div>
        </div>
      </section>

      <section className="space-y-4">
        <SectionHeading
          title="Connected foundations"
          detail="Live diagnostics from the ORHREX service. A reported state does not imply personal data is connected."
        />
        <DiagnosticCard loading={loading} error={error} health={health} />
        {!loading && !error && health && (
          <p className="text-[10px] leading-5 text-[#718092]">
            Status checked against the public service health endpoint. Account-specific memory access remains unavailable until secure sign-in is implemented.
          </p>
        )}
      </section>

      <section className="surface-card flex flex-col items-start justify-between gap-4 p-5 sm:flex-row sm:items-center sm:px-6">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-[#2d3b47] bg-[#17212b] text-[#b7d1e4]"><PlusIcon className="h-5 w-5" /></span>
          <div>
            <p className="text-[12px] font-semibold text-[#e5ebf0]">A foundation, not a finished system</p>
            <p className="mt-1 text-[11px] text-[#8694a3]">Your private workspace will grow as secure account access comes online.</p>
          </div>
        </div>
        <Link href="/settings" className="secondary-button shrink-0 text-[11px]">
          View settings <ArrowRightIcon className="h-3.5 w-3.5" />
        </Link>
      </section>
    </div>
  );
}
