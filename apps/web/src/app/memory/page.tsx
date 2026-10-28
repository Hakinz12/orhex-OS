import Link from "next/link";
import { ArrowRightIcon, MemoryIcon, ShieldIcon } from "@/components/icons";
import { EmptyState, PageHeading } from "@/components/ui";
import { authIntegrationNotice } from "@/lib/api";

export default function MemoryPage() {
  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Personal memory"
        title="Keep what matters."
        description="A thoughtful memory layer for the details, preferences, and commitments that make your context uniquely yours."
        action={<span className="inline-flex items-center gap-2 self-start rounded-full border border-[#35404b] px-3 py-1.5 text-[10px] text-[#a2afbb] sm:self-auto"><span className="status-dot" data-status="unconfigured" /> Account required</span>}
      />
      <section className="surface-card">
        <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><MemoryIcon className="h-[18px] w-[18px]" /></span>
          <div>
            <h2 className="text-[12px] font-semibold text-[#e4eaf0]">Your memory collection</h2>
            <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Private to your account · Consent remains in your control</p>
          </div>
        </div>
        <EmptyState title="No personal memories are displayed" icon={<MemoryIcon className="h-7 w-7" />}>
          The memory service requires a verified bearer principal. Browser authentication is not yet connected, so no private memory request is made.
        </EmptyState>
      </section>
      <section className="grid gap-4 md:grid-cols-2">
        <article className="surface-card p-5 sm:p-6">
          <div className="flex items-center gap-2 text-[12px] font-semibold text-[#e3e9ee]"><ShieldIcon className="h-4 w-4 text-[#b8d2e5]" /> Privacy boundary</div>
          <p className="mt-3 text-[11px] leading-5 text-[#8795a4]">{authIntegrationNotice}</p>
        </article>
        <article className="surface-card p-5 sm:p-6">
          <p className="text-[12px] font-semibold text-[#e3e9ee]">Memory tools are not connected</p>
          <p className="mt-3 text-[11px] leading-5 text-[#8795a4]">There is no browser memory-list API yet. This screen intentionally avoids sample entries, counts, or unverified connection claims.</p>
          <Link href="/settings" className="mt-4 inline-flex items-center gap-2 text-[11px] font-semibold text-[#b8d5e9] hover:text-[#e0edf6]">Review security setup <ArrowRightIcon className="h-3.5 w-3.5" /></Link>
        </article>
      </section>
    </div>
  );
}
