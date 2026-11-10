function validDate(value) {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

export function buildTimelineEvents(
  { tasks = [], memories = [], anticipations = [], actions = [], orchestration = null },
  now = Date.now(),
) {
  const events = [];
  const add = (event) => {
    if (validDate(event.timestamp)) events.push(event);
  };

  for (const task of tasks) {
    add({
      id: `task-created:${task.id}`,
      category: "tasks",
      title: "Task added",
      description: task.title,
      timestamp: task.created_at,
    });
    if (task.status === "completed" && validDate(task.completed_at)) {
      add({
        id: `task-completed:${task.id}`,
        category: "tasks",
        title: "Task completed",
        description: task.title,
        timestamp: task.completed_at,
      });
    } else if (
      task.status !== "cancelled" &&
      task.status !== "completed" &&
      validDate(task.due_at)
    ) {
      add({
        id: `task-deadline:${task.id}`,
        category: "events",
        title: Date.parse(task.due_at) < now ? "Task deadline passed" : "Task deadline coming up",
        description: task.title,
        timestamp: task.due_at,
      });
    }
  }

  for (const memory of memories) {
    if (memory.status !== "confirmed") continue;
    add({
      id: `memory:${memory.operation_id}`,
      category: "memories",
      title: "Memory saved",
      description: memory.text,
      timestamp: memory.confirmed_at ?? memory.created_at,
    });
  }

  for (const insight of anticipations) {
    if (insight.status !== "active") continue;
    add({
      id: `insight:${insight.id}`,
      category: "events",
      title: insight.title,
      description: insight.explanation,
      timestamp: insight.due_at ?? insight.created_at,
    });
  }

  for (const action of actions) {
    add({
      id: `action-proposed:${action.id}`,
      category: "orhex",
      title: "ORHEX prepared a suggestion",
      description: action.title,
      timestamp: action.created_at,
    });
    if (action.status === "executed" && validDate(action.executed_at)) {
      add({
        id: `action-executed:${action.id}`,
        category: "orhex",
        title: "Approved ORHEX suggestion completed",
        description: action.title,
        timestamp: action.executed_at,
      });
    }
  }

  if (orchestration) {
    add({
      id: `orchestration:${orchestration.id}`,
      category: "orhex",
      title: "ORHEX decision cycle completed",
      description: orchestration.summary?.headline ?? "A decision cycle was run.",
      timestamp: orchestration.summary?.generated_at ?? orchestration.created_at,
    });
  }

  const unique = new Map(events.map((event) => [event.id, event]));
  return [...unique.values()].sort(
    (left, right) => Date.parse(right.timestamp) - Date.parse(left.timestamp),
  );
}
