"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { AlertTriangleIcon } from "lucide-react";

import { PaperData } from "@/lib/schema";
import { MAX_USER_REFERENCES, truncateReference } from "@/lib/userReferences";
import { CodeViewerProvider } from "@/components/code/CodeViewerProvider";
import { userMessageReferencesAtom } from "@/components/paper/paperStore";
import { usePaperAtom } from "@/components/paper/PaperStoreProvider";
import { useCitationClick } from "@/components/paper/useCitationJump";

import { ChatComposer, type ChatComposerHandle } from "./ChatComposer";
import { ChatStarters } from "./ChatStarters";
import { ChatSessionTabs } from "./ChatSessionTabs";
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
}

/**
 * The paper's chat: conversation tabs, transcript and composer. Staged
 * references and citation jumps go through the paper store; the chat session
 * itself lives outside React (paperChatSessions.ts).
 *
 * Every conversation keeps its own session, so switching or closing a tab
 * leaves a stream running in it; only deleting a conversation stops it.
 */
export function PaperChatPanel({ id, paperData }: PaperChatPanelProps) {
    const conversationList = useConversationList(id, Boolean(paperData));
    const { conversationId, conversations, openIds } = conversationList;
    const chat = usePaperChat(id, conversationId);
    const { session, messages, isStreaming } = chat;
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
    const { remember, refresh: refreshConversations } = conversationList;
    useEffect(() => {
        if (session.paperId === id) remember();
    }, [id, session, remember]);

    const { openTabs, closedConversations } = useMemo(() => {
        const byId = new Map(conversations.map((c) => [c.id, c]));
        return {
            openTabs: openIds.flatMap((openId) => byId.get(openId) ?? []),
            closedConversations: conversations.filter((c) => !openIds.includes(c.id)),
        };
    }, [conversations, openIds]);

    // "+" lands on an empty conversation either way; put the cursor in it.
    const { startNew } = conversationList;
    const onNewChat = useCallback(() => {
        void startNew().then(() => {
            requestAnimationFrame(() => composerRef.current?.focus());
        });
    }, [startNew]);

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
        // chips) through this one provider.
        <CodeViewerProvider
            paperId={id}
            onAttachReference={attachReference}
            chatModel={codeQuestionModel}
        >
            <div className="flex h-full min-h-0 flex-col">
                <ChatSessionTabs
                    paperId={id}
                    tabs={openTabs}
                    activeId={conversationId}
                    closed={closedConversations}
                    onSelect={conversationList.openConversation}
                    onClose={conversationList.closeConversation}
                    onNew={onNewChat}
                    onDelete={(target) => void conversationList.remove(target)}
                    onTurnSettled={refreshConversations}
                />

                <MessageList
                    chat={chat}
                    turns={turns}
                    handleCitationClick={handleCitationClick}
                    onRetryLastTurn={submit.onRetryLastTurn}
                    onEditFailedTurn={submit.editFailedTurn}
                />

                <div className="space-y-2 px-3 pb-3 pt-1">
                    {messages.length <= 1 && !chat.history.hasMore && !isStreaming && (
                        <ChatStarters onPick={setPendingStarter} />
                    )}

                    <PendingReferences />

                    {contextMode === "full" && (
                        <div className="flex items-start gap-2 rounded-md border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-xs text-amber-900 dark:text-amber-200">
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
