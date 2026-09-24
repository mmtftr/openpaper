"use client";

import {
    FormEvent,
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
} from "react";
import { useStickToBottomContext } from "use-stick-to-bottom";
import type { Components } from "react-markdown";
import Link from "next/link";
import { toast } from "sonner";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import {
    AlertTriangleIcon,
    BookOpenIcon,
    BrainIcon,
    CheckIcon,
    ChevronDownIcon,
    CircleStopIcon,
    CornerDownRightIcon,
    CpuIcon,
    LockIcon,
    MessageSquarePlusIcon,
    MessagesSquareIcon,
    RefreshCwIcon,
    Trash2Icon,
} from "lucide-react";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";

import { CreditUsage } from "@/lib/schema";
import { API_BASE_URL, fetchFromApi } from "@/lib/api";
import {
    ChatUIMessage,
    citationsFromMessage,
    interruptedMetadata,
    isCodeCitation,
    isTruncatedAssistantMessage,
    normalizeChatError,
    parseRetryStatus,
    pendingToolLabel,
    renderBlocks,
    retryTargetFromMessages,
    RetryStatus,
    textFromMessage,
} from "@/lib/chatMessages";
import { setPaperChatStreaming } from "@/lib/paperDocEvents";
import { useAuth } from "@/lib/auth";
import {
    useSubscription,
    getChatCreditUsagePercentage,
    isChatCreditAtLimit,
    isChatCreditNearLimit,
} from "@/hooks/useSubscription";
import {
    getAlphaHashToBackgroundColor,
    getInitials,
    cn,
} from "@/lib/utils";

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
    HoverCard,
    HoverCardContent,
    HoverCardTrigger,
} from "@/components/ui/hover-card";
import {
    Collapsible,
    CollapsibleContent,
    CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

import {
    Conversation,
    ConversationContent,
    ConversationEmptyState,
    ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
    Message,
    MessageContent,
} from "@/components/ai-elements/message";
import {
    PromptInput,
    PromptInputBody,
    PromptInputFooter,
    PromptInputMessage,
    PromptInputSubmit,
    PromptInputTextarea,
    PromptInputTools,
} from "@/components/ai-elements/prompt-input";
import {
    Sources,
    SourcesContent,
    SourcesTrigger,
} from "@/components/ai-elements/sources";
import {
    Suggestions,
    Suggestion,
} from "@/components/ai-elements/suggestion";
import { Loader } from "@/components/ai-elements/loader";
import {
    Reasoning,
    ReasoningContent,
    ReasoningTrigger,
} from "@/components/ai-elements/reasoning";

import { ChatHistorySkeleton } from "@/components/ChatHistorySkeleton";
import { ChatMessageActions } from "@/components/ChatMessageActions";
import { AnimatedMarkdown, CopyableTable } from "@/components/AnimatedMarkdown";
import CustomCitationLink from "@/components/utils/CustomCitationLink";
import { codeMarkdownComponents } from "@/components/code/CodeBlock";
import { CodeCitationItem } from "@/components/code/CodeCitationItem";
import { CodeViewerProvider } from "@/components/code/CodeViewerProvider";
import {
    MAX_USER_REFERENCES,
    truncateReference,
} from "@/lib/userReferences";
import { RepoConnectPopover } from "@/components/code/RepoConnectPopover";
import { ToolActivity } from "@/components/chat/ToolActivity";
import { Citation, PaperData } from "@/lib/schema";

interface PaperChatPanelProps {
    id: string;
    paperData: PaperData;
    isMobile: boolean;
    userMessageReferences: string[];
    setUserMessageReferences: React.Dispatch<React.SetStateAction<string[]>>;
    // `paperId` lets the page route citation jumps to the right PDF when a
    // citation refers to a supplementary. Optional for backwards-compat.
    handleCitationClick: (key: string, messageIndex: number, paperId?: string) => void;
    matchesCurrentCitation: (key: string, messageIndex: number) => boolean;
    flashesCurrentCitation?: (key: string, messageIndex: number) => boolean;
    setExplicitSearchTerm: (value: string) => void;
    headerSlot?: React.ReactNode;
}

type ReasoningEffort = "low" | "medium" | "high" | "xhigh";

const REASONING_EFFORT_OPTIONS: { id: ReasoningEffort; label: string }[] = [
    { id: "low", label: "Low" },
    { id: "medium", label: "Medium" },
    { id: "high", label: "High" },
    { id: "xhigh", label: "xhigh" },
];

type ContextMode = "adaptive" | "comprehensive" | "full" | "raw";

interface ContextModeOption {
    id: ContextMode;
    label: string;
    subtitle: string;
    recommended: boolean;
    forParser: "mistral" | "pymupdf";
}

// Each mode is gated by which parser ran on the paper. The picker hides
// modes that don't apply (Raw only on pymupdf-parsed papers; the rest only
// on Mistral-parsed papers).
const CONTEXT_MODE_OPTIONS: ContextModeOption[] = [
    {
        id: "adaptive",
        label: "Adaptive",
        subtitle:
            "Includes abstract, intro and conclusion with model-selected access to the rest",
        recommended: true,
        forParser: "mistral",
    },
    {
        id: "comprehensive",
        label: "Comprehensive",
        subtitle:
            "Includes the main paper content and all the figures, excl. references and appendix",
        recommended: true,
        forParser: "mistral",
    },
    {
        id: "full",
        label: "Full",
        subtitle: "Includes the full paper content (slow, expensive)",
        recommended: false,
        forParser: "mistral",
    },
    {
        id: "raw",
        label: "Raw",
        subtitle:
            "Includes references and appendices, no figures (fallback parsing)",
        recommended: false,
        forParser: "pymupdf",
    },
];

const CONTEXT_MODE_LS_KEY = "openpaper:paper-context-mode";
const SELECTED_MODEL_LS_KEY = "openpaper:paper-chat-model";
const REASONING_EFFORT_LS_KEY = "openpaper:paper-reasoning-effort";
const REASONING_EFFORT_VALUES: ReasoningEffort[] = ["low", "medium", "high", "xhigh"];

interface ConversationSummary {
    id: string;
    title: string | null;
    updated_at: string | null;
}

const conversationStorageKey = (paperId: string) =>
    `openpaper:active-conversation:${paperId}`;

/** Non-default send behaviour — currently only used by the retry path. */
interface SubmitMessageOptions {
    /** Attach these references instead of the ones staged in the composer. */
    references?: string[];
    /** Leave the composer text and staged references untouched. */
    keepComposer?: boolean;
}

/**
 * How long a "retrying…" indicator may stand without a follow-up chunk. The
 * server's `recovered` marker is transient and can be dropped under load; the
 * indicator must not outlive the turn when that happens.
 */
const RETRY_STATUS_GRACE_MS = 10_000;

/**
 * One name for a turn that errored, live or reloaded — the same failure must
 * not read as two different things either side of a refresh. "Stopped" is
 * reserved for turns that were deliberately interrupted.
 */
const FAILED_TURN_TITLE = "Inference failed";

interface ModelOption {
    id: string;
    name: string;
    provider: string;
    supports_reasoning_effort?: boolean;
    supports_vision?: boolean;
}

// The same model id can exist under two providers (e.g. gpt-5.5 on Azure
// AND on the codex proxy), so selection is keyed by provider too.
const modelKey = (m: Pick<ModelOption, "id" | "provider">) =>
    `${m.provider}::${m.id}`;

// Server default for /api/conversation/{id}?page_size=10. Used to infer
// "no more pages" when a fetch returns fewer than this.
const HISTORY_PAGE_SIZE = 10;

const COMPREHENSIVE_OVERVIEW_DISPLAY = "Create a comprehensive overview";
const COMPREHENSIVE_OVERVIEW_PROMPT =
    "Create a comprehensive, thoughtful brief for this paper. Separate each section with clear headings covering: Key Takeaways (the main points in 2-3 bullets), Background (the problem and context), Key Contributions (what's novel about this work), Methods (the approach taken), Results (main findings), Limitations (weaknesses of the study), Open Questions (gaps for future research), and Important Figures/Tables (which visuals to pay attention to). This should serve as a helpful guided reading before I dive into the paper myself.";

const DEFAULT_STARTERS = [
    COMPREHENSIVE_OVERVIEW_DISPLAY,
    "What is the main research question or hypothesis of this paper?",
    "What methodology did the authors use?",
    "What are the key findings and conclusions?",
    "What are the limitations of this study?",
    "How does this paper relate to other work in the field?",
];

export function PaperChatPanel({
    id,
    paperData,
    isMobile,
    userMessageReferences,
    setUserMessageReferences,
    handleCitationClick,
    matchesCurrentCitation,
    flashesCurrentCitation,
    setExplicitSearchTerm,
    headerSlot,
}: PaperChatPanelProps) {
    const { user } = useAuth();
    const { subscription, refetch: refetchSubscription } = useSubscription();

    const [conversationId, setConversationId] = useState<string | null>(null);
    const [conversations, setConversations] = useState<ConversationSummary[]>(
        []
    );
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
    const [currentMessage, setCurrentMessage] = useState("");
    const [hasMoreMessages, setHasMoreMessages] = useState(true);
    const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false);
    const [pageNumberConversationHistory, setPageNumberConversationHistory] =
        useState(1);
    const [isFetchingHistory, setIsFetchingHistory] = useState(true);
    const [pendingStarterQuestion, setPendingStarterQuestion] = useState<
        string | null
    >(null);

    const [creditUsage, setCreditUsage] = useState<CreditUsage | null>(null);
    const [selectedModel, setSelectedModel] = useState<string>(() => {
        if (typeof window === "undefined") return "";
        return window.localStorage.getItem(SELECTED_MODEL_LS_KEY) ?? "";
    });
    useEffect(() => {
        if (typeof window === "undefined" || !selectedModel) return;
        window.localStorage.setItem(SELECTED_MODEL_LS_KEY, selectedModel);
    }, [selectedModel]);
    const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
    const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>(
        () => {
            if (typeof window === "undefined") return "medium";
            const stored = window.localStorage.getItem(
                REASONING_EFFORT_LS_KEY
            );
            return REASONING_EFFORT_VALUES.includes(stored as ReasoningEffort)
                ? (stored as ReasoningEffort)
                : "medium";
        }
    );
    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(REASONING_EFFORT_LS_KEY, reasoningEffort);
    }, [reasoningEffort]);
    const [nextMonday, setNextMonday] = useState(new Date());

    // Context mode for the agentic chat surface. Defaults to Adaptive on
    // Mistral-parsed papers and Raw on pymupdf-parsed papers; user choice
    // sticks via localStorage but is filtered down to the modes valid for
    // the current paper's parser.
    const paperParser: "mistral" | "pymupdf" =
        paperData?.parser === "mistral" ? "mistral" : "pymupdf";
    const availableContextModes = useMemo(
        () => CONTEXT_MODE_OPTIONS.filter((m) => m.forParser === paperParser),
        [paperParser]
    );
    const [contextMode, setContextMode] = useState<ContextMode>(() => {
        if (typeof window === "undefined") return "adaptive";
        const stored = window.localStorage.getItem(CONTEXT_MODE_LS_KEY) as
            | ContextMode
            | null;
        return stored ?? "adaptive";
    });
    useEffect(() => {
        if (
            !availableContextModes.some((m) => m.id === contextMode) &&
            availableContextModes[0]
        ) {
            setContextMode(availableContextModes[0].id);
        }
    }, [availableContextModes, contextMode]);
    useEffect(() => {
        if (typeof window === "undefined") return;
        window.localStorage.setItem(CONTEXT_MODE_LS_KEY, contextMode);
    }, [contextMode]);
    const contextModeLabel = useMemo(() => {
        return (
            CONTEXT_MODE_OPTIONS.find((m) => m.id === contextMode)?.label ??
            "Adaptive"
        );
    }, [contextMode]);
    const contextWarningMessage =
        contextMode === "full"
            ? "This mode sends the entire paper to the model on every turn. Adaptive is faster and cheaper for most questions."
            : contextMode === "raw"
              ? "OCR parsing failed for this paper, so chat is using fallback PDF text without structured sections or figures."
              : null;

    // ---- useChat wiring ------------------------------------------------
    //
    // The transport sends ONLY the newest user message; server-side history
    // is ground truth. Dynamic body fields (model, context mode, references)
    // are read from a ref at request time so the transport can stay stable.
    const requestExtrasRef = useRef<{
        paper_id: string;
        conversation_id: string | null;
        model?: string;
        llm_provider?: string;
        reasoning_effort?: ReasoningEffort;
        context_mode?: ContextMode;
        user_references?: string[];
    }>({ paper_id: id, conversation_id: null });

    const transport = useMemo(
        () =>
            new DefaultChatTransport<ChatUIMessage>({
                api: `${API_BASE_URL}/api/message/chat/paper`,
                credentials: "include",
                prepareSendMessagesRequest: ({ id: chatId, messages, trigger, messageId }) => ({
                    body: {
                        trigger,
                        id: chatId,
                        messageId,
                        messages: messages.slice(-1),
                        ...requestExtrasRef.current,
                    },
                }),
            }),
        []
    );

    const chatId = conversationId ?? "pending";

    // A turn that ended badly WITHOUT the SDK raising: the stream closed on a
    // clean EOF mid-answer, so `status` is "ready" and `error` is undefined.
    // Keyed by the assistant message id so it disappears with that message.
    const [truncatedTurn, setTruncatedTurn] = useState<{
        messageId: string;
        message: string;
    } | null>(null);
    // Server-side auto-retry of a failing provider call, streamed as transient
    // `data-retry-status` parts (delivered to onData only, never persisted).
    const [retryStatus, setRetryStatus] = useState<RetryStatus | null>(null);

    const {
        messages,
        setMessages,
        sendMessage,
        stop,
        status,
        error,
        clearError,
    } = useChat<ChatUIMessage>({
        id: chatId,
        transport,
        onData: (dataPart) => {
            if (dataPart.type !== "data-retry-status") return;
            const parsed = parseRetryStatus(dataPart.data);
            if (!parsed) return;
            setRetryStatus(parsed.state === "retrying" ? parsed : null);
        },
        onFinish: ({ message, isAbort, isDisconnect, isError, finishReason }) => {
            refetchSubscription().catch((err) =>
                console.error("Error refetching subscription:", err)
            );
            // An abort is the user's own stop button; isError/isDisconnect
            // already set `error`, which the error UI renders. (A server
            // `error` chunk always lands here as isError — the SDK throws on
            // it, so the `finish` chunk that would carry finishReason "error"
            // is never processed.) What's left is the silent case: the stream
            // closed on a clean EOF, with no terminal chunk at all.
            if (isAbort || isError || isDisconnect) return;
            const truncated =
                finishReason == null && isTruncatedAssistantMessage(message);
            setTruncatedTurn(
                truncated
                    ? {
                          messageId: message.id,
                          message:
                              "Connection lost mid-response — the answer stopped early.",
                      }
                    : null
            );
        },
    });

    const isStreaming = status === "submitted" || status === "streaming";

    // The retry indicator belongs to one in-flight turn only.
    useEffect(() => {
        if (status === "ready" || status === "error") setRetryStatus(null);
    }, [status]);

    // Belt and braces: the "recovered" marker is a transient chunk and can be
    // dropped server-side, which would otherwise pin "Retrying…" under a
    // happily streaming answer for the rest of the turn. Each new retry chunk
    // is a fresh object, so this timer restarts on every update.
    useEffect(() => {
        if (!retryStatus) return;
        const timer = setTimeout(
            () => setRetryStatus(null),
            (retryStatus.delayMs ?? 0) + RETRY_STATUS_GRACE_MS
        );
        return () => clearTimeout(timer);
    }, [retryStatus]);

    // Broadcast to PaperDocEditor so it can poll for agent-driven writes
    // landing on the user's main doc. Module-level pub-sub keyed by paperId.
    useEffect(() => {
        setPaperChatStreaming(id, isStreaming);
    }, [id, isStreaming]);
    useEffect(() => {
        return () => {
            setPaperChatStreaming(id, false);
        };
    }, [id]);

    const initialLoadStartedRef = useRef(false);
    const activeConversationRef = useRef<string | null>(null);
    activeConversationRef.current = conversationId;

    // Reset the chat surface whenever the conversation rotates. useChat is
    // keyed by conversation id, so its message state resets on its own; the
    // paging state is ours to reset.
    useEffect(() => {
        initialLoadStartedRef.current = false;
        setPageNumberConversationHistory(1);
        setHasMoreMessages(true);
        setIsFetchingHistory(true);
        setTruncatedTurn(null);
        setRetryStatus(null);
    }, [conversationId]);

    // Conversation whose page load is in flight. Two overlapping loads of the
    // same page would dedupe to one set of rows but bump the page counter
    // twice, skipping a page of history. Keyed by conversation so a switch
    // mid-load never blocks the new conversation's first page.
    const pageFetchInFlightRef = useRef<string | null>(null);

    const fetchPage = useCallback(
        async (page: number) => {
            if (!conversationId) return 0;
            if (pageFetchInFlightRef.current === conversationId) return 0;
            pageFetchInFlightRef.current = conversationId;
            try {
                const response = await fetchFromApi(
                    `/api/conversation/${conversationId}?page=${page}`,
                    { method: "GET" }
                );
                // The user may have switched conversations while this
                // request was in flight — useChat's setMessages targets
                // whatever chat is CURRENT, so a stale response would leak
                // another conversation's history into this one.
                if (activeConversationRef.current !== conversationId) return 0;
                const fetched: ChatUIMessage[] = response.messages || [];
                if (fetched.length === 0) {
                    setHasMoreMessages(false);
                    return 0;
                }
                if (fetched.length < HISTORY_PAGE_SIZE) {
                    setHasMoreMessages(false);
                }
                // Offset pagination drifts when new turns land between page
                // loads — dedupe by id so overlap never duplicates messages.
                setMessages((prev) => {
                    const seen = new Set(prev.map((m) => m.id));
                    return [...fetched.filter((m) => !seen.has(m.id)), ...prev];
                });
                setPageNumberConversationHistory((p) => p + 1);
                return fetched.length;
            } finally {
                if (pageFetchInFlightRef.current === conversationId) {
                    pageFetchInFlightRef.current = null;
                }
            }
        },
        [conversationId, setMessages]
    );

    // Gate on readiness only: `paperData` is a fresh object on every status
    // sync, and re-running this while the create POST below is in flight
    // used to mint a second empty conversation.
    const paperReady = Boolean(paperData);
    useEffect(() => {
        if (!paperReady) return;
        let cancelled = false;

        async function init() {
            let list: ConversationSummary[] = [];
            try {
                const response = await fetchFromApi(
                    `/api/paper/conversations?paper_id=${id}`,
                    { method: "GET" }
                );
                if (Array.isArray(response)) list = response;
            } catch (err) {
                console.error("Error fetching conversations:", err);
            }

            if (cancelled) return;
            setConversations(list);

            const remembered =
                typeof window !== "undefined"
                    ? window.localStorage.getItem(
                          conversationStorageKey(id)
                      )
                    : null;
            const fromStorage = list.find((c) => c.id === remembered);
            const fallback = list[0];

            if (fromStorage) {
                setConversationId(fromStorage.id);
                return;
            }
            if (fallback) {
                setConversationId(fallback.id);
                return;
            }

            try {
                const created = await fetchFromApi(
                    `/api/conversation/paper/${id}`,
                    { method: "POST" }
                );
                if (cancelled) return;
                setConversations([
                    {
                        id: created.id,
                        title: created.title ?? null,
                        updated_at: new Date().toISOString(),
                    },
                ]);
                setConversationId(created.id);
            } catch (err) {
                console.error("Error creating initial conversation:", err);
            }
        }

        init();
        return () => {
            cancelled = true;
        };
    }, [paperReady, id]);

    useEffect(() => {
        if (!id || !conversationId) return;
        if (typeof window === "undefined") return;
        window.localStorage.setItem(
            conversationStorageKey(id),
            conversationId
        );
    }, [id, conversationId]);

    const refreshConversations = useCallback(async () => {
        try {
            const response = await fetchFromApi(
                `/api/paper/conversations?paper_id=${id}`,
                { method: "GET" }
            );
            if (Array.isArray(response)) {
                setConversations(response);
            }
        } catch (err) {
            console.error("Error refreshing conversations:", err);
        }
    }, [id]);

    // After the first turn the server auto-generates a title; refetch the
    // list when a stream completes for an untitled conversation.
    const activeConversationTitle = conversations.find(
        (c) => c.id === conversationId
    )?.title;
    useEffect(() => {
        if (status === "ready" && messages.length > 0 && !activeConversationTitle) {
            refreshConversations();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [status]);

    const handleNewChat = useCallback(async () => {
        if (isStreaming) stop();
        try {
            const created = await fetchFromApi(
                `/api/conversation/paper/${id}`,
                { method: "POST" }
            );
            const summary: ConversationSummary = {
                id: created.id,
                title: created.title ?? null,
                updated_at: new Date().toISOString(),
            };
            setConversations((prev) => [summary, ...prev]);
            setConversationId(created.id);
        } catch (err) {
            console.error("Error creating new conversation:", err);
            toast.error("Could not start a new chat.");
        }
    }, [id, isStreaming, stop]);

    const handleSelectConversation = useCallback(
        (next: string) => {
            if (next === conversationId) return;
            if (isStreaming) stop();
            setConversationId(next);
        },
        [conversationId, isStreaming, stop]
    );

    const handleConfirmDelete = useCallback(async () => {
        const target = pendingDeleteId;
        if (!target) return;
        setPendingDeleteId(null);
        try {
            await fetchFromApi(`/api/conversation/${target}`, {
                method: "DELETE",
            });
        } catch (err) {
            console.error("Error deleting conversation:", err);
            toast.error("Could not delete that chat.");
            return;
        }
        const remaining = conversations.filter((c) => c.id !== target);
        setConversations(remaining);
        if (conversationId === target) {
            // The active chat is going away: stop its stream first, or the
            // server keeps generating (and billing) into a deleted
            // conversation — `useChat` does not abort on an id change.
            if (isStreaming) stop();
            const next = remaining[0];
            if (next) {
                setConversationId(next.id);
            } else {
                try {
                    const created = await fetchFromApi(
                        `/api/conversation/paper/${id}`,
                        { method: "POST" }
                    );
                    setConversations([
                        {
                            id: created.id,
                            title: created.title ?? null,
                            updated_at: new Date().toISOString(),
                        },
                    ]);
                    setConversationId(created.id);
                } catch (err) {
                    console.error(
                        "Error creating replacement conversation:",
                        err
                    );
                }
            }
        }
    }, [conversationId, conversations, id, isStreaming, pendingDeleteId, stop]);

    // One-shot initial history load when both user and conversation are ready.
    useEffect(() => {
        if (!user || !conversationId) return;
        if (initialLoadStartedRef.current) return;
        initialLoadStartedRef.current = true;
        setIsLoadingMoreMessages(true);
        fetchPage(1)
            .catch((err) =>
                console.error("Error fetching initial messages:", err)
            )
            .finally(() => {
                // A load for a conversation the user already left must not
                // clear the NEW conversation's loading state.
                if (activeConversationRef.current !== conversationId) return;
                setIsLoadingMoreMessages(false);
                setIsFetchingHistory(false);
            });
    }, [user, conversationId, fetchPage]);

    useEffect(() => {
        if (!id) return;
        async function fetchAvailableModels() {
            try {
                const response = await fetchFromApi(`/api/message/models`);
                const models: ModelOption[] = Array.isArray(response.models)
                    ? response.models
                    : [];
                if (models.length === 0) return;
                setAvailableModels(models);
                const defaultModel = models.find(
                    (m) =>
                        m.id === response.default &&
                        (!response.default_provider ||
                            m.provider === response.default_provider)
                );
                const fallback = modelKey(defaultModel ?? models[0]);
                // Honor the persisted choice if it's still offered; otherwise
                // fall back. This keeps the picker stable across reloads
                // instead of snapping back to the server default each time.
                // (Legacy stored values are bare model ids — upgrade them to
                // the provider-qualified key.)
                setSelectedModel((current) => {
                    if (current && models.some((m) => modelKey(m) === current)) {
                        return current;
                    }
                    const legacy = models.find((m) => m.id === current);
                    return legacy ? modelKey(legacy) : fallback;
                });
            } catch (err) {
                console.error("Error fetching available models:", err);
            }
        }
        fetchAvailableModels();
    }, [id]);

    useEffect(() => {
        const date = new Date();
        date.setDate(date.getDate() + ((1 + 7 - date.getDay()) % 7));
        setNextMonday(date);
    }, []);

    useEffect(() => {
        if (!subscription) {
            setCreditUsage(null);
            return;
        }
        const { chat_credits_used, chat_credits_remaining } =
            subscription.usage;
        const total = chat_credits_used + chat_credits_remaining;
        const usagePercentage = getChatCreditUsagePercentage(subscription);

        const TOAST_KEY = "chat_credit_limit_toast_shown";
        if (
            isChatCreditAtLimit(subscription) &&
            !sessionStorage.getItem(TOAST_KEY)
        ) {
            toast.error(
                "Nice! You've used your chat credits for the week. Upgrade your plan to continue chatting.",
                {
                    duration: 5000,
                    action: {
                        label: "Upgrade",
                        onClick: () => {
                            window.location.href = "/pricing";
                        },
                    },
                }
            );
            sessionStorage.setItem(TOAST_KEY, "true");
        }

        setCreditUsage({
            used: chat_credits_used,
            remaining: chat_credits_remaining,
            total,
            usagePercentage,
            showWarning: isChatCreditNearLimit(subscription),
            isNearLimit: isChatCreditNearLimit(subscription),
            isCritical: isChatCreditNearLimit(subscription, 95),
        });
    }, [subscription]);

    const selectedModelOption = useMemo(
        () => availableModels.find((m) => modelKey(m) === selectedModel),
        [availableModels, selectedModel]
    );
    const supportsReasoningEffort =
        selectedModelOption?.supports_reasoning_effort ?? false;

    // Same provider/model/effort the chat request sends, handed to the code
    // viewer so an inline quick question is answered by the model the user
    // picked here.
    const codeQuestionModel = useMemo(
        () => ({
            model: selectedModelOption?.id ?? null,
            llmProvider: selectedModelOption?.provider ?? null,
            reasoningEffort: supportsReasoningEffort ? reasoningEffort : null,
        }),
        [selectedModelOption, supportsReasoningEffort, reasoningEffort]
    );

    const reasoningEffortLabel = useMemo(() => {
        const opt = REASONING_EFFORT_OPTIONS.find(
            (o) => o.id === reasoningEffort
        );
        return opt?.label ?? "Medium";
    }, [reasoningEffort]);

    const submitMessage = useCallback(
        (textOverride?: string, options?: SubmitMessageOptions) => {
            const text = (textOverride ?? currentMessage).trim();
            if (!text || isStreaming || !conversationId) return false;

            clearError();
            setTruncatedTurn(null);
            setRetryStatus(null);

            // A retry carries the ORIGINAL turn's references; the staged list
            // belongs to whatever the user is composing right now.
            const references = options?.references
                ? [...options.references]
                : [...userMessageReferences];
            requestExtrasRef.current = {
                paper_id: id,
                conversation_id: conversationId,
                context_mode: contextMode,
                ...(selectedModelOption
                    ? {
                          model: selectedModelOption.id,
                          // Disambiguates model ids shared across providers —
                          // the picker knows which provider this id came from.
                          llm_provider: selectedModelOption.provider,
                      }
                    : {}),
                ...(supportsReasoningEffort
                    ? { reasoning_effort: reasoningEffort }
                    : {}),
                ...(references.length ? { user_references: references } : {}),
            };

            // The data-citations part mirrors what the server persists on the
            // user row, so the live message and the reloaded one look alike.
            sendMessage({
                parts: [
                    { type: "text", text },
                    ...(references.length
                        ? [
                              {
                                  type: "data-citations" as const,
                                  data: {
                                      citations: references.map(
                                          (ref, index) => ({
                                              key: `${index + 1}`,
                                              reference: ref,
                                          })
                                      ),
                                  },
                              },
                          ]
                        : []),
                ],
            });

            // A retry re-sends a past turn: the composer and the staged
            // references are the user's current draft and must survive it.
            if (!options?.keepComposer) {
                setCurrentMessage("");
                setUserMessageReferences([]);
            }
            return true;
        },
        [
            currentMessage,
            isStreaming,
            conversationId,
            id,
            userMessageReferences,
            selectedModel,
            selectedModelOption,
            supportsReasoningEffort,
            reasoningEffort,
            contextMode,
            clearError,
            sendMessage,
            setUserMessageReferences,
        ]
    );

    useEffect(() => {
        if (pendingStarterQuestion) {
            submitMessage(pendingStarterQuestion);
            setPendingStarterQuestion(null);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pendingStarterQuestion]);

    // `isStreaming` comes from the last render, so two clicks landing in one
    // frame would both pass that check. This flag flips synchronously.
    const retryInFlightRef = useRef(false);
    useEffect(() => {
        retryInFlightRef.current = false;
    }, [status]);

    /**
     * Re-ask the last question after a failed turn.
     *
     * The failed assistant row and the user row are dropped locally, then the
     * same text AND the same references are sent as a brand new message: the
     * server reuses its dangling user row and clears the partial assistant
     * row, while reusing the old message id here would be rejected as a
     * duplicate. Never `regenerate()` — the endpoint only accepts the
     * `submit-message` trigger.
     */
    const handleRetryLastTurn = useCallback(() => {
        if (retryInFlightRef.current || isStreaming || !conversationId) return;
        const target = retryTargetFromMessages(messages);
        if (!target) return;
        retryInFlightRef.current = true;
        setTruncatedTurn(null);
        setRetryStatus(null);
        setMessages((prev) => prev.slice(0, target.keep));
        const sent = submitMessage(target.text, {
            references: target.references,
            keepComposer: true,
        });
        // Nothing was sent (the guards inside disagreed) — don't leave the
        // button locked until the next status change.
        if (!sent) retryInFlightRef.current = false;
    }, [
        isStreaming,
        conversationId,
        messages,
        setMessages,
        submitMessage,
    ]);

    const handlePromptSubmit = (msg: PromptInputMessage, e: FormEvent) => {
        e.preventDefault();
        submitMessage(msg.text);
    };

    // Code selections from the repo viewer land in the same pending-reference
    // list as PDF text selections (see PdfReader's handleAskAi) and travel out
    // through the same `user_references` field.
    const attachReference = useCallback(
        (raw: string) => {
            const reference = truncateReference(raw);
            if (userMessageReferences.includes(reference)) return;
            if (userMessageReferences.length >= MAX_USER_REFERENCES) {
                toast.error(
                    `You can attach up to ${MAX_USER_REFERENCES} references to a message.`
                );
                return;
            }
            setUserMessageReferences((prev) =>
                prev.includes(reference) ? prev : [...prev, reference]
            );
        },
        [userMessageReferences, setUserMessageReferences]
    );

    const isCreditMaxed = (creditUsage?.usagePercentage ?? 0) >= 100;

    const isGated =
        subscription !== null &&
        subscription !== undefined &&
        subscription.plan !== undefined &&
        subscription.plan !== "researcher";

    const modelLabel = selectedModelOption?.name ?? "Model";

    const modelsByProvider = useMemo(() => {
        const groups = new Map<string, ModelOption[]>();
        for (const m of availableModels) {
            const list = groups.get(m.provider) ?? [];
            list.push(m);
            groups.set(m.provider, list);
        }
        return Array.from(groups.entries());
    }, [availableModels]);

    const providerLabel = (provider: string) =>
        provider.charAt(0).toUpperCase() + provider.slice(1);

    const heightClass = isMobile
        ? "h-[calc(100vh-128px)]"
        : "h-[calc(100vh-64px)]";

    const formatConversationTitle = (
        c: ConversationSummary,
        index: number
    ) => c.title?.trim() || `Chat ${index + 1}`;

    const lastMessage = messages[messages.length - 1];

    // A user message with nothing after it, once the chat has settled, is a
    // turn that never produced an answer (the request died before the stream
    // opened, or the page was reloaded onto a dangling row). Debounced so the
    // ordinary send transition — user row appended a frame before the status
    // flips to "submitted" — can't flash it.
    const [danglingUserTurn, setDanglingUserTurn] = useState(false);
    const lastMessageId = lastMessage?.id;
    const lastMessageRole = lastMessage?.role;
    useEffect(() => {
        if (
            status !== "ready" ||
            isFetchingHistory ||
            lastMessageRole !== "user"
        ) {
            setDanglingUserTurn(false);
            return;
        }
        const timer = setTimeout(() => setDanglingUserTurn(true), 600);
        return () => clearTimeout(timer);
    }, [status, isFetchingHistory, lastMessageRole, lastMessageId]);

    // Error UX, case (b): the turn failed before any assistant content
    // arrived, so the failure belongs to the user's own message — offer
    // retry/edit there, with the server's actual error text.
    const userTurnFailure = useMemo(() => {
        if (isStreaming || lastMessage?.role !== "user") return null;
        const text = textFromMessage(lastMessage);
        if (!text) return null;
        // Carried so "Edit" puts the original references back in the composer
        // instead of silently dropping them.
        const references = citationsFromMessage(lastMessage).map(
            (citation) => citation.reference
        );
        if (error) {
            const { headline, detail } = normalizeChatError(error);
            return { text, references, headline, detail };
        }
        if (danglingUserTurn) {
            return {
                text,
                references,
                headline: "This message never got a response.",
                detail: "",
            };
        }
        return null;
    }, [isStreaming, lastMessage, error, danglingUserTurn]);

    // Error UX, case (a): the note that belongs under an assistant turn —
    // live (`error` / a silently truncated stream) or reloaded from history
    // (`metadata.interrupted`). Retry is offered on the last turn only.
    const noticeForMessage = useCallback(
        (message: ChatUIMessage, isLast: boolean): TurnNotice | null => {
            if (message.role !== "assistant") return null;
            if (isLast && !isStreaming) {
                if (error) {
                    const { headline, detail } = normalizeChatError(error);
                    return {
                        tone: "error",
                        title: FAILED_TURN_TITLE,
                        headline,
                        detail,
                        canRetry: true,
                    };
                }
                if (truncatedTurn?.messageId === message.id) {
                    return {
                        tone: "error",
                        title: FAILED_TURN_TITLE,
                        headline: truncatedTurn.message,
                        detail: "",
                        canRetry: true,
                    };
                }
            }
            const interrupted = interruptedMetadata(message);
            if (!interrupted) return null;
            // No error text means nothing went wrong: the turn was stopped
            // (the user's stop button, a closed tab). The server deliberately
            // KEEPS such a row, so retrying it would append a duplicate
            // question — show a neutral note with no retry instead.
            if (!interrupted.errorText) {
                return {
                    tone: "muted",
                    title: "Stopped",
                    headline: "This answer was stopped before it finished.",
                    detail: "",
                    canRetry: false,
                };
            }
            const { headline, detail } = normalizeChatError(
                interrupted.errorText
            );
            return {
                tone: "error",
                // Same wording as the live failure above: one turn, one name.
                title: FAILED_TURN_TITLE,
                headline,
                detail,
                // Retrying a turn buried in history would rewrite everything
                // after it — out of scope; the failure is still shown.
                canRetry: isLast && !isStreaming,
            };
        },
        [error, isStreaming, truncatedTurn]
    );

    const showThinkingPlaceholder =
        status === "submitted" ||
        (status === "streaming" &&
            (retryStatus !== null ||
                (lastMessage?.role === "assistant" &&
                    renderBlocks(lastMessage).length === 0)));

    return (
        // Everything below can open the repo code viewer (citations, tool
        // chips, the repo-connect popover) through this one provider.
        <CodeViewerProvider
            paperId={id}
            onAttachReference={attachReference}
            chatModel={codeQuestionModel}
        >
        <div className={cn("flex flex-col", heightClass, "min-h-0")}>
            <div className="flex items-center justify-between gap-1 px-2 py-1.5 border-b border-border/40">
                <div className="flex items-center gap-1">
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-7 text-muted-foreground hover:text-foreground"
                        onClick={handleNewChat}
                        aria-label="Start a new chat"
                        title="New chat"
                    >
                        <MessageSquarePlusIcon className="size-4" />
                    </Button>
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <Button
                                type="button"
                                variant="ghost"
                                size="icon"
                                className="size-7 text-muted-foreground hover:text-foreground"
                                aria-label="Switch chat"
                                title="Conversations"
                                disabled={conversations.length === 0}
                            >
                                <MessagesSquareIcon className="size-4" />
                            </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                            align="start"
                            className="w-72 max-h-[60vh] overflow-y-auto"
                        >
                            <DropdownMenuLabel className="text-xs text-muted-foreground">
                                Chats for this paper
                            </DropdownMenuLabel>
                            <DropdownMenuSeparator />
                            {conversations.length === 0 ? (
                                <DropdownMenuItem disabled>
                                    No chats yet
                                </DropdownMenuItem>
                            ) : (
                                conversations.map((c, i) => (
                                    <DropdownMenuItem
                                        key={c.id}
                                        onSelect={(e) => {
                                            e.preventDefault();
                                            handleSelectConversation(c.id);
                                        }}
                                        className="flex items-center gap-2"
                                    >
                                        <span className="flex-1 truncate text-sm">
                                            {formatConversationTitle(c, i)}
                                        </span>
                                        {c.id === conversationId && (
                                            <CheckIcon className="size-3.5 text-green-500 shrink-0" />
                                        )}
                                        <button
                                            type="button"
                                            className="text-muted-foreground/70 hover:text-destructive shrink-0 p-0.5"
                                            aria-label={`Delete ${formatConversationTitle(
                                                c,
                                                i
                                            )}`}
                                            onClick={(e) => {
                                                e.preventDefault();
                                                e.stopPropagation();
                                                setPendingDeleteId(c.id);
                                            }}
                                        >
                                            <Trash2Icon className="size-3.5" />
                                        </button>
                                    </DropdownMenuItem>
                                ))
                            )}
                        </DropdownMenuContent>
                    </DropdownMenu>
                    <RepoConnectPopover paperId={id} />
                </div>
                {headerSlot}
            </div>

            <AlertDialog
                open={!!pendingDeleteId}
                onOpenChange={(open) => {
                    if (!open) setPendingDeleteId(null);
                }}
            >
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete this chat?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This conversation and its messages will be removed.
                            This can&apos;t be undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={handleConfirmDelete}>
                            Delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            <Conversation className="flex-1 min-h-0">
                <ConversationContent className="flex flex-col gap-6 px-3 py-4">
                    {hasMoreMessages &&
                        messages.length > 0 &&
                        !isFetchingHistory &&
                        status === "ready" && (
                            <LoadEarlier
                                isLoading={isLoadingMoreMessages}
                                onLoad={async () => {
                                    setIsLoadingMoreMessages(true);
                                    try {
                                        await fetchPage(
                                            pageNumberConversationHistory
                                        );
                                    } catch (err) {
                                        console.error(
                                            "Error loading earlier messages:",
                                            err
                                        );
                                    } finally {
                                        setIsLoadingMoreMessages(false);
                                    }
                                }}
                            />
                        )}

                    {isFetchingHistory ? (
                        <ChatHistorySkeleton />
                    ) : messages.length === 0 && !isStreaming ? (
                        <ConversationEmptyState
                            title="Start a conversation"
                            description="Ask anything about this paper, or pick one of the suggested prompts to begin."
                        />
                    ) : (
                        messages.map((msg, index) => {
                            const isLast = index === messages.length - 1;
                            const notice = noticeForMessage(msg, isLast);
                            return (
                                <PaperMessage
                                    key={msg.id || index}
                                    message={msg}
                                    index={index}
                                    isStreamingMessage={
                                        isStreaming &&
                                        isLast &&
                                        msg.role === "assistant"
                                    }
                                    user={user}
                                    handleCitationClick={handleCitationClick}
                                    matchesCurrentCitation={matchesCurrentCitation}
                                    flashesCurrentCitation={flashesCurrentCitation}
                                    onJumpToReference={setExplicitSearchTerm}
                                    turnNotice={
                                        notice ? (
                                            <MessageTurnNotice
                                                notice={notice}
                                                onRetry={handleRetryLastTurn}
                                                retryDisabled={isStreaming}
                                            />
                                        ) : undefined
                                    }
                                />
                            );
                        })
                    )}

                    {showThinkingPlaceholder && (
                        <Message from="assistant">
                            <MessageContent>
                                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                    <Loader size={14} />
                                    <span>
                                        {retryStatus
                                            ? `Retrying (attempt ${
                                                  retryStatus.attempt ?? 2
                                              }/${retryStatus.maxAttempts ?? 3})…`
                                            : pendingToolLabel(
                                                  lastMessage?.role ===
                                                      "assistant"
                                                      ? lastMessage
                                                      : undefined
                                              ) ?? "Thinking…"}
                                    </span>
                                </div>
                                {retryStatus?.error && (
                                    <p className="mt-1 text-xs text-muted-foreground/80 break-words">
                                        {retryStatus.error}
                                    </p>
                                )}
                            </MessageContent>
                        </Message>
                    )}

                    {userTurnFailure && (
                        <Message from="assistant">
                            <MessageContent>
                                <div className="text-sm space-y-2">
                                    <div className="flex items-start gap-1.5 text-destructive">
                                        <AlertTriangleIcon className="size-3.5 mt-0.5 shrink-0" />
                                        <span className="break-words">
                                            {userTurnFailure.headline}
                                        </span>
                                    </div>
                                    {userTurnFailure.detail &&
                                        userTurnFailure.detail !==
                                            userTurnFailure.headline && (
                                            <ErrorDetails
                                                detail={userTurnFailure.detail}
                                            />
                                        )}
                                    <div className="flex gap-2">
                                        <Button
                                            variant="default"
                                            size="sm"
                                            disabled={isStreaming}
                                            onClick={handleRetryLastTurn}
                                        >
                                            <RefreshCwIcon className="size-3.5" />
                                            Retry
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => {
                                                setMessages((prev) =>
                                                    prev.slice(0, -1)
                                                );
                                                setCurrentMessage(
                                                    userTurnFailure.text
                                                );
                                                setUserMessageReferences(
                                                    userTurnFailure.references
                                                );
                                                clearError();
                                            }}
                                        >
                                            Edit
                                        </Button>
                                    </div>
                                </div>
                            </MessageContent>
                        </Message>
                    )}
                </ConversationContent>
                <ConversationScrollButton />
            </Conversation>

            <div className="px-3 pb-3 pt-2 space-y-2">
                {messages.length <= 1 && !hasMoreMessages && !isStreaming && (
                    <Suggestions>
                        {DEFAULT_STARTERS.slice(0, 5).map((q, i) => {
                            const sendText =
                                q === COMPREHENSIVE_OVERVIEW_DISPLAY
                                    ? COMPREHENSIVE_OVERVIEW_PROMPT
                                    : q;
                            return (
                                <Suggestion
                                    key={i}
                                    suggestion={sendText}
                                    onClick={(text) => {
                                        setPendingStarterQuestion(text);
                                    }}
                                >
                                    {q}
                                </Suggestion>
                            );
                        })}
                    </Suggestions>
                )}

                {userMessageReferences.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                        {userMessageReferences.map((ref, index) => (
                            <div
                                key={index}
                                className="group flex items-start gap-1 max-w-[260px] rounded-md border border-border/60 bg-muted/40 px-2 py-1"
                            >
                                <button
                                    type="button"
                                    onClick={() => setExplicitSearchTerm(ref)}
                                    className="text-xs text-muted-foreground line-clamp-2 text-left flex-1 hover:text-foreground"
                                >
                                    {ref}
                                </button>
                                <button
                                    type="button"
                                    onClick={() =>
                                        setUserMessageReferences((prev) =>
                                            prev.filter((_, i) => i !== index)
                                        )
                                    }
                                    className="text-muted-foreground/70 hover:text-foreground shrink-0 text-xs leading-none px-0.5"
                                    aria-label="Remove reference"
                                >
                                    ×
                                </button>
                            </div>
                        ))}
                    </div>
                )}

                {contextWarningMessage && (
                    <div className="mb-2 flex items-start gap-2 rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
                        <AlertTriangleIcon className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                        <span>{contextWarningMessage}</span>
                    </div>
                )}

                <PromptInput onSubmit={handlePromptSubmit}>
                    <PromptInputBody>
                        <PromptInputTextarea
                            value={currentMessage}
                            onChange={(e) =>
                                setCurrentMessage(e.currentTarget.value)
                            }
                            placeholder="Ask something about this paper."
                            disabled={isStreaming || isCreditMaxed}
                        />
                    </PromptInputBody>
                    <PromptInputFooter>
                        <PromptInputTools>
                            <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                                        disabled={isStreaming}
                                    >
                                        <CpuIcon className="h-3.5 w-3.5" />
                                        <span className="truncate max-w-[10rem]">
                                            {modelLabel}
                                        </span>
                                    </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent
                                    align="start"
                                    className="w-64 max-h-[60vh] overflow-y-auto"
                                >
                                    {availableModels.length === 0 ? (
                                        <DropdownMenuItem disabled>
                                            No models available
                                        </DropdownMenuItem>
                                    ) : isGated ? (
                                        <>
                                            {modelsByProvider.map(
                                                ([provider, items], gi) => (
                                                    <div key={provider}>
                                                        {gi > 0 && (
                                                            <DropdownMenuSeparator />
                                                        )}
                                                        <DropdownMenuLabel className="text-xs text-muted-foreground">
                                                            {providerLabel(
                                                                provider
                                                            )}
                                                        </DropdownMenuLabel>
                                                        {items.map((m) => (
                                                            <DropdownMenuItem
                                                                key={modelKey(m)}
                                                                onClick={() => {
                                                                    window.location.href =
                                                                        "/pricing";
                                                                }}
                                                                className="flex items-center justify-between text-muted-foreground"
                                                            >
                                                                <span className="truncate">
                                                                    {m.name}
                                                                </span>
                                                                <LockIcon className="h-3 w-3 shrink-0" />
                                                            </DropdownMenuItem>
                                                        ))}
                                                    </div>
                                                )
                                            )}
                                            <DropdownMenuSeparator />
                                            <DropdownMenuItem
                                                onClick={() => {
                                                    window.location.href =
                                                        "/pricing";
                                                }}
                                                className="flex items-center justify-center bg-primary text-primary-foreground focus:bg-primary/90 font-medium"
                                            >
                                                Upgrade to select models
                                            </DropdownMenuItem>
                                        </>
                                    ) : (
                                        modelsByProvider.map(
                                            ([provider, items], gi) => (
                                                <div key={provider}>
                                                    {gi > 0 && (
                                                        <DropdownMenuSeparator />
                                                    )}
                                                    <DropdownMenuLabel className="text-xs text-muted-foreground">
                                                        {providerLabel(
                                                            provider
                                                        )}
                                                    </DropdownMenuLabel>
                                                    {items.map((m) => (
                                                        <DropdownMenuItem
                                                            key={modelKey(m)}
                                                            onClick={() =>
                                                                setSelectedModel(
                                                                    modelKey(m)
                                                                )
                                                            }
                                                            className="flex items-center justify-between"
                                                        >
                                                            <span className="truncate">
                                                                {m.name}
                                                            </span>
                                                            {modelKey(m) ===
                                                                selectedModel && (
                                                                <CheckIcon className="h-3.5 w-3.5 text-green-500 shrink-0" />
                                                            )}
                                                        </DropdownMenuItem>
                                                    ))}
                                                </div>
                                            )
                                        )
                                    )}
                                </DropdownMenuContent>
                            </DropdownMenu>

                            {supportsReasoningEffort && (
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                                            disabled={isStreaming}
                                            aria-label={`Reasoning effort: ${reasoningEffortLabel}`}
                                        >
                                            <BrainIcon className="h-3.5 w-3.5" />
                                            <span className="truncate max-w-[7rem]">
                                                {reasoningEffortLabel}
                                            </span>
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent
                                        align="start"
                                        className="w-44"
                                    >
                                        <DropdownMenuLabel className="text-xs text-muted-foreground">
                                            Reasoning effort
                                        </DropdownMenuLabel>
                                        <DropdownMenuSeparator />
                                        {REASONING_EFFORT_OPTIONS.map((opt) => (
                                            <DropdownMenuItem
                                                key={opt.id}
                                                onClick={() =>
                                                    setReasoningEffort(opt.id)
                                                }
                                                className="flex items-center justify-between"
                                            >
                                                <span>{opt.label}</span>
                                                {opt.id === reasoningEffort && (
                                                    <CheckIcon className="h-3.5 w-3.5 text-green-500 shrink-0" />
                                                )}
                                            </DropdownMenuItem>
                                        ))}
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            )}

                            {availableContextModes.length > 1 && (
                                <DropdownMenu>
                                    <DropdownMenuTrigger asChild>
                                        <Button
                                            type="button"
                                            variant="ghost"
                                            size="sm"
                                            className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground"
                                            disabled={isStreaming}
                                            aria-label={`Context: ${contextModeLabel}`}
                                        >
                                            <BookOpenIcon className="h-3.5 w-3.5" />
                                            <span className="truncate max-w-[8rem]">
                                                {contextModeLabel}
                                            </span>
                                        </Button>
                                    </DropdownMenuTrigger>
                                    <DropdownMenuContent
                                        align="start"
                                        className="w-80"
                                    >
                                        <DropdownMenuLabel className="text-xs text-muted-foreground">
                                            Paper context
                                        </DropdownMenuLabel>
                                        <DropdownMenuSeparator />
                                        {availableContextModes.map((opt) => (
                                            <DropdownMenuItem
                                                key={opt.id}
                                                onClick={() =>
                                                    setContextMode(opt.id)
                                                }
                                                className="flex items-start gap-2 py-2"
                                            >
                                                <div className="flex flex-col flex-1 min-w-0">
                                                    <div className="flex items-center gap-1.5">
                                                        <span className="font-medium">
                                                            {opt.label}
                                                        </span>
                                                        {!opt.recommended && (
                                                            <AlertTriangleIcon className="h-3 w-3 text-amber-500" />
                                                        )}
                                                    </div>
                                                    <span className="text-xs text-muted-foreground">
                                                        {opt.subtitle}
                                                    </span>
                                                </div>
                                                {opt.id === contextMode && (
                                                    <CheckIcon className="h-3.5 w-3.5 text-green-500 shrink-0 mt-1" />
                                                )}
                                            </DropdownMenuItem>
                                        ))}
                                    </DropdownMenuContent>
                                </DropdownMenu>
                            )}
                        </PromptInputTools>
                        <PromptInputSubmit
                            status={
                                isStreaming
                                    ? "streaming"
                                    : ("ready" as const)
                            }
                            type={isStreaming ? "button" : "submit"}
                            aria-label={
                                isStreaming
                                    ? "Stop generating"
                                    : "Send message"
                            }
                            onClick={
                                isStreaming
                                    ? (e) => {
                                          e.preventDefault();
                                          stop();
                                      }
                                    : undefined
                            }
                            disabled={
                                isStreaming
                                    ? false
                                    : !currentMessage.trim() ||
                                      isCreditMaxed ||
                                      !conversationId
                            }
                        />
                    </PromptInputFooter>
                </PromptInput>

                {creditUsage && creditUsage.showWarning && (
                    <div
                        className={cn(
                            "text-xs px-1 flex justify-between",
                            creditUsage.isCritical
                                ? "text-red-600 dark:text-red-400"
                                : "text-amber-600 dark:text-amber-400"
                        )}
                    >
                        <span className="font-semibold">
                            {creditUsage.used} credits used
                        </span>
                        <div>
                            <HoverCard>
                                <HoverCardTrigger asChild>
                                    <span className="font-semibold cursor-help">
                                        {creditUsage.remaining} credits
                                        remaining
                                    </span>
                                </HoverCardTrigger>
                                <HoverCardContent
                                    side="top"
                                    className="w-48"
                                >
                                    <p className="text-sm">
                                        Resets on{" "}
                                        {nextMonday.toLocaleDateString()}
                                    </p>
                                </HoverCardContent>
                            </HoverCard>
                            <Link
                                href="/pricing"
                                className="ml-1 text-blue-500 hover:text-blue-700"
                            >
                                Upgrade
                            </Link>
                        </div>
                    </div>
                )}
            </div>
        </div>
        </CodeViewerProvider>
    );
}

