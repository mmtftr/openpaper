"use client";

import {
    Conversation,
    ConversationContent,
    ConversationEmptyState,
    ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { Loader } from "@/components/ai-elements/loader";
import { Button } from "@/components/ui/button";
import { ChatHistorySkeleton } from "@/components/ChatHistorySkeleton";
import type { CitationClickHandler } from "@/components/paper/useCitationJump";
import { useAuth } from "@/lib/auth";
import { pendingToolLabel } from "@/lib/chatMessages";
import type { PaperChat } from "../usePaperChat";
import type { useTurnFailures } from "../useTurnFailures";
import { LoadEarlier } from "./LoadEarlier";
import { PaperMessage } from "./PaperMessage";
import { FailedTurnNotice } from "./TurnNotice";

interface MessageListProps {
    chat: PaperChat;
    turns: ReturnType<typeof useTurnFailures>;
    handleCitationClick: CitationClickHandler;
    onRetryLastTurn: () => void;
    onEditFailedTurn: (text: string, references: string[]) => void;
}

/**
 * The scrolling transcript: "load earlier", the history skeleton / error /
 * empty state, the messages, a thinking/retrying placeholder while the answer
 * hasn't started, and the failure note for a turn that got no answer.
 */
export function MessageList({
    chat,
    turns,
    handleCitationClick,
    onRetryLastTurn,
    onEditFailedTurn,
}: MessageListProps) {
    const { user } = useAuth();
    const { messages, status, isStreaming, history, session, retryStatus } = chat;
    const { userTurnFailure, noticeForMessage, showThinkingPlaceholder } = turns;
    const lastMessage = messages[messages.length - 1];

    let body: React.ReactNode;
    if (history.isFetching) {
        body = <ChatHistorySkeleton />;
    } else if (history.error && messages.length === 0) {
        body = null;
    } else if (messages.length === 0 && !isStreaming) {
        body = (
            <ConversationEmptyState
                title="Start a conversation"
                description="Ask anything about this paper, or pick one of the suggested prompts to begin."
            />
        );
    } else {
        body = messages.map((msg, index) => {
            const isLast = index === messages.length - 1;
            const notice = noticeForMessage(msg, isLast);
            // Only the turn in flight rises in (the question, then its answer);
            // loaded history just appears.
            const risesIn =
                isStreaming &&
                (isLast || (msg.role === "user" && messages[messages.length - 1]?.role === "assistant" && index === messages.length - 2));
            return (
                <div key={msg.id || index} className={risesIn ? "animate-rise-in" : undefined}>
                    <PaperMessage
                        message={msg}
                        index={index}
                        isStreamingMessage={isStreaming && isLast && msg.role === "assistant"}
                        user={user}
                        handleCitationClick={handleCitationClick}
                        notice={notice}
                        onRetry={onRetryLastTurn}
                        retryDisabled={notice ? isStreaming : false}
                    />
                </div>
            );
        });
    }

    return (
        <Conversation className="flex-1 min-h-0">
            <ConversationContent className="flex flex-col gap-6 px-3 py-4">
                {history.hasMore && messages.length > 0 && !history.isFetching && status === "ready" && (
                    <LoadEarlier
                        isLoading={history.isLoadingMore}
                        onLoad={async () => {
                            await session.loadHistoryPage();
                        }}
                    />
                )}

                {history.error && !history.loaded && (
                    <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                        <span>{history.error} —</span>
                        <Button
                            variant="link"
                            size="sm"
                            className="h-auto p-0"
                            onClick={() => session.ensureHistoryLoaded()}
                        >
                            Retry
                        </Button>
                    </div>
                )}
                {body}

                {showThinkingPlaceholder && (
                    <Message from="assistant" className="animate-rise-in">
                        <MessageContent>
                            <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                <Loader size={14} />
                                <span>
                                    {retryStatus
                                        ? `Retrying (attempt ${retryStatus.attempt ?? 2}/${
                                              retryStatus.maxAttempts ?? 3
                                          })…`
                                        : pendingToolLabel(
                                              lastMessage?.role === "assistant" ? lastMessage : undefined
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
                    <FailedTurnNotice
                        failure={userTurnFailure}
                        retryDisabled={isStreaming}
                        onRetry={onRetryLastTurn}
                        onEdit={() =>
                            onEditFailedTurn(userTurnFailure.text, userTurnFailure.references)
                        }
                    />
                )}
            </ConversationContent>
            <ConversationScrollButton />
        </Conversation>
    );
}
