"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createTask,
  decideAction,
  fetchActionProposals,
  fetchLatestOrchestration,
  fetchTasks,
  prepareActionProposals,
  runOrchestration,
  updateTask,
} from "@/lib/api";
import type { ActionProposal, OrhexTask, OrchestrationRun } from "@/lib/types";

export default function TasksPage() {
  const [tasks, setTasks] = useState<OrhexTask[]>([]);
  const [actions, setActions] = useState<ActionProposal[]>([]);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [orchestration, setOrchestration] = useState<OrchestrationRun | null>(null);
  const operationInFlight = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextTasks, nextActions, nextOrchestration] = await Promise.all([
        fetchTasks(),
        fetchActionProposals(),
        fetchLatestOrchestration().catch((reason: unknown) => {
          if (reason instanceof ApiError && reason.status === 404) return null;
          throw reason;
        }),
      ]);
      setTasks(nextTasks);
      setActions(nextActions);
      setOrchestration(nextOrchestration);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load tasks and suggestions.");
      return false;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runOperation(
    action: () => Promise<void>,
    successMessage?: string,
    reload = true,
  ) {
    if (operationInFlight.current) return;
    operationInFlight.current = true;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      await action();
      if (reload && !(await load())) return;
      if (successMessage) setSuccess(successMessage);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The requested task action could not be completed.");
    } finally {
      operationInFlight.current = false;
      setBusy(false);
    }
  }

  function addTask(event: React.FormEvent) {
    event.preventDefault();
    const taskTitle = title.trim();
    if (!taskTitle) return;
    void runOperation(
      async () => {
        await createTask({ title: taskTitle, priority: "medium" });
        setTitle("");
      },
      "Task added.",
    );
  }

  function orchestrate() {
    void runOperation(async () => {
      setOrchestration(await runOrchestration());
    }, "Decision cycle completed.", false);
  }

  function prepare() {
    void runOperation(async () => {
      setActions(await prepareActionProposals());
    }, "Suggestions refreshed.", false);
  }

  function decide(id: string, status: "approved" | "dismissed") {
    void runOperation(
      async () => { await decideAction(id, status); },
      status === "approved" ? "Suggestion approved and task created." : "Suggestion dismissed.",
    );
  }

  function toggleTask(task: OrhexTask, completed: boolean) {
    void runOperation(
      async () => { await updateTask(task.id, { status: completed ? "completed" : "todo" }); },
      completed ? "Task marked complete." : "Task reopened.",
    );
  }

  const openCount = tasks.filter((task) => task.status !== "completed" && task.status !== "cancelled").length;
  const pendingActions = actions.filter((action) => action.status === "proposed");

  return (
    <main className="mx-auto max-w-5xl space-y-8 px-6 py-10">
      <header>
        <p className="text-sm font-medium uppercase tracking-[.2em] text-cyan-500">LifeOS / Execution</p>
        <h1 className="mt-2 text-3xl font-semibold">Tasks &amp; actions</h1>
        <p className="mt-2 text-sm text-neutral-500">Turn priorities into trackable work. ORHEX prepares suggestions; you approve before they create tasks.</p>
      </header>

      <section className="rounded-2xl border border-cyan-500/30 bg-cyan-500/5 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-2xl">
            <p className="text-xs font-semibold uppercase tracking-[.18em] text-cyan-600">ORHEX intelligence core</p>
            <h2 className="mt-2 text-xl font-semibold">Autonomous orchestration</h2>
            <p className="mt-1 text-sm text-neutral-500">Run a decision cycle across your goals, commitments, anticipation signals and open tasks. ORHEX ranks next steps; proposals still require your approval.</p>
          </div>
          <button type="button" disabled={busy || loading} onClick={orchestrate} className="rounded-lg bg-cyan-600 px-4 py-2.5 text-sm font-semibold text-white">
            {busy ? "Working…" : "Run decision cycle"}
          </button>
        </div>
        {orchestration && (
          <div className="mt-5 border-t border-cyan-500/20 pt-4">
            <p className="font-medium">{orchestration.summary.headline}</p>
            <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
              {([
                ["Open tasks", orchestration.summary.counts.open_tasks],
                ["Overdue", orchestration.summary.counts.overdue_tasks],
                ["Due in 24h", orchestration.summary.counts.tasks_due_within_24h],
                ["Awaiting approval", orchestration.summary.counts.pending_approvals],
              ] as const).map(([label, value]) => (
                <div key={label} className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
                  <p className="text-xs text-neutral-500">{label}</p>
                  <p className="mt-1 text-2xl font-semibold">{value}</p>
                </div>
              ))}
            </div>
            {orchestration.summary.recommendations.length > 0 && (
              <div className="mt-4 space-y-2">
                {orchestration.summary.recommendations.slice(0, 5).map((item) => (
                  <article key={`${item.source_type}:${item.source_id}`} className="rounded-lg border border-neutral-200 p-3 dark:border-neutral-800">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-xs uppercase tracking-wide text-cyan-600">{item.priority}</span>
                      {item.requires_approval && <span className="text-xs text-amber-600">Approval required</span>}
                    </div>
                    <p className="mt-1 text-sm font-medium">{item.title}</p>
                    <p className="mt-1 text-xs text-neutral-500">{item.reason}</p>
                  </article>
                ))}
              </div>
            )}
            <p className="mt-3 text-xs text-neutral-500">Last run: {new Date(orchestration.created_at).toLocaleString()} · No external actions or wallet transactions were executed.</p>
          </div>
        )}
      </section>

      {error && <p role="alert" className="rounded-lg border border-red-300 p-3 text-sm">{error}</p>}
      {success && <p role="status" className="rounded-lg border border-emerald-500/30 p-3 text-sm text-emerald-700 dark:text-emerald-300">{success}</p>}

      <section className="rounded-2xl border border-neutral-200 p-5 dark:border-neutral-800">
        <h2 className="text-lg font-semibold">Quick capture</h2>
        <form onSubmit={addTask} className="mt-3 flex flex-col gap-3 sm:flex-row">
          <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={256} placeholder="What needs to get done?" className="min-w-0 flex-1 rounded-lg border border-neutral-300 bg-transparent px-3 py-3 dark:border-neutral-700" />
          <button type="submit" disabled={busy || !title.trim()} className="rounded-lg bg-cyan-600 px-5 py-3 font-medium text-white">Add task</button>
        </form>
      </section>

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold">Your tasks <span className="text-sm font-normal text-neutral-500">({openCount} open)</span></h2>
          <button type="button" disabled={loading || busy} onClick={() => void load()} className="text-sm underline">{loading ? "Loading…" : "Refresh"}</button>
        </div>
        <div className="space-y-3">
          {loading && tasks.length === 0 && <p role="status" className="rounded-xl border border-dashed p-6 text-sm text-neutral-500">Loading your tasks…</p>}
          {!loading && tasks.length === 0 && <p className="rounded-xl border border-dashed p-6 text-sm text-neutral-500">No tasks yet. Capture your first task above.</p>}
          {tasks.map((task) => (
            <article key={task.id} className="flex items-start gap-3 rounded-xl border border-neutral-200 p-4 dark:border-neutral-800">
              <input aria-label={`Complete ${task.title}`} type="checkbox" checked={task.status === "completed"} onChange={(event) => toggleTask(task, event.target.checked)} disabled={busy} className="mt-1 h-4 w-4" />
              <div className="min-w-0 flex-1">
                <p className={task.status === "completed" ? "text-neutral-400 line-through" : "font-medium"}>{task.title}</p>
                {task.description && <p className="mt-1 text-sm text-neutral-500">{task.description}</p>}
                <p className="mt-2 text-xs text-neutral-500">{task.priority} priority{task.due_at ? ` · Due ${new Date(task.due_at).toLocaleString()}` : ""}{task.source_type ? ` · From ${task.source_type}` : ""}</p>
              </div>
              <span className="text-xs text-neutral-500">{task.status.replace("_", " ")}</span>
            </article>
          ))}
        </div>
      </section>

      <section>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold">Suggested actions</h2>
            <p className="mt-1 text-sm text-neutral-500">Derived from active anticipation signals. Nothing runs until you approve it.</p>
          </div>
          <button type="button" disabled={busy || loading} onClick={prepare} className="rounded-lg border border-neutral-300 px-4 py-2 text-sm dark:border-neutral-700">
            {busy ? "Working…" : "Prepare suggestions"}
          </button>
        </div>
        <div className="space-y-3">
          {!loading && pendingActions.length === 0 && <p className="rounded-xl border border-dashed p-6 text-sm text-neutral-500">No pending suggestions. Refresh anticipation signals, then prepare actions.</p>}
          {pendingActions.map((action) => (
            <article key={action.id} className="rounded-xl border border-neutral-200 p-4 dark:border-neutral-800">
              <h3 className="font-medium">{action.title}</h3>
              <p className="mt-2 text-sm text-neutral-500">{action.rationale}</p>
              <div className="mt-4 flex gap-2">
                <button type="button" disabled={busy || loading} onClick={() => decide(action.id, "approved")} className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white">
                  {busy ? "Working…" : "Approve & create task"}
                </button>
                <button type="button" disabled={busy || loading} onClick={() => decide(action.id, "dismissed")} className="rounded-lg border border-neutral-300 px-4 py-2 text-sm dark:border-neutral-700">Dismiss</button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