interface LoadEarlierProps {
    isLoading: boolean;
    onLoad: () => Promise<void>;
}

// Lives inside <Conversation> so it can read the StickToBottom scroll
// container ref and preserve viewport position when older messages are
// prepended.
function LoadEarlier({ isLoading, onLoad }: LoadEarlierProps) {
    const { scrollRef } = useStickToBottomContext();

    const handleClick = async () => {
        const el = scrollRef.current;
        const beforeHeight = el?.scrollHeight ?? 0;
        const beforeTop = el?.scrollTop ?? 0;
        await onLoad();
        requestAnimationFrame(() => {
            const node = scrollRef.current;
            if (!node) return;
            const delta = node.scrollHeight - beforeHeight;
            node.scrollTop = beforeTop + delta;
        });
    };

    return (
        <div className="text-center">
            {isLoading ? (
                <span className="text-xs text-muted-foreground">
                    Loading earlier messages…
                </span>
            ) : (
                <button
                    type="button"
                    className="text-xs text-muted-foreground hover:text-foreground"
                    onClick={handleClick}
                >
                    Load earlier messages
                </button>
            )}
        </div>
    );
}

/**
 * The note under an assistant turn that didn't complete normally.
 *
 * `tone` separates the two very different cases: "error" is a failure the
 * user did not ask for (destructive styling, retry offered), "muted" is a
 * deliberate stop (neutral styling, never retried — the server keeps the
 * stopped row, so re-sending would duplicate the question).
 */
