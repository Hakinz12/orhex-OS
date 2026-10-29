"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { ArrowRightIcon, MemoryIcon, MessageIcon, SearchIcon, ShieldIcon, SparkIcon } from "@/components/icons";
import { PageHeading } from "@/components/ui";
import { ApiError, fetchMemoryStatus, recallMemories, rememberMemory } from "@/lib/api";
import type { MemoryOperation, MemoryRecallResponse, MemoryServiceStatus } from "@/lib/types";

export default function WorkspacePage() {
  const [memoryStatus, setMemoryStatus] = useState<MemoryServiceStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusFailed, setStatusFailed] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const isMounted = useRef(false);
  const statusRequestId = useRef(0);
  const [rememberText, setRememberText] = useState("");
  const [rememberBusy, setRememberBusy] = useState(false);
  const [rememberError, setRememberError] = useState<string | null>(null);
  const [operation, setOperation] = useState<MemoryOperation | null>(null);
  const [question, setQuestion] = useState("");
  const [recallBusy, setRecallBusy] = useState(false);
  const [recallError, setRecallError] = useState<string | null>(null);
  const [recall, setRecall] = useState<MemoryRecallResponse | null>(null);

  async function refreshMemoryStatus() {
    const requestId = ++statusRequestId.current;
    setStatusLoading(true);
    setMemoryStatus(null);
    setStatusFailed(false);
    setSessionExpired(false);
    try {
      const status = await fetchMemoryStatus();
      if (isMounted.current && requestId === statusRequestId.current) setMemoryStatus(status);
    } catch (reason) {
      if (isMounted.current && requestId === statusRequestId.current) {
        setStatusFailed(true);
        setSessionExpired(reason instanceof ApiError && reason.status === 401);
      }
    } finally {
      if (isMounted.current && requestId === statusRequestId.current) setStatusLoading(false);
    }
  }

  useEffect(() => {
    isMounted.current = true;
    void refreshMemoryStatus();
    return () => {
      isMounted.current = false;
      statusRequestId.current += 1;
    };
  }, []);

  const statusLabel = statusLoading
    ? "Checking memory…"
    : statusFailed || !memoryStatus
      ? "Memory status unavailable"
      : memoryStatus.status === "ready" && memoryStatus.write_ready === true
        ? "Walrus memory ready"
        : memoryStatus.status === "initializing"
          ? "Memory initializing"
          : memoryStatus.status === "unconfigured"
            ? "Memory not configured"
            : memoryStatus.status === "rate_limited"
              ? "Memory rate limited"
              : memoryStatus.status === "unreachable"
                ? "Memory unreachable"
                : "Memory degraded";

  const statusTone = statusLoading
    ? "border-[#35404b] bg-[#17212b] text-[#a2afbb]"
    : statusLabel === "Walrus memory ready"
      ? "border-[#315342] bg-[#17231d] text-[#a8c8b3]"
      : statusLabel === "Memory status unavailable" || statusLabel === "Memory unreachable"
        ? "border-[#563c3d] bg-[#261b1c] text-[#d3aaa6]"
        : "border-[#51472f] bg-[#241f16] text-[#d4c08d]";

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRememberBusy(true);
    setRememberError(null);
    setOperation(null);
    try {
      setOperation(await rememberMemory({ text: rememberText }));
      setRememberText("");
    } catch (reason) {
      setRememberError(reason instanceof ApiError ? reason.message : "The memory could not be saved.");
    } finally {
      setRememberBusy(false);
    }
  }

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRecallBusy(true);
    setRecallError(null);
    setRecall(null);
    try {
      setRecall(await recallMemories(question));
    } catch (reason) {
      setRecallError(reason instanceof ApiError ? reason.message : "Context recall is unavailable.");
    } finally {
      setRecallBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="AI workspace"
        title="Think with your context."
        description="Use your connected memory to save information and retrieve relevant context. Generative chat is not available in the current backend contract."
        action={
          <div className="flex flex-wrap items-center gap-2 self-start sm:self-auto">
            <span className="rounded-full border border-[#35404b] bg-[#17212b] px-3 py-1.5 text-[10px] text-[#a2afbb]">
              {memoryStatus
                ? "Authenticated session"
                : sessionExpired
                  ? "Session expired"
                  : statusLoading
                    ? "Verifying session…"
                    : "Session unavailable"}
            </span>
            <span
              role="status"
              aria-live="polite"
              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[10px] ${statusTone}`}
            >
              <span
                className="status-dot"
                data-status={
                  statusLoading
                    ? "unknown"
                    : statusLabel === "Walrus memory ready"
                      ? "connected"
                      : statusLabel === "Memory status unavailable" || statusLabel === "Memory unreachable"
                        ? "error"
                        : "degraded"
                }
              />
              {statusLabel}
            </span>
            <button
              type="button"
              className="secondary-button text-[10px]"
              onClick={() => void refreshMemoryStatus()}
              disabled={statusLoading}
            >
              {statusLoading ? "Checking…" : "Refresh"}
            </button>
          </div>
        }
      />
      {sessionExpired && (
        <p role="alert" className="text-[11px] text-[#d3aaa6]">
          Your session may have expired. Sign in again to check memory status.
        </p>
      )}
      <section className="grid gap-5 xl:grid-cols-2">
        <article className="surface-card overflow-hidden">
          <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><MemoryIcon className="h-[18px] w-[18px]" /></span>
            <div>
              <p className="text-[12px] font-semibold text-[#e4eaf0]">Save information to memory</p>
              <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Stored under the identity verified by the backend.</p>
            </div>
          </div>
          <form onSubmit={save} className="space-y-3 p-5 sm:p-7">
            <label htmlFor="workspace-remember" className="block text-[11px] font-medium text-[#b8c4ce]">What would you like ORHEX to remember?</label>
            <textarea id="workspace-remember" className="text-input resize-y" rows={5} minLength={3} maxLength={8000} required value={rememberText} onChange={(event) => setRememberText(event.target.value)} placeholder="Share a preference, commitment, or detail to save." />
            {rememberError && <p role="alert" className="text-[11px] text-[#e2a8a4]">{rememberError}</p>}
            <button type="submit" className="primary-button" disabled={rememberBusy || rememberText.trim().length < 3}>
              <SparkIcon className="h-4 w-4" />{rememberBusy ? "Saving…" : "Save to memory"}
            </button>
            {operation && <p role="status" className="text-[11px] text-[#a8c8b3]">Memory operation {operation.status}: {operation.operation_id}</p>}
          </form>
        </article>

        <article className="surface-card overflow-hidden">
          <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><MessageIcon className="h-[18px] w-[18px]" /></span>
            <div>
              <p className="text-[12px] font-semibold text-[#e4eaf0]">Recall relevant context</p>
              <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Semantic results from your actual saved memories.</p>
            </div>
          </div>
          <form onSubmit={ask} className="space-y-3 p-5 sm:p-7">
            <label htmlFor="workspace-question" className="block text-[11px] font-medium text-[#b8c4ce]">What would you like to recall?</label>
            <textarea id="workspace-question" className="text-input resize-y" rows={3} minLength={3} maxLength={1000} required value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ask a question about context you have saved." />
            {recallError && <p role="alert" className="text-[11px] text-[#e2a8a4]">{recallError}</p>}
            <button type="submit" className="secondary-button" disabled={recallBusy || question.trim().length < 3}>
              <SearchIcon className="h-4 w-4" />{recallBusy ? "Recalling…" : "Recall context"}
            </button>
          </form>
          {recall && (
            <div className="border-t border-[#202a34] px-5 py-4 sm:px-7">
              <p className="text-[10px] text-[#8291a1]">{recall.total} result{recall.total === 1 ? "" : "s"} returned</p>
              {recall.results.length ? (
                <ul className="mt-3 space-y-3">
                  {recall.results.map((item) => <li key={item.blob_id} className="rounded-xl border border-[#26323e] bg-[#0f151c] p-3 text-[11px] leading-5 text-[#c2ccd5]">{item.text}</li>)}
                </ul>
              ) : <p className="mt-3 text-[11px] text-[#8493a3]">No relevant memory was returned for this query.</p>}
            </div>
          )}
        </article>
      </section>
      <div className="surface-inset flex flex-col justify-between gap-4 p-5 sm:flex-row sm:items-center sm:p-6">
        <div>
          <p className="inline-flex items-center gap-2 text-[12px] font-semibold text-[#dfe6ec]"><ShieldIcon className="h-4 w-4 text-[#b8d2e5]" /> No generated answers are shown</p>
          <p className="mt-1 text-[11px] text-[#8492a1]">The backend currently supports memory writes and semantic recall, not conversational generation.</p>
        </div>
        <Link href="/memory" className="secondary-button shrink-0 text-[11px]">Memory tools <ArrowRightIcon className="h-3.5 w-3.5" /></Link>
      </div>
    </div>
  );
}
