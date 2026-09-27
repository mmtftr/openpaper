"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, unwrap, type Schemas } from "@/lib/api/client";
import {
    disposePaperChatSession,
    findPaperChatSession,
    recallActiveConversation,
    recallOpenConversations,
    rememberActiveConversation,
    rememberOpenConversations,
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

const activeStorageKey = (paperId: string) => `openpaper:active-conversation:${paperId}`;
const openStorageKey = (paperId: string) => `openpaper:open-conversations:${paperId}`;

function readStoredOpen(paperId: string): string[] | null {
    try {
        const raw = window.localStorage.getItem(openStorageKey(paperId));
        const parsed: unknown = raw ? JSON.parse(raw) : null;
        return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : null;
    } catch {
        return null;
    }
}

/**
 * A conversation that has nothing in it yet: untitled (the server titles a
 * conversation after its first turn) and, if its transcript is loaded, empty.
 * "+" reuses one of these instead of minting another empty conversation.
 */
function isBlank(conversation: ConversationSummary | undefined): boolean {
    if (!conversation || conversation.title) return false;
    const session = findPaperChatSession(conversation.id);
    if (!session) return false;
    const { historyLoaded } = session.getState();
    return historyLoaded && session.chat.messages.length === 0 && session.chat.status === "ready";
}

/**
 * A paper's chat conversations, which of them are open as tabs, and the
 * active one.
 *
 * Restores the tabs and active conversation this browser last had for the
 * paper (in-memory, then localStorage), else opens the most recent, else a
 * new one. Both are remembered per paper as they change. `paperReady` gates
 * the first load: the paper details are a fresh object on every status sync,
 * and re-running the load while its create POST was in flight used to mint a
 * second empty conversation.
 */
export function useConversationList(paperId: string, paperReady: boolean) {
    // Seeded from the in-memory session store so a remount (layout swap)
    // lands straight back on the conversation it left, transcript included.
    const [conversationId, setConversationId] = useState<string | null>(() =>
        recallActiveConversation(paperId)
    );
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    // Tagged with its paper so a paper switch never saves one paper's tabs
    // under the other's key.
    const [open, setOpen] = useState<{ paperId: string; ids: string[] }>(() => ({
        paperId,
        ids: recallOpenConversations(paperId) ?? [],
    }));
    const openIds = open.paperId === paperId ? open.ids : [];

    // A different paper in the same mounted panel: start from its own
    // remembered conversation, never the previous paper's.
    const paperIdRef = useRef(paperId);
    useEffect(() => {
        if (paperIdRef.current === paperId) return;
        paperIdRef.current = paperId;
        setConversationId(recallActiveConversation(paperId));
        setOpen({ paperId, ids: recallOpenConversations(paperId) ?? [] });
    }, [paperId]);

    const loadedRef = useRef<string | null>(null);
    useEffect(() => {
        if (loadedRef.current !== open.paperId) return;
        rememberOpenConversations(open.paperId, open.ids);
        window.localStorage.setItem(openStorageKey(open.paperId), JSON.stringify(open.ids));
    }, [open]);

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

            const known = new Set(list.map((c) => c.id));
            const remembered =
                recallActiveConversation(paperId) ??
                window.localStorage.getItem(activeStorageKey(paperId));
            const storedOpen = (recallOpenConversations(paperId) ?? readStoredOpen(paperId) ?? []).filter(
                (id) => known.has(id)
            );
            let active =
                (remembered && known.has(remembered) ? remembered : null) ??
                storedOpen[0] ??
                list[0]?.id ??
                null;

            if (!active) {
                try {
                    const created = await createConversation(paperId);
                    if (cancelled) return;
                    list = [summaryOf(created)];
                    active = created.id;
                } catch (err) {
                    console.error("Error creating initial conversation:", err);
                }
            }

            setConversations(list);
            loadedRef.current = paperId;
            setOpen({
                paperId,
                ids: active && !storedOpen.includes(active) ? [...storedOpen, active] : storedOpen,
            });
            if (active) setConversationId(active);
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
        window.localStorage.setItem(activeStorageKey(paperId), conversationId);
    }, [paperId, conversationId]);

    const refresh = useCallback(async () => {
        try {
            setConversations(await listConversations(paperId));
        } catch (err) {
            console.error("Error refreshing conversations:", err);
        }
    }, [paperId]);

    /** Show a conversation as the active tab, opening a tab for it if needed. */
    const openConversation = useCallback(
        (id: string) => {
            setOpen((prev) =>
                prev.paperId !== paperId
                    ? prev
                    : prev.ids.includes(id)
                      ? prev
                      : { paperId, ids: [...prev.ids, id] }
            );
            setConversationId(id);
        },
        [paperId]
    );

    /** A fresh conversation in a new active tab (or an open blank one, reused). */
    const startNew = useCallback(async () => {
        const blank = openIds.find((id) => isBlank(conversations.find((c) => c.id === id)));
        if (blank) {
            openConversation(blank);
            return;
        }
        try {
            const created = await createConversation(paperId);
            setConversations((prev) => [summaryOf(created), ...prev]);
            openConversation(created.id);
        } catch (err) {
            console.error("Error creating new conversation:", err);
            toast.error("Could not start a new chat.");
        }
    }, [paperId, openIds, conversations, openConversation]);

    /**
     * Close a tab. The conversation (and any stream it is running) carries on;
     * it can be reopened from history. Closing the last tab opens a fresh one.
     */
    const closeConversation = useCallback(
        (id: string) => {
            const index = openIds.indexOf(id);
            if (index === -1) return;
            // The only tab, and already a fresh chat: nothing to replace it with.
            if (openIds.length === 1 && isBlank(conversations.find((c) => c.id === id))) return;
            const remaining = openIds.filter((openId) => openId !== id);
            setOpen({ paperId, ids: remaining });
            if (id !== conversationId) return;
            const next = remaining[index] ?? remaining[index - 1];
            if (next) setConversationId(next);
            else void startNew();
        },
        [openIds, conversations, paperId, conversationId, startNew]
    );

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
            setConversations((prev) => prev.filter((c) => c.id !== target));
            // Stops the deleted chat's stream if it still has one, or the
            // server keeps generating into a deleted conversation.
            disposePaperChatSession(target);
            if (openIds.includes(target)) closeConversation(target);
        },
        [openIds, closeConversation]
    );

    return {
        conversations,
        conversationId,
        openIds,
        remember,
        refresh,
        openConversation,
        closeConversation,
        startNew,
        remove,
    };
}