interface TurnNotice {
    tone: "error" | "muted";
    /** Short label for what happened. */
    title: string;
    /** One-line, human-readable cause. */
    headline: string;
    /** Full raw error text for the disclosure; empty when there is no more. */
    detail: string;
    canRetry: boolean;
}

interface ErrorDetailsProps {
    detail: string;
}

/** Collapsed dump of the raw error, styled like the tool-failure output. */
function ErrorDetails({ detail }: ErrorDetailsProps) {
    const [open, setOpen] = useState(false);
    return (
        <Collapsible open={open} onOpenChange={setOpen}>
            <CollapsibleTrigger asChild>
                <button
                    type="button"
                    className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors"
                >
                    <span>Details</span>
                    <ChevronDownIcon
                        className={cn(
                            "size-3 shrink-0 transition-transform",
                            open && "rotate-180"
                        )}
                    />
                </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
                <pre className="mt-1 whitespace-pre-wrap break-words max-h-40 overflow-y-auto rounded bg-destructive/10 p-1.5 text-[11px] font-mono text-muted-foreground">
                    {detail}
                </pre>
            </CollapsibleContent>
        </Collapsible>
    );
}

interface MessageTurnNoticeProps {
    notice: TurnNotice;
    onRetry: () => void;
    retryDisabled?: boolean;
}

