"use client";

import { FormEvent, useRef, useState } from "react";
import { MemoryIcon, SearchIcon, ShieldIcon } from "@/components/icons";
import { EmptyState, PageHeading } from "@/components/ui";
import { ApiError, getMemoryOperation, recallMemories, reconcileMemoryOperation, rememberMemory } from "@/lib/api";
import type { MemoryOperation, MemoryRecallResponse } from "@/lib/types";

export default function MemoryPage() {
  const [text, setText] = useState("");
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [operation, setOperation] = useState<MemoryOperation | null>(null);
  const [query, setQuery] = useState("");
  const [recallBusy, setRecallBusy] = useState(false);
  const [recallError, setRecallError] = useState<string | null>(null);
  const [recall, setRecall] = useState<MemoryRecallResponse | null>(null);
  const [operationId, setOperationId] = useState("");
  const [lookupBusy, setLookupBusy] = useState(false);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const rememberAttempt = useRef<{ text: string; idempotencyKey: string } | null>(null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaveBusy(true);
    setSaveError(null);
    setOperation(null);
    const normalizedText = text.trim();
    if (!rememberAttempt.current || rememberAttempt.current.text !== normalizedText) {
      rememberAttempt.current = { text: normalizedText, idempotencyKey: crypto.randomUUID() };
    }
    const { idempotencyKey } = rememberAttempt.current;
    try {
      const saved = await rememberMemory({
        text: normalizedText,
        idempotency_key: idempotencyKey,
      });
      rememberAttempt.current = null;
      setOperation(saved);
      if (saved.status !== "failed") setText("");
    } catch (reason) {
      setSaveError(
        reason instanceof ApiError && reason.status === 503
          ? "The save outcome could not be confirmed. Retry this same save to retrieve its operation; do not edit and resubmit it as a new memory."
          : reason instanceof ApiError
            ? reason.message
            : "The memory could not be saved.",
      );
    } finally {
      setSaveBusy(false);
    }
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRecallBusy(true);
    setRecallError(null);
    setRecall(null);
    try {
      setRecall(await recallMemories(query));
    } catch (reason) {
      setRecallError(reason instanceof ApiError ? reason.message : "Memory recall is unavailable.");
    } finally {
      setRecallBusy(false);
    }
  }

  async function lookup(reconcile = false) {
    const id = (operation?.operation_id || operationId).trim();
    if (!id) return;
    setLookupBusy(true);
    setLookupError(null);
    try {
      setOperation(reconcile ? await reconcileMemoryOperation(id) : await getMemoryOperation(id));
    } catch (reason) {
      setLookupError(reason instanceof ApiError ? reason.message : "The operation status is unavailable.");
    } finally {
      setLookupBusy(false);
    }
  }

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="Personal memory"
        title="Keep what matters."
        description="Save information you want ORHEX to remember, then retrieve relevant context through semantic recall."
        action={<span className="inline-flex items-center gap-2 self-start rounded-full border border-[#35404b] px-3 py-1.5 text-[10px] text-[#a2afbb] sm:self-auto"><span className="status-dot" data-status="connected" /> Authenticated session</span>}
      />
      <div className="grid gap-5 xl:grid-cols-2">
        <section className="surface-card p-5 sm:p-7">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><MemoryIcon className="h-[18px] w-[18px]" /></span>
            <div>
              <h2 className="text-[13px] font-semibold text-[#e4eaf0]">Save a memory</h2>
              <p className="mt-1 text-[10px] text-[#7e8d9d]">Writes are confirmed by the connected memory service.</p>
            </div>
          </div>
          <form onSubmit={save} className="mt-5 space-y-3">
            <label htmlFor="memory-text" className="block text-[11px] font-medium text-[#b8c4ce]">What should ORHEX remember?</label>
            <textarea id="memory-text" className="text-input resize-y" rows={5} minLength={3} maxLength={8000} required value={text} onChange={(event) => setText(event.target.value)} placeholder="Write a preference, commitment, or detail that matters to you." />
            <p className="text-[10px] leading-5 text-[#748394]">Only submit information you intend to store in your private memory namespace.</p>
            {saveError && <p role="alert" className="text-[11px] text-[#e2a8a4]">{saveError}</p>}
            <button type="submit" className="primary-button" disabled={saveBusy || text.trim().length < 3}>{saveBusy ? "Saving…" : "Save memory"}</button>
          </form>
          {operation && (
            <div className="mt-5 rounded-xl border border-[#2b3946] bg-[#0f151c] p-4" role="status">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[12px] font-semibold text-[#dce5ec]">Memory operation: <span className="capitalize">{operation.status.replaceAll("_", " ")}</span></p>
                <code className="max-w-full break-all text-[10px] text-[#90a1b0]">{operation.operation_id}</code>
              </div>
              {operation.error && <p className="mt-2 text-[11px] text-[#d4a39f]">{operation.error}</p>}
              {(operation.status === "pending" || operation.status === "reconciliation_required") && (
                <button type="button" className="secondary-button mt-3 text-[10px]" onClick={() => void lookup(true)} disabled={lookupBusy}>
                  {lookupBusy ? "Checking…" : "Check and reconcile status"}
                </button>
              )}
            </div>
          )}
        </section>

        <section className="surface-card p-5 sm:p-7">
          <div className="flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><SearchIcon className="h-[18px] w-[18px]" /></span>
            <div>
              <h2 className="text-[13px] font-semibold text-[#e4eaf0]">Recall relevant context</h2>
              <p className="mt-1 text-[10px] text-[#7e8d9d]">Searches only memories available to your authenticated account.</p>
            </div>
          </div>
          <form onSubmit={search} className="mt-5 space-y-3">
            <label htmlFor="memory-query" className="block text-[11px] font-medium text-[#b8c4ce]">What context are you looking for?</label>
            <input id="memory-query" className="text-input" minLength={3} maxLength={1000} required value={query} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. preferences for planning my week" />
            {recallError && <p role="alert" className="text-[11px] text-[#e2a8a4]">{recallError}</p>}
            <button type="submit" className="secondary-button" disabled={recallBusy || query.trim().length < 3}>{recallBusy ? "Searching…" : "Recall memories"}</button>
          </form>
          {recall && (
            <div className="mt-5 border-t border-[#202a34] pt-4">
              <p className="text-[10px] text-[#8291a1]">{recall.total} result{recall.total === 1 ? "" : "s"} returned</p>
              {recall.results.length ? (
                <ul className="mt-3 space-y-3">
                  {recall.results.map((item) => <li key={item.blob_id} className="rounded-xl border border-[#26323e] bg-[#0f151c] p-3 text-[11px] leading-5 text-[#c2ccd5]">{item.text}</li>)}
                </ul>
              ) : <EmptyState title="No relevant memories found">Try a different query. No examples or placeholder memories are shown.</EmptyState>}
            </div>
          )}
        </section>
      </div>

      <section className="surface-card p-5 sm:p-7">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]"><ShieldIcon className="h-[18px] w-[18px]" /></span>
          <div className="w-full">
            <h2 className="text-[13px] font-semibold text-[#e4eaf0]">Operation status</h2>
            <p className="mt-1 text-[11px] leading-5 text-[#8795a4]">Look up or reconcile a pending memory operation. Reconciliation checks status and never resubmits the write.</p>
            <form className="mt-4 flex flex-col gap-3 sm:flex-row" onSubmit={(event) => { event.preventDefault(); void lookup(false); }}>
              <label htmlFor="operation-id" className="sr-only">Memory operation identifier</label>
              <input id="operation-id" className="text-input min-w-0 flex-1" value={operationId} onChange={(event) => setOperationId(event.target.value)} placeholder={operation?.operation_id || "Operation ID"} />
              <button type="submit" className="secondary-button shrink-0" disabled={lookupBusy || (!operationId.trim() && !operation)}>{lookupBusy ? "Loading…" : "Retrieve status"}</button>
            </form>
            {lookupError && <p role="alert" className="mt-3 text-[11px] text-[#e2a8a4]">{lookupError}</p>}
          </div>
        </div>
      </section>
    </div>
  );
}
