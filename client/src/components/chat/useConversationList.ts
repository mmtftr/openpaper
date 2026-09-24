"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import {
    disposePaperChatSession,
    recallActiveConversation,
    rememberActiveConversation,
} from "./paperChatSessions";

export type ConversationSummary = Schemas["PaperConversationSummary"];

const listConversations = (paperId: string) =>
    unwrap(
        api.GET("/api/paper/conversations", {
            params: { query: { paper_id: paperId } },
        })
    );

const createConversation = (paperId: string) =>
    unwrap(
        api.POST("/api/conversation/paper/{paper_id}", {
            params: { path: { paper_id: paperId } },
        })
    );

const summaryOf = (created: Awaited<ReturnType<typeof createConversation>>): ConversationSummary => ({
    id: created.id,
    title: created.title ?? null,
    updated_at: new Date().toISOString(),
});

const conversationStorageKey = (paperId: string) =>
    `openpaper:active-conversation:${paperId}`;

/**
 * A paper's chat conversations and which one is open.
 *
 * Opens the conversation this tab last had open for the paper (in-memory,
 * then localStorage), else the most recent, else a new one. The open id is
 * remembered for the paper as it changes. `paperReady` gates the first load:
 * the paper details are a fresh object on every status sync, and re-running
 * the load while its create POST was in flight used to mint a second empty
 * conversation.
 */
export function useConversationList(paperId: string, paperReady: boolean) {
    // Seeded from the in-memory session store so a remount (tab switch)
    // lands straight back on the conversation it left, transcript included.
    const [conversationId, setConversationId] = useState<string | null>(() =>
        recallActiveConversation(paperId)
    );
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);

    // A different paper in the same mounted panel: start from its own
    // remembered conversation, never the previous paper's.
    const paperIdRef = useRef(paperId);
    useEffect(() => {
        if (paperIdRef.current === paperId) return;
        paperIdRef.current = paperId;
        setConversationId(recallActiveConversation(paperId));
    }, [paperId]);

    useEffect(() => {
        if (!paperReady) return;
        let cancelled = false;

        async function init() {
            let list: ConversationSummary[] = [];
            try {
                list = await listConversations(paperId);
            } catch (err) {
                console.error("Error fetching conversations:", err);
            }
            if (cancelled) return;
            setConversations(list);

            const remembered =
                recallActiveConversation(paperId) ??
                window.localStorage.getItem(conversationStorageKey(paperId));
            const existing = list.find((c) => c.id === remembered) ?? list[0];
            if (existing) {
                setConversationId(existing.id);
                return;
            }

            try {
                const created = await createConversation(paperId);
                if (cancelled) return;
                setConversations([summaryOf(created)]);
                setConversationId(created.id);
            } catch (err) {
                console.error("Error creating initial conversation:", err);
            }
        }

        init();
        return () => {
            cancelled = true;
        };
    }, [paperReady, paperId]);

    /**
     * Remember the open conversation for this paper. Only call it for a
     * conversation of this paper: right after a paper switch the previous
     * paper's id is still open.
     */
    const remember = useCallback(() => {
        if (!paperId || !conversationId) return;
        rememberActiveConversation(paperId, conversationId);
        window.localStorage.setItem(conversationStorageKey(paperId), conversationId);
    }, [paperId, conversationId]);

    const refresh = useCallback(async () => {
        try {
            setConversations(await listConversations(paperId));
        } catch (err) {
            console.error("Error refreshing conversations:", err);
        }
    }, [paperId]);

    const startNew = useCallback(async () => {
        try {
            const created = await createConversation(paperId);
            setConversations((prev) => [summaryOf(created), ...prev]);
            setConversationId(created.id);
        } catch (err) {
            console.error("Error creating new conversation:", err);
            toast.error("Could not start a new chat.");
        }
    }, [paperId]);

    const remove = useCallback(
        async (target: string) => {
            try {
                await unwrap(
                    api.DELETE("/api/conversation/{conversation_id}", {
                        params: { path: { conversation_id: target } },
                    })
                );
            } catch (err) {
                console.error("Error deleting conversation:", err);
                toast.error("Could not delete that chat.");
                return;
            }
            const remaining = conversations.filter((c) => c.id !== target);
            setConversations(remaining);
            // Stops the deleted chat's stream if it still has one, or the
            // server keeps generating into a deleted conversation.
            disposePaperChatSession(target);
            if (conversationId !== target) return;
            const next = remaining[0];
            if (next) {
                setConversationId(next.id);
                return;
            }
            try {
                const created = await createConversation(paperId);
                setConversations([summaryOf(created)]);
                setConversationId(created.id);
            } catch (err) {
                console.error("Error creating replacement conversation:", err);
            }
        },
        [conversationId, conversations, paperId]
    );

    const activeTitle = conversations.find((c) => c.id === conversationId)?.title;

    return {
        conversations,
        conversationId,
        setConversationId,
        activeTitle,
        remember,
        refresh,
        startNew,
        remove,
    };
}