/**
 * The note attached to an assistant turn that didn't finish — live (the
 * stream errored or died) or reloaded (`metadata.interrupted`). A failure
 * shows the server's own error text with the untrimmed version one click
 * away and a retry on the last turn; a deliberate stop is a quiet line.
 */
function MessageTurnNotice({
    notice,
    onRetry,
    retryDisabled,
}: MessageTurnNoticeProps) {
    const isError = notice.tone === "error";
    return (
        <div
            className={cn(
                "mt-2 rounded-md border px-2.5 py-2 space-y-1.5",
                isError
                    ? "border-destructive/40 bg-destructive/5"
                    : "border-border/60 bg-muted/40"
            )}
        >
            <div
                className={cn(
                    "flex items-start gap-1.5",
                    isError ? "text-destructive" : "text-muted-foreground"
                )}
            >
                {isError ? (
                    <AlertTriangleIcon className="size-3.5 mt-0.5 shrink-0" />
                ) : (
                    <CircleStopIcon className="size-3.5 mt-0.5 shrink-0" />
                )}
                <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium">{notice.title}</p>
                    <p className="text-xs break-words">{notice.headline}</p>
                </div>
            </div>
            {notice.detail && notice.detail !== notice.headline && (
                <ErrorDetails detail={notice.detail} />
            )}
            {notice.canRetry && (
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 gap-1.5 text-xs"
                    disabled={retryDisabled}
                    onClick={onRetry}
                >
                    <RefreshCwIcon className="size-3.5" />
                    Retry
                </Button>
            )}
        </div>
    );
}

