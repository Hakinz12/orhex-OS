"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  fetchActionProposals,
  fetchAnticipations,
  fetchLatestOrchestration,
  fetchTasks,
  listMemories,
} from "@/lib/api";
import type { TimelineEvent } from "@/lib/timeline-events.mjs";
import { buildTimelineEvents } from "@/lib/timeline-events.mjs";
import { ClockIcon, TimelineIcon } from "@/components/icons";
import { PageHeading } from "@/components/ui";

const filters = [
  ["all", "All"],
  ["tasks", "Tasks"],
  ["memories", "Memories"],
  ["events", "Events"],
  ["orhex", "ORHEX Activity"],
] as const;

type TimelineFilter = (typeof filters)[number][0];

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function TimelinePage() {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [filter, setFilter] = useState<TimelineFilter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [tasksResult, memoriesResult, anticipationsResult, actionsResult, orchestrationResult] =
      await Promise.allSettled([
        fetchTasks(),
        listMemories("active", 50),
        fetchAnticipations(),
        fetchActionProposals(),
        fetchLatestOrchestration(),
      ]);

    const orchestrationMissing =
      orchestrationResult.status === "rejected" &&
      orchestrationResult.reason instanceof ApiError &&
      orchestrationResult.reason.status === 404;
    const failures = [
      tasksResult,
      memoriesResult,
      anticipationsResult,
      actionsResult,
      ...(orchestrationMissing ? [] : [orchestrationResult]),
    ].filter((result) => result.status === "rejected");
    const successful = (result: PromiseSettledResult<unknown>) =>
      result.status === "fulfilled" ? result.value : undefined;
    const memories = successful(memoriesResult) as Awaited<ReturnType<typeof listMemories>> | undefined;

    setEvents(
      buildTimelineEvents({
        tasks: (successful(tasksResult) as Awaited<ReturnType<typeof fetchTasks>> | undefined) ?? [],
        memories: memories?.memories ?? [],
        anticipations:
          (successful(anticipationsResult) as Awaited<ReturnType<typeof fetchAnticipations>> | undefined) ?? [],
        actions:
          (successful(actionsResult) as Awaited<ReturnType<typeof fetchActionProposals>> | undefined) ?? [],
        orchestration:
          (successful(orchestrationResult) as Awaited<ReturnType<typeof fetchLatestOrchestration>> | undefined) ?? null,
      }),
    );
    if (failures.length > 0) {
      setError("Some activity sources are unavailable. The timeline shows only data that loaded successfully.");
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visibleEvents = useMemo(
    () => events.filter((event) => filter === "all" || event.category === filter),
    [events, filter],
  );

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Timeline"
        title="What happened, and what is coming next."
        description="A chronological view of recorded tasks, confirmed memories, active deadlines, and ORHEX actions. Earlier task edits are not available because ORHEX does not keep a task-change history."
        action={
          <button type="button" className="secondary-button" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
        }
      />
      <section className="surface-card overflow-hidden">
        <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]">
            <TimelineIcon className="h-[18px] w-[18px]" />
          </span>
          <div>
            <p className="text-[12px] font-semibold text-[#e4eaf0]">Recent activity</p>
            <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Ordered newest first · memory entries limited to the latest 50</p>
          </div>
        </div>
        <div className="space-y-5 p-5 sm:p-7">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Filter timeline">
            {filters.map(([value, label]) => (
              <button
                key={value}
                type="button"
                className={filter === value ? "primary-button min-h-9 px-3 text-[10px]" : "secondary-button min-h-9 px-3 text-[10px]"}
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
          {error && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#49393a] bg-[#1d1719] px-4 py-3">
              <p role="alert" className="text-[11px] text-[#d7aaa6]">{error}</p>
              <button type="button" className="secondary-button text-[10px]" onClick={() => void load()} disabled={loading}>
                Retry
              </button>
            </div>
          )}
          {loading && <p role="status" className="py-8 text-center text-[12px] text-[#8493a3]">Loading recorded activity…</p>}
          {!loading && !error && visibleEvents.length === 0 && (
            <div className="py-8 text-center">
              <p className="text-[13px] font-semibold text-[#e0e7ed]">No activity in this view yet.</p>
              <p className="mx-auto mt-2 max-w-md text-[11px] leading-5 text-[#8493a3]">
                Add tasks, save confirmed memories, or record commitments with due dates. ORHEX does not create events or historical activity that was not recorded.
              </p>
            </div>
          )}
          {!loading && visibleEvents.length > 0 && (
            <ol className="divide-y divide-[#202a34]">
              {visibleEvents.map((event) => (
                <li key={event.id} className="grid gap-2 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
                  <div className="min-w-0">
                    <span className="inline-flex rounded-full border border-[#344653] bg-[#17232c] px-2.5 py-1 text-[9px] font-semibold uppercase tracking-wide text-[#b6ccdc]">
                      {event.category === "orhex" ? "ORHEX Activity" : event.category}
                    </span>
                    <h2 className="mt-2 text-[12px] font-semibold text-[#e4eaf0]">{event.title}</h2>
                    <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-5 text-[#98a6b3]">{event.description}</p>
                  </div>
                  <time className="inline-flex items-center gap-1.5 text-[10px] text-[#8291a1]" dateTime={event.timestamp}>
                    <ClockIcon className="h-3.5 w-3.5" />
                    {formatTimestamp(event.timestamp)}
                  </time>
                </li>
              ))}
            </ol>
          )}
        </div>
      </section>
    </div>
  );
}
