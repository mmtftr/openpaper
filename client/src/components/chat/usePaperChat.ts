"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import { useChat } from "@ai-sdk/react";
import { useAuth } from "@/lib/auth";
import type { ChatUIMessage } from "@/lib/chatMessages";
import { getPaperChatSession } from "./paperChatSessions";

/**
 * The chat session for one conversation, and its transcript paging.
 *
 * One AI SDK `Chat` per conversation, held outside React (see
 * paperChatSessions.ts) so a stream and its transcript survive the panel
 * unmounting on a side-panel tab switch; this attaches to it. The first
 * history page loads once per conversation, once the user is known.
 */
export function usePaperChat(paperId: string, conversationId: string | null) {
    const { user } = useAuth();
    const session = useMemo(
        () => getPaperChatSession(paperId, conversationId),
        [paperId, conversationId]
    );
    const sessionState = useSyncExternalStore(
        session.subscribe,
        session.getState,
        session.getState
    );
    const chat = useChat<ChatUIMessage>({
        chat: session.chat,
        // Batch streamed chunks: re-rendering long cited answers per chunk
        // is what makes streaming janky.
        experimental_throttle: 50,
    });

    useEffect(() => {
        if (!user) return;
        session.ensureHistoryLoaded();
    }, [user, session]);

    const history = {
        loaded: sessionState.historyLoaded,
        error: sessionState.historyError,
        /** The first page is still on its way (not failed). */
        isFetching: !sessionState.historyLoaded && !sessionState.historyError,
        hasMore: sessionState.hasMoreHistory,
        isLoadingMore: sessionState.loadingHistory,
    };

    return {
        ...chat,
        session,
        isStreaming: chat.status === "submitted" || chat.status === "streaming",
        retryStatus: sessionState.retryStatus,
        truncatedTurn: sessionState.truncatedTurn,
        history,
    };
}

export type PaperChat = ReturnType<typeof usePaperChat>;
