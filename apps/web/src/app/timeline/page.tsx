import { ClockIcon, TimelineIcon } from "@/components/icons";
import { PageHeading } from "@/components/ui";
import { AnticipationPanel } from "@/components/lifeos/anticipation-panel";

export default function TimelinePage() {
  return <div className="space-y-8">
    <PageHeading eyebrow="Timeline" title="A record of what surfaced." description="A chronological view of active anticipation signals generated from your recorded goals and commitments. This is not yet a complete history of every interaction." />
    <section className="surface-card">
      <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7"><span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><TimelineIcon className="h-[18px] w-[18px]" /></span><div><p className="text-[12px] font-semibold text-[#e4eaf0]">Current anticipation signals</p><p className="mt-0.5 text-[10px] text-[#7e8d9d]">Active insights, ordered by creation time</p></div></div>
      <div className="p-5 sm:p-7"><AnticipationPanel mode="timeline" /></div>
    </section>
    <p className="inline-flex items-center gap-2 text-[10px] text-[#7c8b9b]"><ClockIcon className="h-3.5 w-3.5" /> Broader conversation, task, and wallet event history will be added in a later milestone.</p>
  </div>;
}
