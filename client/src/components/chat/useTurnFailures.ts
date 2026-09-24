"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ChatUIMessage,
    citationsFromMessage,
    interruptedMetadata,
    normalizeChatError,
    renderBlocks,
    textFromMessage,
} from "@/lib/chatMessages";
import {
    FAILED_TURN_TITLE,
    type TurnNotice,
    type UserTurnFailure,
} from "./messages/TurnNotice";
import type { PaperChat } from "./usePaperChat";

/**
 * How the transcript's tail reads: still thinking, failed before or after
 * the assistant said anything, or stopped.
 */
export function useTurnFailures(chat: PaperChat) {
    const { messages, status, error, isStreaming, truncatedTurn, retryStatus } = chat;
    const isFetchingHistory = chat.history.isFetching;
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
        if (status !== "ready" || isFetchingHistory || lastMessageRole !== "user") {
            setDanglingUserTurn(false);
            return;
        }
        const timer = setTimeout(() => setDanglingUserTurn(true), 600);
        return () => clearTimeout(timer);
    }, [status, isFetchingHistory, lastMessageRole, lastMessageId]);

    // Case (b): the turn failed before any assistant content arrived.
    const userTurnFailure = useMemo((): UserTurnFailure | null => {
        if (isStreaming || lastMessage?.role !== "user") return null;
        const text = textFromMessage(lastMessage);
        if (!text) return null;
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

    // Case (a): the note under an assistant turn — live (`error` / a silently
    // truncated stream) or reloaded from history (`metadata.interrupted`).
    // Retry is offered on the last turn only.
    const noticeForMessage = useCallback(
        (message: ChatUIMessage, isLast: boolean): TurnNotice | null => {
            if (message.role !== "assistant") return null;
            if (isLast && !isStreaming) {
                if (error) {
                    const { headline, detail } = normalizeChatError(error);
                    return { tone: "error", title: FAILED_TURN_TITLE, headline, detail, canRetry: true };
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
            const { headline, detail } = normalizeChatError(interrupted.errorText);
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
                (lastMessage?.role === "assistant" && renderBlocks(lastMessage).length === 0)));

    return { userTurnFailure, noticeForMessage, showThinkingPlaceholder };
}
