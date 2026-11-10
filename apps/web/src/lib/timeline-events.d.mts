import type {
  ActionProposal,
  AnticipationInsight,
  ManagedMemory,
  OrhexTask,
  OrchestrationRun,
} from "./types";

export interface TimelineEvent {
  id: string;
  category: "tasks" | "memories" | "events" | "orhex";
  title: string;
  description: string;
  timestamp: string;
}

export function buildTimelineEvents(
  data: {
    tasks?: OrhexTask[];
    memories?: ManagedMemory[];
    anticipations?: AnticipationInsight[];
    actions?: ActionProposal[];
    orchestration?: OrchestrationRun | null;
  },
  now?: number,
): TimelineEvent[];
