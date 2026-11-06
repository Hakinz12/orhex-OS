import { InsightsIcon, ShieldIcon } from "@/components/icons";
import { PageHeading } from "@/components/ui";
import { AnticipationPanel } from "@/components/lifeos/anticipation-panel";

export default function InsightsPage() {
  return <div className="space-y-8">
    <PageHeading eyebrow="Contextual intelligence" title="See what needs your attention." description="Deadline signals generated from your recorded goals and commitments, with an explanation and traceable source for every insight." />
    <section className="surface-card">
      <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7"><span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><InsightsIcon className="h-[18px] w-[18px]" /></span><div><p className="text-[12px] font-semibold text-[#e4eaf0]">Signals with context</p><p className="mt-0.5 text-[10px] text-[#7e8d9d]">Deterministic rules · owner-scoped evidence</p></div></div>
      <div className="p-5 sm:p-7"><AnticipationPanel mode="insights" /></div>
    </section>
    <p className="inline-flex items-start gap-2 text-[10px] leading-5 text-[#7c8b9b]"><ShieldIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#9fb5c7]" /> These are evidence-backed deadline signals, not autonomous decisions or guaranteed predictions.</p>
  </div>;
}
