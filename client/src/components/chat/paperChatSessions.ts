import { Chat } from "@ai-sdk/react";
import {
    DefaultChatTransport,
    type ChatTransport,
    type UIMessageChunk,
} from "ai";

import { API_BASE_URL, fetchFromApi } from "@/lib/api";
import {
    ChatUIMessage,
    isTruncatedAssistantMessage,
    parseRetryStatus,
    RetryStatus,
} from "@/lib/chatMessages";
import { bumpAgentDocWrites } from "@/lib/paperDocRevision";
import type { ContextMode, ReasoningEffort } from "@/components/chat/chatOptions";

/**
 * Paper-chat state that must outlive `PaperChatPanel`.
 *
 * The side panel unmounts the chat whenever another tab (Annotations, Doc) is
 * shown. `useChat` with its own options would build a fresh `Chat` on every
 * remount — the in-flight stream kept writing into the orphaned instance and
 * the transcript came back empty. Instead each conversation owns one AI SDK
 * `Chat` (plus its paging and per-turn UI state) held in this module, and the
 * panel attaches to it with `useChat({ chat })`, so a stream keeps running and
 * the transcript survives tab switches. Sessions live for the page's lifetime.
 */

/** Dynamic request fields, read by the transport at send time. */
export interface ChatRequestExtras {
    paper_id: string;
    conversation_id: string | null;
    model?: string;
    llm_provider?: string;
    reasoning_effort?: ReasoningEffort;
    context_mode?: ContextMode;
    user_references?: string[];
}

export interface TruncatedTurn {
    messageId: string;
    message: string;
}

export interface PaperChatSessionState {
    /**
     * Server-side auto-retry of a failing provider call, streamed as
     * transient `data-retry-status` parts (delivered to onData only, never
     * persisted).
     */
    retryStatus: RetryStatus | null;
    /**
     * A turn that ended badly WITHOUT the SDK raising: the stream closed on a
     * clean EOF mid-answer, so `status` is "ready" and `error` is undefined.
     * Keyed by the assistant message id so it disappears with that message.
     */
    truncatedTurn: TruncatedTurn | null;
    /** The first history page has been fetched (or failed). */
    historyLoaded: boolean;
    loadingHistory: boolean;
    hasMoreHistory: boolean;
    nextHistoryPage: number;
}

// Server default for /api/conversation/{id}?page_size=10. Used to infer
// "no more pages" when a fetch returns fewer than this.
const HISTORY_PAGE_SIZE = 10;

/**
 * How long a "retrying…" indicator may stand without a follow-up chunk. The
 * server's `recovered` marker is transient and can be dropped under load; the
 * indicator must not outlive the turn when that happens.
 */
const RETRY_STATUS_GRACE_MS = 10_000;

const DOC_WRITE_TOOL = "write_doc";

/**
 * Pass the UI stream through unchanged, bumping the paper's doc revision when
 * a `write_doc` call returns successfully so an open doc editor refetches.
 */
function watchDocWrites(
    stream: ReadableStream<UIMessageChunk>,
    paperId: string
): ReadableStream<UIMessageChunk> {
    const docWriteCalls = new Set<string>();
    return stream.pipeThrough(
        new TransformStream<UIMessageChunk, UIMessageChunk>({
            transform(chunk, controller) {
                if (
                    (chunk.type === "tool-input-start" ||
                        chunk.type === "tool-input-available") &&
                    chunk.toolName === DOC_WRITE_TOOL
                ) {
                    docWriteCalls.add(chunk.toolCallId);
                } else if (
                    chunk.type === "tool-output-available" &&
                    !chunk.preliminary &&
                    docWriteCalls.has(chunk.toolCallId)
                ) {
                    docWriteCalls.delete(chunk.toolCallId);
                    const output = chunk.output;
                    const failed =
                        !!output &&
                        typeof output === "object" &&
                        "error" in output;
                    if (!failed) bumpAgentDocWrites(paperId);
                }
                controller.enqueue(chunk);
            },
        })
    );
}

export class PaperChatSession {
    readonly chat: Chat<ChatUIMessage>;
    requestExtras: ChatRequestExtras;

    private state: PaperChatSessionState = {
        retryStatus: null,
        truncatedTurn: null,
        historyLoaded: false,
        loadingHistory: false,
        hasMoreHistory: true,
        nextHistoryPage: 1,
    };
    private readonly listeners = new Set<() => void>();
    private retryTimer: ReturnType<typeof setTimeout> | null = null;
    private historyRequested = false;
    private historyFetchInFlight = false;