function citationComponents(
    handleCitationClick: (key: string, messageIndex: number) => void,
    messageIndex: number,
    citations: Citation[]
): Components {
    // CustomCitationLink calls handleCitationClick(key, messageIndex) without a
    // paper_id (it's a shared component used in non-chat surfaces too); the
    // caller's handler resolves the citation — including whether it's a code
    // citation, which routes to the code viewer instead of the PDF.
    const inject = (props: object) => (
        <CustomCitationLink
            {...(props as Record<string, unknown>)}
            handleCitationClick={handleCitationClick}
            messageIndex={messageIndex}
            citations={citations}
        />
    );
    return {
        p: inject,
        li: inject,
        div: inject,
        td: inject,
        table: CopyableTable,
        ...codeMarkdownComponents,
    } as Components;
}

interface PaperMessageProps {
    message: ChatUIMessage;
    index: number;
    isStreamingMessage: boolean;
    user: ReturnType<typeof useAuth>["user"];
    handleCitationClick: (key: string, messageIndex: number, paperId?: string) => void;
    matchesCurrentCitation: (key: string, messageIndex: number) => boolean;
    flashesCurrentCitation?: (key: string, messageIndex: number) => boolean;
    onJumpToReference: (text: string) => void;
    /** Failure/stopped note for this turn, rendered under the assistant bubble. */
    turnNotice?: React.ReactNode;
}

