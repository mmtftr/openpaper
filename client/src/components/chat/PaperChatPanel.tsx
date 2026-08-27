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
    CornerDownRightIcon,
    CpuIcon,
    LockIcon,
    MessageSquarePlusIcon,
    MessagesSquareIcon,
    Trash2Icon,
} from "lucide-react";

import { ChatMessage, CreditUsage, Reference } from "@/lib/schema";
import { fetchFromApi, fetchStreamFromApi } from "@/lib/api";
import {
    readOpenPaperUIMessageStream,
    stripEvidenceBlock,
} from "@/lib/uiMessageStream";
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

interface ChatRequestBody {
    user_query: string;
    conversation_id: string | null;
    paper_id: string;
    user_references: string[];
    model?: string;
    llm_provider?: string;
    reasoning_effort?: ReasoningEffort;
    context_mode?: ContextMode;
}

interface ConversationSummary {
    id: string;
    title: string | null;
    updated_at: string | null;
}

const conversationStorageKey = (paperId: string) =>
    `openpaper:active-conversation:${paperId}`;

interface ModelOption {
    id: string;
    name: string;
    provider: string;
}

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
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [currentMessage, setCurrentMessage] = useState("");
    const [hasMoreMessages, setHasMoreMessages] = useState(true);
    const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false);
    const [pageNumberConversationHistory, setPageNumberConversationHistory] =
        useState(1);
    const [isFetchingHistory, setIsFetchingHistory] = useState(true);

    const [isStreaming, setIsStreaming] = useState(false);

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

    const [streamingText, setStreamingText] = useState("");
    const [streamingReasoning, setStreamingReasoning] = useState("");
    const [streamingReferences, setStreamingReferences] = useState<
        Reference | undefined
    >(undefined);
    const [streamingSourcesOpen, setStreamingSourcesOpen] = useState(false);
    // Last status event received from the agentic loop ("Searching for X",
    // "Reading section Y"). Cleared once the answer starts streaming.
    const [streamingStatus, setStreamingStatus] = useState<string | null>(null);
    const [errorState, setErrorState] = useState<{
        failedUserMessage: string;
    } | null>(null);
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
        // If the persisted choice doesn't match this paper's parser, fall
        // back to the first valid mode for this parser.
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

    const starterQuestions = useMemo(() => {
        if (
            paperData?.starter_questions &&
            paperData.starter_questions.length > 0
        ) {
            return paperData.starter_questions;
        }
        return DEFAULT_STARTERS;
    }, [paperData?.starter_questions]);

    const transformReferencesToFormat = useCallback((references: string[]) => {
        const citations = references.map((ref, index) => ({
            key: `${index + 1}`,
            reference: ref,
        }));
        return { citations };
    }, []);

    const initialLoadStartedRef = useRef(false);
    const abortControllerRef = useRef<AbortController | null>(null);

    // Reset the chat surface whenever the conversation rotates (e.g., switching
    // papers). The auto-fetch effect re-arms once initialLoadStartedRef flips
    // back to false.
    useEffect(() => {
        initialLoadStartedRef.current = false;
        setMessages([]);
        setPageNumberConversationHistory(1);
        setHasMoreMessages(true);
        setIsFetchingHistory(true);
    }, [conversationId]);

    const fetchPage = useCallback(
        async (page: number) => {
            if (!conversationId) return 0;
            const response = await fetchFromApi(
                `/api/conversation/${conversationId}?page=${page}`,
                { method: "GET" }
            );
            const fetched = (response.messages || []).map(
                (msg: ChatMessage) => ({
                    role: msg.role,
                    content: msg.content,
                    id: msg.id,
                    references: msg.references || {},
                })
            );
            if (fetched.length === 0) {
                setHasMoreMessages(false);
                return 0;
            }
            if (fetched.length < HISTORY_PAGE_SIZE) {
                setHasMoreMessages(false);
            }
            setMessages((prev) => [...fetched, ...prev]);
            setPageNumberConversationHistory((p) => p + 1);
            return fetched.length;
        },
        [conversationId]
    );

    useEffect(() => {
        if (!paperData) return;
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
    }, [paperData, id]);

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

    const handleNewChat = useCallback(async () => {
        if (isStreaming) {
            abortControllerRef.current?.abort();
        }
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
    }, [id, isStreaming]);

    const handleSelectConversation = useCallback(
        (next: string) => {
            if (next === conversationId) return;
            if (isStreaming) abortControllerRef.current?.abort();
            setConversationId(next);
        },
        [conversationId, isStreaming]
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
    }, [conversationId, conversations, id, pendingDeleteId]);

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
                const defaultId: string | undefined = response.default;
                const fallback =
                    defaultId &&
                    models.some((m: ModelOption) => m.id === defaultId)
                        ? defaultId
                        : models[0].id;
                // Honor the persisted choice if it's still offered; otherwise
                // fall back. This keeps the picker stable across reloads
                // instead of snapping back to the server default each time.
                setSelectedModel((current) =>
                    current && models.some((m) => m.id === current)
                        ? current
                        : fallback
                );
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

    const reasoningEffortLabel = useMemo(() => {
        const opt = REASONING_EFFORT_OPTIONS.find(
            (o) => o.id === reasoningEffort
        );
        return opt?.label ?? "Medium";
    }, [reasoningEffort]);

    const submitMessage = useCallback(
        async (textOverride?: string) => {
            const text = (textOverride ?? currentMessage).trim();
            if (!text || isStreaming) return;
            if (!conversationId) {
                // Conversation not provisioned yet — surface a soft error
                // rather than firing a request the server will reject.
                setErrorState({ failedUserMessage: text });
                return;
            }

            setErrorState(null);

            const userMessage: ChatMessage = {
                role: "user",
                content: text,
                references: transformReferencesToFormat(userMessageReferences),
            };
            setMessages((prev) => [...prev, userMessage]);

            const failedUserMessage = text;
            setCurrentMessage("");
            setUserMessageReferences([]);

            setIsStreaming(true);
            setStreamingText("");
            setStreamingReasoning("");
            setStreamingReferences(undefined);
            setStreamingSourcesOpen(false);

            const requestBody: ChatRequestBody = {
                user_query: text,
                conversation_id: conversationId,
                paper_id: id,
                user_references: userMessageReferences,
            };
            if (selectedModel) {
                requestBody.model = selectedModel;
                // Disambiguates model ids shared across providers (e.g. the
                // same gpt-5.5 id under Azure vs. a same-family proxy
                // provider) - the picker already knows which provider this
                // id came from.
                const provider = availableModels.find(
                    (m) => m.id === selectedModel
                )?.provider;
                if (provider) requestBody.llm_provider = provider;
            }
            requestBody.reasoning_effort = reasoningEffort;
            requestBody.context_mode = contextMode;

            const controller = new AbortController();
            abortControllerRef.current = controller;
            let accumulated = "";
            let reasoning = "";
            let references: Reference | undefined;

            try {
                const stream = await fetchStreamFromApi(
                    "/api/message/chat/paper",
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(requestBody),
                        signal: controller.signal,
                    }
                );

                await readOpenPaperUIMessageStream(stream, {
                    onText: (delta) => {
                        accumulated += delta;
                        setStreamingText((prev) => prev + delta);
                        setStreamingStatus(null);
                    },
                    onReasoning: (delta) => {
                        reasoning += delta;
                        setStreamingReasoning((prev) => prev + delta);
                    },
                    onReferences: (nextReferences) => {
                        references = nextReferences;
                        setStreamingReferences(nextReferences);
                    },
                    onStatus: setStreamingStatus,
                });

                if (accumulated) {
                    const finalMessage: ChatMessage = {
                        role: "assistant",
                        content: stripEvidenceBlock(accumulated),
                        references,
                        reasoning: reasoning || undefined,
                    };
                    setMessages((prev) => [...prev, finalMessage]);
                }
                // After the first message the server auto-generates a title;
                // refetch so the picker shows it instead of "Chat N".
                const activeBeforeStream = conversations.find(
                    (c) => c.id === conversationId
                );
                if (!activeBeforeStream?.title) {
                    refreshConversations();
                }
                try {
                    await refetchSubscription();
                } catch (err) {
                    console.error("Error refetching subscription:", err);
                }
            } catch (error) {
                const aborted =
                    controller.signal.aborted ||
                    (error instanceof DOMException &&
                        error.name === "AbortError");

                if (accumulated) {
                    // We already streamed a (partial) answer. A late failure —
                    // an abort, or an auxiliary/post-stream step such as
                    // citation reconciliation or title generation hitting the
                    // provider's content filter — must never discard what the
                    // user already saw. Finalize the streamed message instead
                    // of dropping the whole turn into the error state.
                    const finalizedMessage: ChatMessage = {
                        role: "assistant",
                        content: stripEvidenceBlock(accumulated),
                        references,
                        reasoning: reasoning || undefined,
                    };
                    setMessages((prev) => [...prev, finalizedMessage]);
                    if (!aborted) {
                        console.error(
                            "Stream ended with an error after partial content was received:",
                            error
                        );
                    }
                } else if (!aborted) {
                    // Nothing streamed yet — surface the retryable error box.
                    console.error("Error during streaming:", error);
                    setErrorState({ failedUserMessage });
                }
            } finally {
                if (abortControllerRef.current === controller) {
                    abortControllerRef.current = null;
                }
                setIsStreaming(false);
                setStreamingStatus(null);
            }
        },
        [
            currentMessage,
            isStreaming,
            conversationId,
            id,
            userMessageReferences,
            selectedModel,
            availableModels,
            reasoningEffort,
            contextMode,
            transformReferencesToFormat,
            refetchSubscription,
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

    const stopStreaming = useCallback(() => {
        abortControllerRef.current?.abort();
    }, []);

    useEffect(() => {
        return () => {
            abortControllerRef.current?.abort();
        };
    }, []);

    const handlePromptSubmit = (msg: PromptInputMessage, e: FormEvent) => {
        e.preventDefault();
        submitMessage(msg.text);
    };

    const isCreditMaxed = (creditUsage?.usagePercentage ?? 0) >= 100;
    const status: "ready" | "submitted" | "streaming" = isStreaming
        ? "streaming"
        : "ready";

    const isGated =
        subscription !== null &&
        subscription !== undefined &&
        subscription.plan !== undefined &&
        subscription.plan !== "researcher";

    const selectedModelOption = useMemo(
        () => availableModels.find((m) => m.id === selectedModel),
        [availableModels, selectedModel]
    );
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

    return (
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
                        !isFetchingHistory && (
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
                        messages.map((msg, index) => (
                            <PaperMessage
                                key={`${msg.id || index}-${index}`}
                                message={msg}
                                index={index}
                                user={user}
                                handleCitationClick={handleCitationClick}
                                matchesCurrentCitation={matchesCurrentCitation}
                                flashesCurrentCitation={flashesCurrentCitation}
                                onJumpToReference={setExplicitSearchTerm}
                            />
                        ))
                    )}

                    {errorState && !isStreaming && (
                        <Message from="assistant">
                            <MessageContent>
                                <div className="text-destructive text-sm space-y-2">
                                    <p>
                                        Something went wrong while answering.
                                    </p>
                                    <div className="flex gap-2">
                                        <Button
                                            variant="default"
                                            size="sm"
                                            onClick={() => {
                                                const failed =
                                                    errorState.failedUserMessage;
                                                const lastUserIndex = messages.findLastIndex(
                                                    (m) => m.role === "user"
                                                );
                                                if (
                                                    lastUserIndex !== -1 &&
                                                    messages[lastUserIndex]
                                                        ?.content === failed
                                                ) {
                                                    setMessages((prev) =>
                                                        prev.slice(
                                                            0,
                                                            lastUserIndex
                                                        )
                                                    );
                                                }
                                                setErrorState(null);
                                                submitMessage(failed);
                                            }}
                                        >
                                            Retry
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => {
                                                const lastUserIndex = messages.findLastIndex(
                                                    (m) => m.role === "user"
                                                );
                                                if (
                                                    lastUserIndex !== -1 &&
                                                    messages[lastUserIndex]
                                                        ?.content ===
                                                        errorState.failedUserMessage
                                                ) {
                                                    setMessages((prev) =>
                                                        prev.slice(
                                                            0,
                                                            lastUserIndex
                                                        )
                                                    );
                                                }
                                                setCurrentMessage(
                                                    errorState.failedUserMessage
                                                );
                                                setErrorState(null);
                                            }}
                                        >
                                            Edit
                                        </Button>
                                    </div>
                                </div>
                            </MessageContent>
                        </Message>
                    )}

                    {isStreaming && (() => {
                        const streamingIndex = messages.length;
                        const onStreamingCitationClick = (
                            key: string,
                            msgIdx: number,
                            paperId?: string
                        ) => {
                            handleCitationClick(key, msgIdx, paperId);
                            if (msgIdx === streamingIndex)
                                setStreamingSourcesOpen(true);
                        };
                        return (
                            <Message from="assistant">
                                <MessageContent className="!text-foreground">
                                    {streamingReasoning && (
                                        <Reasoning isStreaming={isStreaming}>
                                            <ReasoningTrigger />
                                            <ReasoningContent>
                                                {streamingReasoning}
                                            </ReasoningContent>
                                        </Reasoning>
                                    )}
                                    {streamingText.length > 0 ? (
                                        <AnimatedMarkdown
                                            content={stripEvidenceBlock(streamingText)}
                                            className="prose-sm"
                                            components={citationComponents(
                                                onStreamingCitationClick,
                                                streamingIndex,
                                                streamingReferences?.citations || []
                                            )}
                                        />
                                    ) : (
                                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                            <Loader size={14} />
                                            <span>{streamingStatus ?? "Thinking…"}</span>
                                        </div>
                                    )}
                                </MessageContent>
                                {streamingReferences?.citations &&
                                    streamingReferences.citations.length > 0 && (
                                        <div className="mt-2">
                                            <PaperSources
                                                citations={
                                                    streamingReferences.citations
                                                }
                                                messageIndex={streamingIndex}
                                                handleCitationClick={
                                                    onStreamingCitationClick
                                                }
                                                matchesCurrentCitation={
                                                    matchesCurrentCitation
                                                }
                                                open={streamingSourcesOpen}
                                                onOpenChange={
                                                    setStreamingSourcesOpen
                                                }
                                            />
                                        </div>
                                    )}
                            </Message>
                        );
                    })()}
                </ConversationContent>
                <ConversationScrollButton />
            </Conversation>

            <div className="px-3 pb-3 pt-2 space-y-2">
                {messages.length <= 1 && !hasMoreMessages && !isStreaming && (
                    <Suggestions>
                        {starterQuestions.slice(0, 5).map((q, i) => {
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
                                                                key={m.id}
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
                                                            key={m.id}
                                                            onClick={() =>
                                                                setSelectedModel(
                                                                    m.id
                                                                )
                                                            }
                                                            className="flex items-center justify-between"
                                                        >
                                                            <span className="truncate">
                                                                {m.name}
                                                            </span>
                                                            {m.id ===
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
                            status={status}
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
                                          stopStreaming();
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

function citationComponents(
    handleCitationClick: (key: string, messageIndex: number, paperId?: string) => void,
    messageIndex: number,
    citations: Citation[]
): Components {
    // CustomCitationLink calls handleCitationClick(key, messageIndex) without a
    // paper_id (it's a shared component used in non-chat surfaces too). We
    // resolve the matching citation's paper_id from the citations array here so
    // it reaches the page's handler — which uses it to flip the displayed PDF
    // when the citation refers to a supplementary.
    const onClickWithPaperId = (key: string, msgIdx: number) => {
        const match = citations.find((c) => String(c.key) === key);
        handleCitationClick(key, msgIdx, match?.paper_id);
    };
    const inject = (props: object) => (
        <CustomCitationLink
            {...(props as Record<string, unknown>)}
            handleCitationClick={onClickWithPaperId}
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
    } as Components;
}

interface PaperMessageProps {
    message: ChatMessage;
    index: number;
    user: ReturnType<typeof useAuth>["user"];
    handleCitationClick: (key: string, messageIndex: number, paperId?: string) => void;
    matchesCurrentCitation: (key: string, messageIndex: number) => boolean;
    flashesCurrentCitation?: (key: string, messageIndex: number) => boolean;
    onJumpToReference: (text: string) => void;
}

function PaperMessage({
    message,
    index,
    user,
    handleCitationClick,
    matchesCurrentCitation,
    flashesCurrentCitation,
    onJumpToReference,
}: PaperMessageProps) {
    const citations = message.references?.citations ?? [];
    const isUser = message.role === "user";

    const [sourcesOpen, setSourcesOpen] = useState(false);
    const hasFlash = flashesCurrentCitation
        ? citations.some((c) => flashesCurrentCitation(c.key, index))
        : false;
    // Auto-open when a citation we own is flashing (couldn't be located in the
    // PDF). Sticky-open after the flash clears so the user can close manually.
    useEffect(() => {
        if (hasFlash) setSourcesOpen(true);
    }, [hasFlash]);

    const onCitationClick = useCallback(
        (key: string, msgIdx: number, paperId?: string) => {
            handleCitationClick(key, msgIdx, paperId);
            if (msgIdx === index) setSourcesOpen(true);
        },
        [handleCitationClick, index]
    );

    return (
        <div data-message-index={index}>
            <Message from={message.role}>
                {isUser && user && (
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
                {isUser && citations.length > 0 && (
                    <div className="ml-auto flex flex-col items-end gap-1">
                        {citations.map((c, i) => (
                            <button
                                key={`${c.key}-${i}`}
                                type="button"
                                onClick={() => onJumpToReference(c.reference)}
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
                    {!isUser && message.reasoning && (
                        <Reasoning isStreaming={false} defaultOpen={false}>
                            <ReasoningTrigger />
                            <ReasoningContent>
                                {message.reasoning}
                            </ReasoningContent>
                        </Reasoning>
                    )}
                    <div className="prose dark:prose-invert prose-sm !max-w-none">
                        <Markdown
                            remarkPlugins={[
                                [remarkMath, { singleDollarTextMath: false }],
                                remarkGfm,
                            ]}
                            rehypePlugins={[rehypeKatex]}
                            components={citationComponents(
                                onCitationClick,
                                index,
                                citations
                            )}
                        >
                            {message.content}
                        </Markdown>
                    </div>
                </MessageContent>
            </Message>
            {!isUser && citations.length > 0 && (
                <div className="mt-2 ml-0">
                    <PaperSources
                        citations={citations}
                        messageIndex={index}
                        handleCitationClick={onCitationClick}
                        matchesCurrentCitation={matchesCurrentCitation}
                        flashesCurrentCitation={flashesCurrentCitation}
                        open={sourcesOpen}
                        onOpenChange={setSourcesOpen}
                        rightSlot={
                            <ChatMessageActions
                                message={message.content}
                                references={message.references}
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
    handleCitationClick: (key: string, messageIndex: number, paperId?: string) => void;
    matchesCurrentCitation: (key: string, messageIndex: number) => boolean;
    flashesCurrentCitation?: (key: string, messageIndex: number) => boolean;
    rightSlot?: React.ReactNode;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

function PaperSources({
    citations,
    messageIndex,
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
                                        messageIndex,
                                        citation.paper_id
                                    )
                                }
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        handleCitationClick(
                                            citation.key,
                                            messageIndex,
                                            citation.paper_id
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
                                        remarkPlugins={[remarkGfm, remarkMath]}
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