    constructor(
        readonly paperId: string,
        readonly conversationId: string | null
    ) {
        this.requestExtras = { paper_id: paperId, conversation_id: conversationId };

        // The transport sends ONLY the newest user message; server-side
        // history is ground truth. Dynamic body fields (model, context mode,
        // references) are read from `requestExtras` at request time.
        const base = new DefaultChatTransport<ChatUIMessage>({
            api: `${API_BASE_URL}/api/message/chat/paper`,
            credentials: "include",
            prepareSendMessagesRequest: ({ id: chatId, messages, trigger, messageId }) => ({
                body: {
                    trigger,
                    id: chatId,
                    messageId,
                    messages: messages.slice(-1),
                    ...this.requestExtras,
                },
            }),
        });
        const transport: ChatTransport<ChatUIMessage> = {
            sendMessages: async (options) =>
                watchDocWrites(await base.sendMessages(options), paperId),
            reconnectToStream: async (options) => {
                const stream = await base.reconnectToStream(options);
                return stream ? watchDocWrites(stream, paperId) : null;
            },
        };

        this.chat = new Chat<ChatUIMessage>({
            id: conversationId ?? `pending:${paperId}`,
            transport,
            onData: (dataPart) => {
                if (dataPart.type !== "data-retry-status") return;
                const parsed = parseRetryStatus(dataPart.data);
                if (!parsed) return;
                this.setRetryStatus(parsed.state === "retrying" ? parsed : null);
            },
            onFinish: ({ message, isAbort, isDisconnect, isError, finishReason }) => {
                // The retry indicator belongs to one in-flight turn only.
                this.setRetryStatus(null);
                // An abort is the user's own stop button; isError/isDisconnect
                // already set `error`, which the error UI renders. (A server
                // `error` chunk always lands here as isError — the SDK throws
                // on it, so the `finish` chunk that would carry finishReason
                // "error" is never processed.) What's left is the silent case:
                // the stream closed on a clean EOF, with no terminal chunk.
                if (isAbort || isError || isDisconnect) return;
                const truncated =
                    finishReason == null && isTruncatedAssistantMessage(message);
                this.setState({
                    truncatedTurn: truncated
                        ? {
                              messageId: message.id,
                              message:
                                  "Connection lost mid-response — the answer stopped early.",
                          }
                        : null,
                });
            },
        });
    }

    getState = (): PaperChatSessionState => this.state;

    subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener);
        return () => {
            this.listeners.delete(listener);
        };
    };

    private setState(patch: Partial<PaperChatSessionState>) {
        this.state = { ...this.state, ...patch };
        for (const listener of this.listeners) listener();
    }

    private setRetryStatus(retryStatus: RetryStatus | null) {
        if (this.retryTimer) {
            clearTimeout(this.retryTimer);
            this.retryTimer = null;
        }
        // Belt and braces: the "recovered" marker can be dropped server-side,
        // which would otherwise pin "Retrying…" under a happily streaming
        // answer for the rest of the turn. Each new retry chunk restarts this.
        if (retryStatus) {
            this.retryTimer = setTimeout(
                () => this.setRetryStatus(null),
                (retryStatus.delayMs ?? 0) + RETRY_STATUS_GRACE_MS
            );
        }
        this.setState({ retryStatus });
    }

    /** Forget the previous turn's transient notes before sending a new one. */
    clearTurnState() {
        this.setRetryStatus(null);
        this.setState({ truncatedTurn: null });
    }

    /** Load the first history page once per session. */
    ensureHistoryLoaded() {
        if (this.historyRequested || !this.conversationId) return;
        this.historyRequested = true;
        void this.loadHistoryPage();
    }

    /** Prepend the next page of server history. Returns the rows fetched. */
    async loadHistoryPage(): Promise<number> {
        // Two overlapping loads of the same page would dedupe to one set of
        // rows but bump the page counter twice, skipping a page of history.
        if (!this.conversationId || this.historyFetchInFlight) return 0;
        this.historyFetchInFlight = true;
        this.setState({ loadingHistory: true });
        let fetchedCount = 0;
        try {
            const response = await fetchFromApi(
                `/api/conversation/${this.conversationId}?page=${this.state.nextHistoryPage}`,
                { method: "GET" }
            );
            const fetched: ChatUIMessage[] = response.messages || [];
            fetchedCount = fetched.length;
            if (fetched.length < HISTORY_PAGE_SIZE) {
                this.setState({ hasMoreHistory: false });
            }
            if (fetched.length > 0) {
                // Offset pagination drifts when new turns land between page
                // loads — dedupe by id so overlap never duplicates messages.
                const seen = new Set(this.chat.messages.map((m) => m.id));
                this.chat.messages = [
                    ...fetched.filter((m) => !seen.has(m.id)),
                    ...this.chat.messages,
                ];
                this.setState({ nextHistoryPage: this.state.nextHistoryPage + 1 });
            }
        } catch (err) {
            console.error("Error fetching conversation history:", err);
        } finally {
            this.historyFetchInFlight = false;
            this.setState({ loadingHistory: false, historyLoaded: true });
        }
        return fetchedCount;
    }

    dispose() {
        if (this.retryTimer) clearTimeout(this.retryTimer);
        this.retryTimer = null;
        this.listeners.clear();
    }
}

const sessions = new Map<string, PaperChatSession>();
const activeConversationByPaper = new Map<string, string>();

const sessionKey = (paperId: string, conversationId: string | null) =>
    conversationId ?? `pending:${paperId}`;

/**
 * The session for a conversation, created on first use. `null` gives the
 * paper's placeholder session, shown while its conversation is still being
 * resolved — it never sends (the panel requires a conversation id).
 */
export function getPaperChatSession(
    paperId: string,
    conversationId: string | null
): PaperChatSession {
    // Never keep sessions in a server render's module scope.
    if (typeof window === "undefined") {
        return new PaperChatSession(paperId, conversationId);
    }
    const key = sessionKey(paperId, conversationId);
    let session = sessions.get(key);
    if (!session) {
        session = new PaperChatSession(paperId, conversationId);
        sessions.set(key, session);
    }
    return session;
}

/** Drop a deleted conversation's session (stopping any stream it still has). */
export function disposePaperChatSession(conversationId: string) {
    const session = sessions.get(conversationId);
    if (!session) return;
    sessions.delete(conversationId);
    void session.chat.stop();
    session.dispose();
}

/** In-memory mirror of the panel's active conversation, per paper. */
export function rememberActiveConversation(paperId: string, conversationId: string) {
    activeConversationByPaper.set(paperId, conversationId);
}

export function recallActiveConversation(paperId: string): string | null {
    return activeConversationByPaper.get(paperId) ?? null;
}
