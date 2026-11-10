import assert from "node:assert/strict";
import test from "node:test";
import { buildTimelineEvents } from "../src/lib/timeline-events.mjs";

test("timeline contains only recorded data, deduplicates IDs, and sorts newest first", () => {
  const events = buildTimelineEvents(
    {
      tasks: [
        {
          id: "task-1",
          title: "Complete report",
          status: "completed",
          created_at: "2026-11-01T10:00:00Z",
          completed_at: "2026-11-03T10:00:00Z",
          due_at: "2026-11-02T10:00:00Z",
        },
        {
          id: "task-2",
          title: "Cancelled task",
          status: "cancelled",
          created_at: "2026-11-02T10:00:00Z",
          completed_at: null,
          due_at: "2026-11-04T10:00:00Z",
        },
      ],
      memories: [
        {
          operation_id: "memory-1",
          text: "A confirmed fact",
          status: "confirmed",
          created_at: "2026-11-01T11:00:00Z",
          confirmed_at: "2026-11-01T11:30:00Z",
        },
        {
          operation_id: "memory-pending",
          text: "Not yet confirmed",
          status: "pending",
          created_at: "2026-11-05T11:00:00Z",
        },
      ],
      anticipations: [
        {
          id: "insight-1",
          title: "Commitment due",
          explanation: "Recorded deadline.",
          status: "active",
          created_at: "2026-11-02T08:00:00Z",
          due_at: "2026-11-06T08:00:00Z",
        },
      ],
    },
    Date.parse("2026-11-05T00:00:00Z"),
  );

  assert.deepEqual(events.map((event) => event.id), [
    "insight:insight-1",
    "task-completed:task-1",
    "task-created:task-2",
    "memory:memory-1",
    "task-created:task-1",
  ]);
  assert.equal(events.some((event) => event.id.includes("memory-pending")), false);
  assert.equal(events.some((event) => event.id === "task-deadline:task-2"), false);
  assert.equal(new Set(events.map((event) => event.id)).size, events.length);
});

test("timeline discards malformed timestamps instead of inventing dates", () => {
  assert.deepEqual(
    buildTimelineEvents({
      tasks: [{
        id: "bad-time",
        title: "No timestamp",
        status: "todo",
        created_at: "not-a-date",
      }],
    }),
    [],
  );
});
