"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { MemoryIcon, SearchIcon, ShieldIcon } from "@/components/icons";
import { EmptyState, PageHeading } from "@/components/ui";
import { ApiError, checkPermanentDeleteSupport, getMemoryOperation, listMemories, recallMemories, reconcileMemoryOperation, rememberMemory, restoreMemory, trashMemory } from "@/lib/api";
import type { ManagedMemory, MemoryOperation, MemoryRecallResponse } from "@/lib/types";

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
  const [folder, setFolder] = useState<"active" | "trash">("active");
  const [memories, setMemories] = useState<ManagedMemory[]>([]);
  const [folderBusy, setFolderBusy] = useState(true);
  const [folderError, setFolderError] = useState<string | null>(null);
  const [memoryActionMessage, setMemoryActionMessage] = useState<string | null>(null);
  const [memoryActionBusy, setMemoryActionBusy] = useState<string | null>(null);
  const rememberAttempt = useRef<{ text: string; idempotencyKey: string } | null>(null);

  useEffect(() => {
    let active = true;
    setFolderBusy(true);
    setFolderError(null);
    void listMemories(folder)
      .then((result) => {
        if (active) setMemories(result.memories);
      })
      .catch((reason: unknown) => {
        if (active) {
          setFolderError(reason instanceof ApiError ? reason.message : "The memory list is unavailable.");
        }
      })
      .finally(() => {
        if (active) setFolderBusy(false);
      });
    return () => {
      active = false;
    };
  }, [folder]);

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
      if (saved.status === "confirmed" && folder === "active") {
        try {
          setMemories((await listMemories("active")).memories);
        } catch (reason) {
          setFolderError(reason instanceof ApiError ? reason.message : "The memory list could not be refreshed.");
        }
      }
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

  async function moveMemory(memory: ManagedMemory, action: "trash" | "restore" | "permanent-delete") {
    setMemoryActionBusy(memory.operation_id);
    setMemoryActionMessage(null);
    let successMessage: string;
    try {
      if (action === "trash") {
        await trashMemory(memory.operation_id);
        successMessage = "Moved to Trash. Its Walrus data has not been deleted.";
      } else if (action === "restore") {
        await restoreMemory(memory.operation_id);
        successMessage = "Memory restored to Active Memories.";
      } else {
        await checkPermanentDeleteSupport(memory.operation_id);
        successMessage = "Permanent deletion is not available. The memory remains in Trash.";
      }
    } catch (reason) {
      setMemoryActionMessage(
        reason instanceof ApiError && reason.status === 501
          ? "Walrus permanent deletion is unsupported. This memory remains in Trash and its remote data is unchanged."
          : reason instanceof ApiError
            ? reason.message
            : "The memory action could not be completed.",
      );
      setMemoryActionBusy(null);
      return;
    }
    setMemoryActionMessage(successMessage);
    try {
      const updated = await listMemories(folder);
      setMemories(updated.memories);
    } catch (reason) {
      setFolderError(reason instanceof ApiError ? reason.message : "The memory list could not be refreshed.");
    } finally {
      setMemoryActionBusy(null);
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
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-[13px] font-semibold text-[#e4eaf0]">Your memories</h2>
            <p className="mt-1 text-[11px] leading-5 text-[#8795a4]">
              Trash is recoverable and user-controlled. No memory is automatically or remotely deleted.
            </p>
          </div>
          <div className="flex gap-2" role="tablist" aria-label="Memory folders">
            {(["active", "trash"] as const).map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                aria-selected={folder === item}
                className={folder === item ? "primary-button" : "secondary-button"}
                onClick={() => {
                  setFolder(item);
                  setMemoryActionMessage(null);
                }}
              >
                {item === "active" ? "Active Memories" : "Trash"}
              </button>
            ))}
          </div>
        </div>
        {memoryActionMessage && <p role="status" className="mt-4 text-[11px] text-[#a9c5d9]">{memoryActionMessage}</p>}
        {folderError && <p role="alert" className="mt-4 text-[11px] text-[#e2a8a4]">{folderError}</p>}
        {folderBusy ? (
          <p className="mt-5 text-[11px] text-[#8795a4]">Loading memories…</p>
        ) : memories.length ? (
          <ul className="mt-5 space-y-3">
            {memories.map((item) => (
              <li key={item.operation_id} className="rounded-xl border border-[#26323e] bg-[#0f151c] p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <p className="whitespace-pre-wrap text-[11px] leading-5 text-[#c2ccd5]">{item.text}</p>
                  <span className="rounded-full border border-[#35404b] px-2.5 py-1 text-[10px] capitalize text-[#a2afbb]">
                    {item.trashed_at ? "In Trash" : "Active"}
                  </span>
                </div>
                <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                  <div className="text-[10px] text-[#748394]">
                    <span>{item.category}</span>
                    <span className="mx-2">·</span>
                    <span>{new Date(item.created_at || "").toLocaleString()}</span>
                    {item.blob_id && <code className="ml-2 break-all">Walrus blob: {item.blob_id}</code>}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {folder === "active" ? (
                      <button type="button" className="secondary-button text-[10px]" onClick={() => void moveMemory(item, "trash")} disabled={memoryActionBusy !== null}>
                        {memoryActionBusy === item.operation_id ? "Moving…" : "Move to Trash"}
                      </button>
                    ) : (
                      <>
                        <button type="button" className="secondary-button text-[10px]" onClick={() => void moveMemory(item, "restore")} disabled={memoryActionBusy !== null || item.permanent_delete_started_at !== null}>
                          {memoryActionBusy === item.operation_id ? "Restoring…" : "Restore"}
                        </button>
                        <button type="button" className="secondary-button text-[10px]" onClick={() => void moveMemory(item, "permanent-delete")} disabled={memoryActionBusy !== null || item.permanent_delete_started_at !== null}>
                          Check permanent-delete support
                        </button>
                      </>
                    )}
                  </div>
                </div>
                {item.permanent_delete_started_at && (
                  <p className="mt-3 text-[10px] text-[#d4a39f]">Permanent deletion has started; this memory cannot be restored.</p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <div className="mt-5">
            <EmptyState title={folder === "active" ? "No active memories" : "Trash is empty"}>
              {folder === "active" ? "Save a memory or restore one from Trash." : "Deleted memories will appear here until restored."}
            </EmptyState>
          </div>
        )}
        {folder === "trash" && (
          <p className="mt-4 text-[10px] leading-5 text-[#748394]">
            Permanent deletion is currently unsupported by the configured Walrus service. Checking support will not delete the remote record.
          </p>
        )}
      </section>

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
