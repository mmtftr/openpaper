"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { AlertTriangleIcon } from "lucide-react";

import { PaperData } from "@/lib/schema";
import { MAX_USER_REFERENCES, truncateReference } from "@/lib/userReferences";
import { CodeViewerProvider } from "@/components/code/CodeViewerProvider";
import { RepoConnectPopover } from "@/components/code/RepoConnectPopover";
import { userMessageReferencesAtom } from "@/components/paper/paperStore";
import { usePaperAtom } from "@/components/paper/PaperStoreProvider";
import { useCitationClick } from "@/components/paper/useCitationJump";

import { ChatComposer, type ChatComposerHandle } from "./ChatComposer";
import { ChatStarters } from "./ChatStarters";
import { ConversationSwitcher } from "./ConversationSwitcher";
import { MessageList } from "./messages/MessageList";
import { PendingReferences } from "./PendingReferences";
import { useChatModelOptions } from "./useChatModelOptions";
import { useChatSubmit } from "./useChatSubmit";
import { useConversationList } from "./useConversationList";
import { usePaperChat } from "./usePaperChat";
import { useTurnFailures } from "./useTurnFailures";

interface PaperChatPanelProps {
    id: string;
    paperData: PaperData;
    headerSlot?: React.ReactNode;
}

/**
 * The paper's chat: conversation switcher, transcript and composer. Staged
 * references and citation jumps go through the paper store; the chat session
 * itself lives outside React (paperChatSessions.ts).
 */
export function PaperChatPanel({ id, paperData, headerSlot }: PaperChatPanelProps) {
    const conversationList = useConversationList(id, Boolean(paperData));
    const { conversationId, setConversationId } = conversationList;
    const chat = usePaperChat(id, conversationId);
    const { session, status, messages, isStreaming, stop } = chat;
    const options = useChatModelOptions();
    const composerRef = useRef<ChatComposerHandle>(null);
    const submit = useChatSubmit({ paperId: id, conversationId, chat, options, composerRef });
    const turns = useTurnFailures(chat);
    const handleCitationClick = useCitationClick();
    const [userMessageReferences, setUserMessageReferences] = usePaperAtom(
        userMessageReferencesAtom
    );

    // Only a conversation of this paper is remembered for it (right after a
    // paper switch the previous paper's id is still open).
    const { remember } = conversationList;
    useEffect(() => {
        if (session.paperId === id) remember();
    }, [id, session, remember]);

    // After the first turn the server auto-generates a title; refetch the
    // list when a stream completes for an untitled conversation.
    useEffect(() => {
        if (status === "ready" && messages.length > 0 && !conversationList.activeTitle) {
            void conversationList.refresh();
        }
    }, [status]);

    const onNewChat = useCallback(() => {
        if (isStreaming) stop();
        void conversationList.startNew();
    }, [isStreaming, stop, conversationList]);
    const onSelectConversation = useCallback(
        (next: string) => {
            if (next === conversationId) return;
            if (isStreaming) stop();
            setConversationId(next);
        },
        [conversationId, isStreaming, stop, setConversationId]
    );

    // A starter goes through a render so it's sent with the current options.
    const [pendingStarter, setPendingStarter] = useState<string | null>(null);
    const { submitMessage } = submit;
    useEffect(() => {
        if (!pendingStarter) return;
        submitMessage(pendingStarter);
        setPendingStarter(null);
        // Only a new starter sends.
    }, [pendingStarter]);

    // Code selections from the repo viewer land in the same staged list as
    // PDF text selections (see PdfReader's handleAskAi) and travel out through
    // the same `user_references` field.
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

    // The same provider/model/effort the chat sends, for the code viewer's
    // inline quick questions.
    const { selectedModelOption, supportsReasoningEffort, reasoningEffort, contextMode } = options;
    const codeQuestionModel = useMemo(
        () => ({
            model: selectedModelOption?.id ?? null,
            llmProvider: selectedModelOption?.provider ?? null,
            reasoningEffort: supportsReasoningEffort ? reasoningEffort : null,
        }),
        [selectedModelOption, supportsReasoningEffort, reasoningEffort]
    );

    return (
        // Everything below can open the repo code viewer (citations, tool
        // chips, the repo-connect popover) through this one provider.
        <CodeViewerProvider
            paperId={id}
            onAttachReference={attachReference}
            chatModel={codeQuestionModel}
        >
            <div className="flex h-full min-h-0 flex-col">
                <div className="flex items-center justify-between gap-1 px-2 py-1.5 border-b border-border/40">
                    <div className="flex items-center gap-1">
                        <ConversationSwitcher
                            conversations={conversationList.conversations}
                            activeId={conversationId}
                            onNew={onNewChat}
                            onSelect={onSelectConversation}
                            onDelete={(target) => void conversationList.remove(target)}
                        />
                        <RepoConnectPopover paperId={id} />
                    </div>
                    {headerSlot}
                </div>

                <MessageList
                    chat={chat}
                    turns={turns}
                    handleCitationClick={handleCitationClick}
                    onRetryLastTurn={submit.onRetryLastTurn}
                    onEditFailedTurn={submit.editFailedTurn}
                />

                <div className="px-3 pb-3 pt-2 space-y-2">
                    {messages.length <= 1 && !chat.history.hasMore && !isStreaming && (
                        <ChatStarters onPick={setPendingStarter} />
                    )}

                    <PendingReferences />

                    {contextMode === "full" && (
                        <div className="mb-2 flex items-start gap-2 rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
                            <AlertTriangleIcon className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                            <span>
                                This mode sends the entire paper to the model on every turn.
                                Adaptive is faster and cheaper for most questions.
                            </span>
                        </div>
                    )}

                    <ChatComposer
                        ref={composerRef}
                        isStreaming={isStreaming}
                        canSend={!!conversationId}
                        onSubmit={submit.onComposerSubmit}
                        onStop={submit.onStop}
                        availableModels={options.availableModels}
                        defaultModelLabel={options.defaultModelLabel}
                        selectedModel={options.selectedModel}
                        onSelectModel={options.setSelectedModel}
                        supportsReasoningEffort={supportsReasoningEffort}
                        reasoningEffort={reasoningEffort}
                        onSelectReasoningEffort={options.setReasoningEffort}
                        availableContextModes={options.availableContextModes}
                        contextMode={contextMode}
                        onSelectContextMode={options.setContextMode}
                    />
                </div>
            </div>
        </CodeViewerProvider>
    );
}
