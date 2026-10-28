import Link from "next/link";
import { ArrowRightIcon, MessageIcon, ShieldIcon, SparkIcon } from "@/components/icons";
import { EmptyState, PageHeading } from "@/components/ui";
import { authIntegrationNotice } from "@/lib/api";

export default function WorkspacePage() {
  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="AI workspace"
        title="Think with your context."
        description="A space for questions, decisions, and ideas—designed to become more useful as your personal context connects."
        action={<span className="inline-flex items-center gap-2 self-start rounded-full border border-[#35404b] px-3 py-1.5 text-[10px] text-[#a2afbb] sm:self-auto"><span className="status-dot" data-status="unconfigured" /> Session not connected</span>}
      />
      <section className="surface-card overflow-hidden">
        <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><SparkIcon className="h-[18px] w-[18px]" /></span>
          <div>
            <p className="text-[12px] font-semibold text-[#e4eaf0]">A more connected conversation</p>
            <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Private workspace · Ready when secure access is available</p>
          </div>
        </div>
        <EmptyState title="Your conversation begins here" icon={<MessageIcon className="h-7 w-7" />}>
          ORHREX does not yet expose a chat endpoint or browser sign-in. No example response or fabricated conversation is shown.
        </EmptyState>
        <div className="border-t border-[#202a34] bg-[#0f151c] p-4 sm:p-6">
          <label htmlFor="workspace-message" className="sr-only">Message composer unavailable</label>
          <textarea
            id="workspace-message"
            disabled
            rows={3}
            placeholder="Your private conversation will appear here after secure sign-in is connected."
            className="text-input resize-none opacity-65"
          />
          <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="inline-flex items-center gap-2 text-[10px] leading-5 text-[#788797]"><ShieldIcon className="h-3.5 w-3.5 shrink-0" />{authIntegrationNotice}</p>
            <button type="button" className="primary-button shrink-0" disabled aria-disabled="true" title="Unavailable until authenticated chat is implemented">
              Send <ArrowRightIcon className="h-4 w-4" />
            </button>
          </div>
        </div>
      </section>
      <div className="surface-inset flex flex-col justify-between gap-4 p-5 sm:flex-row sm:items-center sm:p-6">
        <div>
          <p className="text-[12px] font-semibold text-[#dfe6ec]">Build continuity with care</p>
          <p className="mt-1 text-[11px] text-[#8492a1]">Memory controls will require consent and an authenticated account.</p>
        </div>
        <Link href="/memory" className="secondary-button shrink-0 text-[11px]">Memory foundations <ArrowRightIcon className="h-3.5 w-3.5" /></Link>
      </div>
    </div>
  );
}