function PaperMessage({
    message,
    index,
    isStreamingMessage,
    user,
    handleCitationClick,
    matchesCurrentCitation,
    flashesCurrentCitation,
    onJumpToReference,
    turnNotice,
}: PaperMessageProps) {
    const citations = useMemo(
        () => citationsFromMessage(message),
        [message]
    );
    const isUser = message.role === "user";
    const blocks = useMemo(() => renderBlocks(message), [message]);

    const [sourcesOpen, setSourcesOpen] = useState(false);
    const hasFlash = flashesCurrentCitation
        ? citations.some((c) => flashesCurrentCitation(c.key, index))
        : false;
    // Auto-open when a citation we own is flashing (couldn't be located in the
    // PDF). Sticky-open after the flash clears so the user can close manually.
    useEffect(() => {
        if (hasFlash) setSourcesOpen(true);
    }, [hasFlash]);

    // Code citation whose snippet the sources list should scroll to, and the
    // key currently emphasized after that jump.
    const [pendingCodeScroll, setPendingCodeScroll] = useState<string | null>(
        null
    );
    const [focusedCodeKey, setFocusedCodeKey] = useState<string | null>(null);

    // One entry point for every citation click (inline marker or sources row).
    // Code citations jump to their inline snippet in the sources list — the
    // same open-sources-and-scroll-to-`citation-{key}-{index}` move PDF
    // citations make, minus the document search, which has nothing to find.
    const onCitationClick = useCallback(
        (key: string, msgIdx: number) => {
            const citation = citations.find((c) => String(c.key) === key);
            if (citation && isCodeCitation(citation)) {
                if (msgIdx === index) {
                    setSourcesOpen(true);
                    setPendingCodeScroll(key);
                    setFocusedCodeKey(key);
                }
                return;
            }
            handleCitationClick(key, msgIdx, citation?.paper_id);
            if (msgIdx === index) setSourcesOpen(true);
        },
        [citations, handleCitationClick, index]
    );

    // Scroll after the sources disclosure has mounted its content.
    useEffect(() => {
        if (!pendingCodeScroll || !sourcesOpen) return;
        const frame = requestAnimationFrame(() => {
            document
                .getElementById(`citation-${pendingCodeScroll}-${index}`)
                ?.scrollIntoView({ behavior: "smooth", block: "center" });
            setPendingCodeScroll(null);
        });
        return () => cancelAnimationFrame(frame);
    }, [pendingCodeScroll, sourcesOpen, index]);

    // Emphasis is a flash, not a mode.
    useEffect(() => {
        if (!focusedCodeKey) return;
        const timer = setTimeout(() => setFocusedCodeKey(null), 2500);
        return () => clearTimeout(timer);
    }, [focusedCodeKey]);

    const markdownComponents = useMemo(
        () => citationComponents(onCitationClick, index, citations),
        [onCitationClick, index, citations]
    );

    if (isUser) {
        return (
            <div data-message-index={index}>
                <Message from="user">
                    {user && (
                        <Avatar className="size-6 shrink-0 ring-1 ring-border">
                            <AvatarImage
                                src={user.picture}
                                alt={user.name || user.email}
                            />
                            <AvatarFallback
                                className={cn(
                                    "text-[10px]",
                                    getAlphaHashToBackgroundColor(
                                        user.name || user.email
                                    )
                                )}
                            >
                                {getInitials(user.name || user.email)}
                            </AvatarFallback>
                        </Avatar>
                    )}
                    {citations.length > 0 && (
                        <div className="ml-auto flex flex-col items-end gap-1">
                            {citations.map((c, i) => (
                                <button
                                    key={`${c.key}-${i}`}
                                    type="button"
                                    onClick={() =>
                                        onJumpToReference(c.reference)
                                    }
                                    className="group flex items-start gap-1 max-w-[260px] rounded-md border-l-2 border-primary/60 bg-muted/40 px-2 py-1 text-left hover:bg-muted/70 transition-colors"
                                    title="Jump to this section in the PDF"
                                >
                                    <CornerDownRightIcon className="size-3 mt-0.5 shrink-0 text-muted-foreground" />
                                    <span className="text-xs text-muted-foreground line-clamp-2 leading-snug group-hover:text-foreground">
                                        {c.reference}
                                    </span>
                                </button>
                            ))}
                        </div>
                    )}
                    <MessageContent>
                        <div className="prose dark:prose-invert prose-sm !max-w-none whitespace-pre-wrap">
                            {textFromMessage(message)}
                        </div>
                    </MessageContent>
                </Message>
            </div>
        );
    }

    // A turn that ended before producing anything renders as the note alone —
    // an empty assistant bubble above it is just noise.
    if (blocks.length === 0 && turnNotice) {
        return <div data-message-index={index}>{turnNotice}</div>;
    }

    return (
        <div data-message-index={index}>
            <Message from="assistant">
                <MessageContent className={isStreamingMessage ? "!text-foreground" : undefined}>
                    {blocks.map((block, blockIndex) => {
                        if (block.kind === "reasoning") {
                            return (
                                <Reasoning
                                    key={`reasoning-${blockIndex}`}
                                    isStreaming={
                                        isStreamingMessage && block.streaming
                                    }
                                    defaultOpen={false}
                                >
                                    <ReasoningTrigger />
                                    <ReasoningContent>
                                        {block.text}
                                    </ReasoningContent>
                                </Reasoning>
                            );
                        }
                        if (block.kind === "tool") {
                            return (
                                <ToolActivity
                                    key={`tool-${block.part.toolCallId ?? blockIndex}`}
                                    part={block.part}
                                />
                            );
                        }
                        // Text: animated for the live message, static markdown
                        // for history.
                        return isStreamingMessage ? (
                            <AnimatedMarkdown
                                key={`text-${blockIndex}`}
                                content={block.text}
                                className="prose-sm"
                                components={markdownComponents}
                            />
                        ) : (
                            <div
                                key={`text-${blockIndex}`}
                                className="prose dark:prose-invert prose-sm !max-w-none"
                            >
                                <Markdown
                                    remarkPlugins={[
                                        [
                                            remarkMath,
                                            { singleDollarTextMath: false },
                                        ],
                                        remarkGfm,
                                    ]}
                                    rehypePlugins={[rehypeKatex]}
                                    components={markdownComponents}
                                >
                                    {block.text}
                                </Markdown>
                            </div>
                        );
                    })}
                </MessageContent>
            </Message>
            {turnNotice}
            {citations.length > 0 && (
                <div className="mt-2 ml-0">
                    <PaperSources
                        citations={citations}
                        messageIndex={index}
                        focusedCodeKey={focusedCodeKey}
                        handleCitationClick={onCitationClick}
                        matchesCurrentCitation={matchesCurrentCitation}
                        flashesCurrentCitation={flashesCurrentCitation}
                        open={sourcesOpen}
                        onOpenChange={setSourcesOpen}
                        rightSlot={
                            <ChatMessageActions
                                message={blocks
                                    .filter((b) => b.kind === "text")
                                    .map((b) => (b.kind === "text" ? b.text : ""))
                                    .join("\n\n")}
                                references={{ citations }}
                            />
                        }
                    />
                </div>
            )}
        </div>
    );
}

