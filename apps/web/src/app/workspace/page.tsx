"use client";

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import Link from "next/link";
import { ArrowRightIcon, MemoryIcon, MessageIcon, SearchIcon, ShieldIcon, SparkIcon } from "@/components/icons";
import { handleChatComposerKeyDown } from "@/lib/chat-composer.mjs";
import { PageHeading } from "@/components/ui";
import {
  ApiError,
  fetchMemoryStatus,
  getConversation,
  recallMemories,
  reconcileMemoryOperation,
  rememberMemory,
  sendConversationMessage,
} from "@/lib/api";
import type {
  ConversationResponse,
  MemoryOperation,
  MemoryRecallResponse,
  MemoryServiceStatus,
} from "@/lib/types";

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
  const [operationCheckBusy, setOperationCheckBusy] = useState(false);
  const rememberInFlight = useRef(false);
  const operationCheckInFlight = useRef(false);
  const rememberAttempt = useRef<{ text: string; idempotencyKey: string } | null>(null);
  const [question, setQuestion] = useState("");
  const [recallBusy, setRecallBusy] = useState(false);
  const [recallError, setRecallError] = useState<string | null>(null);
  const [recall, setRecall] = useState<MemoryRecallResponse | null>(null);
  const recallInFlight = useRef(false);
  const [conversation, setConversation] = useState<ConversationResponse | null>(null);
  const [conversationLoading, setConversationLoading] = useState(true);
  const [conversationBusy, setConversationBusy] = useState(false);
  const [conversationError, setConversationError] = useState<string | null>(null);
  const [chatDraft, setChatDraft] = useState("");
  const [includeWalletContext, setIncludeWalletContext] = useState(false);
  const conversationRequestId = useRef(0);
  const conversationInFlight = useRef(false);
  const pendingChatRequest = useRef<{
    message: string;
    idempotency_key: string;
    conversation_id?: string;
    include_wallet_context: boolean;
  } | null>(null);

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

  async function restoreConversation() {
    const requestId = ++conversationRequestId.current;
    setConversationLoading(true);
    setConversationError(null);
    try {
      const savedId = window.localStorage.getItem("orhex-conversation-id");
      if (!savedId) {
        if (isMounted.current && requestId === conversationRequestId.current) {
          setConversation(null);
        }
        return;
      }
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(savedId)) {
        window.localStorage.removeItem("orhex-conversation-id");
        if (isMounted.current && requestId === conversationRequestId.current) {
          setConversation(null);
        }
        return;
      }
      const loaded = await getConversation(savedId);
      if (isMounted.current && requestId === conversationRequestId.current) {
        setConversation(loaded);
      }
    } catch (reason) {
      if (isMounted.current && requestId === conversationRequestId.current) {
        if (reason instanceof ApiError && reason.status === 404) {
          window.localStorage.removeItem("orhex-conversation-id");
          setConversation(null);
          setConversationError(null);
        } else {
          setConversationError(
            reason instanceof ApiError
              ? reason.message
              : "Conversation history could not be loaded.",
          );
        }
      }
    } finally {
      if (isMounted.current && requestId === conversationRequestId.current) {
        setConversationLoading(false);
      }
    }
  }

  useEffect(() => {
    isMounted.current = true;
    void refreshMemoryStatus();
    void restoreConversation();
    return () => {
      isMounted.current = false;
      statusRequestId.current += 1;
      conversationRequestId.current += 1;
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
    if (rememberInFlight.current) return;
    rememberInFlight.current = true;
    setRememberBusy(true);
    setRememberError(null);
    setOperation(null);
    const text = rememberText.trim();
    if (!rememberAttempt.current || rememberAttempt.current.text !== text) {
      rememberAttempt.current = { text, idempotencyKey: crypto.randomUUID() };
    }
    const { idempotencyKey } = rememberAttempt.current;
    try {
      const saved = await rememberMemory({ text, idempotency_key: idempotencyKey });
      rememberAttempt.current = null;
      setOperation(saved);
      if (saved.status !== "failed") setRememberText("");
    } catch (reason) {
      setRememberError(
        reason instanceof ApiError && reason.status === 503
          ? "The save outcome could not be confirmed. Retry this same save to retrieve its operation; do not edit and resubmit it as a new memory."
          : reason instanceof ApiError
            ? reason.message
            : "The memory could not be saved.",
      );
    } finally {
      rememberInFlight.current = false;
      setRememberBusy(false);
    }
  }

  async function checkOperation() {
    if (!operation || operationCheckInFlight.current) return;
    operationCheckInFlight.current = true;
    setOperationCheckBusy(true);
    setRememberError(null);
    try {
      setOperation(await reconcileMemoryOperation(operation.operation_id));
    } catch (reason) {
      setRememberError(reason instanceof ApiError ? reason.message : "The operation status is unavailable.");
    } finally {
      operationCheckInFlight.current = false;
      setOperationCheckBusy(false);
    }
  }

  const pendingOperationId = operation?.operation_id;
  const pendingOperationStatus = operation?.status;

  useEffect(() => {
    if (!pendingOperationId || pendingOperationStatus !== "pending") return;
    const operationId = pendingOperationId;
    let cancelled = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout>;

    async function reconcilePendingOperation() {
      if (cancelled || attempts >= 8) return;
      attempts += 1;
      if (operationCheckInFlight.current) {
        timer = setTimeout(() => void reconcilePendingOperation(), 1500);
        return;
      }
      operationCheckInFlight.current = true;
      setOperationCheckBusy(true);
      try {
        const updated = await reconcileMemoryOperation(operationId);
        if (cancelled) return;
        setOperation(updated);
        if (updated.status === "pending" && attempts < 8) {
          timer = setTimeout(() => void reconcilePendingOperation(), 1500);
        }
      } catch (reason) {
        if (!cancelled) {
          setRememberError(
            reason instanceof ApiError
              ? reason.message
              : "Memory confirmation is still pending. Check its status before retrying.",
          );
        }
      } finally {
        operationCheckInFlight.current = false;
        if (!cancelled) setOperationCheckBusy(false);
      }
    }

    timer = setTimeout(() => void reconcilePendingOperation(), 1000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pendingOperationId, pendingOperationStatus]);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (recallInFlight.current) return;
    recallInFlight.current = true;
    setRecallBusy(true);
    setRecallError(null);
    setRecall(null);
    try {
      setRecall(await recallMemories(question));
    } catch (reason) {
      setRecallError(reason instanceof ApiError ? reason.message : "Context recall is unavailable.");
    } finally {
      recallInFlight.current = false;
      setRecallBusy(false);
    }
  }

  async function sendChatMessage() {
    if (conversationInFlight.current) return;
    const message = chatDraft.trim();
    if (!pendingChatRequest.current) {
      if (!message) return;
      pendingChatRequest.current = {
        message,
        idempotency_key: crypto.randomUUID(),
        include_wallet_context: includeWalletContext,
        ...(conversation ? { conversation_id: conversation.conversation_id } : {}),
      };
    }
    conversationInFlight.current = true;
    setConversationBusy(true);
    setConversationError(null);
    try {
      const response = await sendConversationMessage(pendingChatRequest.current);
      pendingChatRequest.current = null;
      setConversation(response);
      setChatDraft("");
      setIncludeWalletContext(false);
      try {
        window.localStorage.setItem("orhex-conversation-id", response.conversation_id);
      } catch {
        setConversationError("Your response is available, but this browser could not save the conversation link for reload continuity.");
      }
    } catch (reason) {
      setConversationError(
        reason instanceof ApiError
          ? reason.message
          : "The assistant could not complete your message. Retry safely.",
      );
    } finally {
      conversationInFlight.current = false;
      setConversationBusy(false);
    }
  }

  async function submitChat(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendChatMessage();
  }

  function startNewConversation() {
    try {
      window.localStorage.removeItem("orhex-conversation-id");
    } catch {
      setConversationError("The previous conversation link could not be removed from this browser.");
      return;
    }
    setConversation(null);
    setChatDraft("");
    setIncludeWalletContext(false);
    setConversationError(null);
  }

  const loadFailureBlocksSending =
    Boolean(conversationError) && !conversation && pendingChatRequest.current === null;

  return (
    <div className="space-y-8">
      <PageHeading
        eyebrow="AI workspace"
        title="Think with your context."
        description="Talk with ORHEX, save information to long-term memory, and retrieve relevant context. Conversation history stays separate from saved memories."
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
      <section className="surface-card overflow-hidden" aria-labelledby="workspace-chat-heading">
        <div className="flex items-center gap-3 border-b border-[#202a34] px-5 py-4 sm:px-7">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#334351] bg-[#19242e] text-[#bdd4e5]">
            <MessageIcon className="h-[18px] w-[18px]" />
          </span>
          <div>
            <h2 id="workspace-chat-heading" className="text-[12px] font-semibold text-[#e4eaf0]">Conversation with ORHEX</h2>
            <p className="mt-0.5 text-[10px] text-[#7e8d9d]">Your conversation is private to this authenticated session and is not automatically saved as long-term memory.</p>
            {conversation?.memory_context.status === "retrieved" && (
              <p className="mt-1 text-[10px] text-[#a8c8b3]" role="status">
                Personal context included ({conversation.memory_context.count} saved {conversation.memory_context.count === 1 ? "memory" : "memories"}).
              </p>
            )}
          </div>
          {conversation && (
            <button
              type="button"
              className="secondary-button ml-auto text-[10px]"
              onClick={startNewConversation}
              disabled={conversationBusy || pendingChatRequest.current !== null}
            >
              New conversation
            </button>
          )}
        </div>
        <div
          className="max-h-[min(55vh,560px)] min-h-[220px] space-y-4 overflow-y-auto px-5 py-5 sm:px-7"
          role="log"
          aria-live="polite"
          aria-label="Conversation messages"
        >
          {conversationLoading ? (
            <p role="status" className="py-8 text-center text-[12px] text-[#8493a3]">Loading your conversation…</p>
          ) : conversation?.messages.length ? (
            conversation.messages.map((item) => (
              <article
                key={item.id}
                className={`max-w-[90%] rounded-2xl border px-4 py-3 text-[12px] leading-6 sm:max-w-[80%] ${
                  item.role === "user"
                    ? "ml-auto border-[#334351] bg-[#19242e] text-[#e0e7ed]"
                    : "mr-auto border-[#26323e] bg-[#0f151c] text-[#c2ccd5]"
                }`}
              >
                <p className="mb-1 text-[9px] font-semibold uppercase tracking-wide text-[#8493a3]">
                  {item.role === "user" ? "You" : "ORHEX"}
                </p>
                <p className="whitespace-pre-wrap break-words">{item.content}</p>
              </article>
            ))
          ) : (
            <div className="flex min-h-[180px] flex-col items-center justify-center text-center">
              <p className="text-[13px] font-semibold text-[#dce3e9]">Start a conversation</p>
              <p className="mt-2 max-w-sm text-[11px] leading-5 text-[#8493a3]">
                Ask ORHEX anything. Your first message begins a private conversation; no history is invented when the conversation is empty.
              </p>
            </div>
          )}
        </div>
        {conversationError && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#202a34] px-5 py-3 sm:px-7">
            <p role="alert" className="text-[11px] text-[#e2a8a4]">{conversationError}</p>
            <button
              type="button"
              className="secondary-button text-[10px]"
              onClick={() => void (pendingChatRequest.current ? sendChatMessage() : restoreConversation())}
              disabled={conversationBusy || conversationLoading}
            >
              {pendingChatRequest.current ? "Retry message" : "Retry loading"}
            </button>
          </div>
        )}
        <form onSubmit={submitChat} className="space-y-3 border-t border-[#202a34] p-5 sm:p-7">
          <label className="flex items-start gap-2 text-[11px] leading-5 text-[#aebbc7]">
            <input
              type="checkbox"
              className="mt-1 accent-[#b8d2e5]"
              checked={includeWalletContext}
              onChange={(event) => setIncludeWalletContext(event.target.checked)}
              disabled={
                conversationLoading ||
                conversationBusy ||
                loadFailureBlocksSending ||
                pendingChatRequest.current !== null
              }
            />
            <span>
              Include my verified Sui wallet snapshot with this message. If enabled, the server may share the verified wallet address, network, and bounded read-only balances with Gemini for this request. No wallet data is shared by default; this does not authorize signing or transfers.
            </span>
          </label>
          <label htmlFor="workspace-chat-message" className="block text-[11px] font-medium text-[#b8c4ce]">Message</label>
          <textarea
            id="workspace-chat-message"
            className="text-input resize-y"
            rows={3}
            maxLength={4000}
            value={chatDraft}
            onChange={(event) => setChatDraft(event.target.value)}
            onKeyDown={(event) => {
              handleChatComposerKeyDown(event, () => void sendChatMessage());
            }}
            placeholder="Write a message to ORHEX…"
            aria-describedby="workspace-chat-keyboard-help"
            disabled={
              conversationLoading ||
              conversationBusy ||
              loadFailureBlocksSending ||
              pendingChatRequest.current !== null
            }
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p id="workspace-chat-keyboard-help" className="text-[10px] text-[#778697]">Enter sends · Shift+Enter adds a line · up to 4,000 characters</p>
            <button
              type="submit"
              className="primary-button"
              disabled={
                conversationLoading ||
                conversationBusy ||
                loadFailureBlocksSending ||
                pendingChatRequest.current !== null ||
                chatDraft.trim().length === 0
              }
            >
              {conversationBusy ? "Thinking…" : "Send message"}
            </button>
          </div>
        </form>
      </section>
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
            {operation && (
              <div role="status" className="text-[11px] text-[#a8c8b3]">
                <p>
                  {operation.status === "confirmed"
                    ? "Memory save confirmed."
                    : operation.status === "failed"
                      ? "Memory save failed. Your text is still available to try again."
                      : operation.status === "pending"
                        ? "Memory save is pending confirmation. Check its status before submitting it again."
                        : operation.status === "reconciliation_required"
                          ? "Memory save outcome is uncertain. Check its status before submitting it again."
                          : `Memory operation status: ${operation.status}`}
                </p>
                <p className="mt-1 break-all">{operation.operation_id}</p>
                {operation.status !== "confirmed" && operation.status !== "failed" && (
                  <button
                    type="button"
                    className="secondary-button mt-2 text-[10px]"
                    onClick={() => void checkOperation()}
                    disabled={operationCheckBusy}
                  >
                    {operationCheckBusy ? "Checking…" : "Check operation status"}
                  </button>
                )}
              </div>
            )}
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
          <p className="inline-flex items-center gap-2 text-[12px] font-semibold text-[#dfe6ec]"><ShieldIcon className="h-4 w-4 text-[#b8d2e5]" /> Conversations do not change long-term memory</p>
          <p className="mt-1 text-[11px] text-[#8492a1]">Save information explicitly below when you want it retained beyond conversation history.</p>
        </div>
        <Link href="/memory" className="secondary-button shrink-0 text-[11px]">Memory tools <ArrowRightIcon className="h-3.5 w-3.5" /></Link>
      </div>
    </div>
  );
}
