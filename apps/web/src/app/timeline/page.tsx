import { ClockIcon, TimelineIcon } from "@/components/icons";
import { EmptyState, PageHeading } from "@/components/ui";

export default function TimelinePage() {
  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Timeline"
        title="The moments that connect."
        description="A chronological view of meaningful interactions, decisions, and events—when there is real activity to show."
      />
      <section className="surface-card">
        <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><TimelineIcon className="h-[18px] w-[18px]" /></span>
          <div>
            <p className="text-[12px] font-semibold text-[#e4eaf0]">Personal history</p>
            <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Chronology is built from your own connected activity.</p>
          </div>
        </div>
        <EmptyState title="Your timeline is waiting for connected activity" icon={<ClockIcon className="h-7 w-7" />}>
          No events are available. ORHREX will not invent dates, conversations, or personal history.
        </EmptyState>
      </section>
    </div>
  );
}