interface PaperSourcesProps {
    citations: Citation[];
    messageIndex: number;
    /** Code citation to emphasize after a jump from its inline marker. */
    focusedCodeKey?: string | null;
    handleCitationClick: (key: string, messageIndex: number) => void;
    matchesCurrentCitation: (key: string, messageIndex: number) => boolean;
    flashesCurrentCitation?: (key: string, messageIndex: number) => boolean;
    rightSlot?: React.ReactNode;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

function PaperSources({
    citations,
    messageIndex,
    focusedCodeKey,
    handleCitationClick,
    matchesCurrentCitation,
    flashesCurrentCitation,
    rightSlot,
    open,
    onOpenChange,
}: PaperSourcesProps) {
    return (
        <Sources open={open} onOpenChange={onOpenChange}>
            <div className="flex items-center justify-between gap-2">
                <SourcesTrigger
                    count={citations.length}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                />
                {rightSlot}
            </div>
            <SourcesContent className="!w-full !mt-1.5 !gap-0">
                <ul className="list-none p-0 m-0">
                    {citations.map((citation, refIndex) => {
                        // Code citations point at the repo snapshot, not the
                        // PDF — they get their own row and their own click
                        // target (the code viewer).
                        if (isCodeCitation(citation)) {
                            return (
                                <CodeCitationItem
                                    key={`${citation.key}-${refIndex}`}
                                    citation={citation}
                                    messageIndex={messageIndex}
                                    focused={
                                        focusedCodeKey === String(citation.key)
                                    }
                                />
                            );
                        }
                        const active = matchesCurrentCitation(
                            citation.key,
                            messageIndex
                        );
                        const flashed = flashesCurrentCitation
                            ? flashesCurrentCitation(
                                  citation.key,
                                  messageIndex
                              )
                            : false;
                        return (
                            <li
                                key={`${citation.key}-${refIndex}`}
                                id={`citation-${citation.key}-${messageIndex}`}
                                role="button"
                                tabIndex={0}
                                aria-label={`Jump to citation ${citation.key}`}
                                onClick={() =>
                                    handleCitationClick(
                                        citation.key,
                                        messageIndex
                                    )
                                }
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        handleCitationClick(
                                            citation.key,
                                            messageIndex
                                        );
                                    }
                                }}
                                className={cn(
                                    "flex gap-1.5 items-baseline rounded px-1.5 py-0.5 cursor-pointer transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                    flashed &&
                                        "bg-yellow-200/80 dark:bg-yellow-500/30 animate-pulse",
                                    !flashed &&
                                        active &&
                                        "bg-blue-100 dark:bg-blue-900/40"
                                )}
                            >
                                <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                                    [{citation.key}]
                                </span>
                                <span
                                    id={`citation-ref-${citation.key}-${messageIndex}`}
                                    className="text-xs text-muted-foreground line-clamp-2 leading-snug citation-ref-md"
                                >
                                    <Markdown
                                        remarkPlugins={[
                                            remarkGfm,
                                            [
                                                remarkMath,
                                                { singleDollarTextMath: false },
                                            ],
                                        ]}
                                        rehypePlugins={[rehypeKatex]}
                                        components={{
                                            // Citations are short blurbs;
                                            // strip block-level styling so
                                            // they sit inline with the [N]
                                            // marker and the line-clamp.
                                            p: ({ children }) => <>{children}</>,
                                            h1: ({ children }) => <strong>{children}</strong>,
                                            h2: ({ children }) => <strong>{children}</strong>,
                                            h3: ({ children }) => <strong>{children}</strong>,
                                            h4: ({ children }) => <strong>{children}</strong>,
                                            h5: ({ children }) => <strong>{children}</strong>,
                                            h6: ({ children }) => <strong>{children}</strong>,
                                            code: ({ children }) => (
                                                <code className="font-mono text-[11px]">
                                                    {children}
                                                </code>
                                            ),
                                        }}
                                    >
                                        {citation.reference}
                                    </Markdown>
                                </span>
                            </li>
                        );
                    })}
                </ul>
            </SourcesContent>
        </Sources>
    );
}
