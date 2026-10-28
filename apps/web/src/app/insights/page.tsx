import { CompassIcon, InsightsIcon, ShieldIcon } from "@/components/icons";
import { EmptyState, PageHeading } from "@/components/ui";

export default function InsightsPage() {
  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Contextual intelligence"
        title="See the useful connections."
        description="Grounded insights and possible next steps, drawn only from connected and relevant context."
      />
      <section className="surface-card">
        <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><InsightsIcon className="h-[18px] w-[18px]" /></span>
          <div>
            <p className="text-[12px] font-semibold text-[#e4eaf0]">Signals with context</p>
            <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Recommendations will be traceable to their source.</p>
          </div>
        </div>
        <EmptyState title="No grounded insights yet" icon={<CompassIcon className="h-7 w-7" />}>
          Insights require real account context and a connected intelligence workflow. Nothing is fabricated for this preview.
        </EmptyState>
      </section>
      <p className="inline-flex items-start gap-2 text-[10px] leading-5 text-[#7c8b9b]"><ShieldIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[#9fb5c7]" /> ORHREX is designed to explain why an insight appears; unsupported recommendations will not be presented as personal guidance.</p>
    </div>
  );
}
