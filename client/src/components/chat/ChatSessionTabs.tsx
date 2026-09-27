"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { HistoryIcon, Loader2Icon, PlusIcon, Trash2Icon, XIcon } from "lucide-react";

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
import { EASE_OUT_SOFT, PILL_SPRING } from "@/lib/motion";
import { cn } from "@/lib/utils";
import type { ConversationSummary } from "./useConversationList";
import { usePaperChatBusy } from "./usePaperChat";

export const conversationTitle = (c: ConversationSummary | undefined) =>
    c?.title?.trim() || "New chat";

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
    ["year", 31_536_000],
    ["month", 2_592_000],
    ["week", 604_800],
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
];
const relativeFormat = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

function relativeTime(iso: string | null | undefined): string {
    const at = iso ? Date.parse(iso) : NaN;
    if (Number.isNaN(at)) return "";
    const seconds = (at - Date.now()) / 1000;
    for (const [unit, size] of RELATIVE_UNITS) {
        if (Math.abs(seconds) >= size) return relativeFormat.format(Math.round(seconds / size), unit);
    }
    return "just now";
}

interface ChatSessionTabsProps {
    paperId: string;
    /** The open conversations, in tab order. */
    tabs: ConversationSummary[];
    activeId: string | null;
    /** The paper's conversations that aren't open, for the history menu. */
    closed: ConversationSummary[];
    onSelect: (conversationId: string) => void;
    onClose: (conversationId: string) => void;
    onNew: () => void;
    /** Called once the user confirms. */
    onDelete: (conversationId: string) => void;
    /** An untitled conversation finished a turn; the server has titled it. */
    onTurnSettled: () => void;
}

/**
 * The chat's top bar: open conversations as tabs, "+" for a new one, and the
 * history menu to reopen (or delete) the paper's other conversations.
 */
export function ChatSessionTabs({
    paperId,
    tabs,
    activeId,
    closed,
    onSelect,
    onClose,
    onNew,
    onDelete,
    onTurnSettled,
}: ChatSessionTabsProps) {
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
    const scrollerRef = useRef<HTMLDivElement>(null);
    const [fade, setFade] = useState({ start: false, end: false });

    const updateFade = useCallback(() => {
        const el = scrollerRef.current;
        if (!el) return;
        const start = el.scrollLeft > 1;
        const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
        setFade((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
    }, []);

    useLayoutEffect(() => {
        const el = scrollerRef.current;
        if (!el) return;
        updateFade();
        const observer = new ResizeObserver(updateFade);
        observer.observe(el);
        for (const child of Array.from(el.children)) observer.observe(child);
        // Tabs entering, leaving and sliding over overflow the strip while
        // they animate; measure again once they have settled.
        const settled = setTimeout(updateFade, 400);
        return () => {
            observer.disconnect();
            clearTimeout(settled);
        };
    }, [updateFade, tabs.length]);

    // Keep the active tab in view. Scrolls the strip only — scrollIntoView
    // would also scroll the page and the panel around it.
    useEffect(() => {
        const el = scrollerRef.current;
        const tab = el?.querySelector<HTMLElement>(`[data-tab-id="${activeId}"]`);
        if (!el || !tab) return;
        const margin = 16;
        const left = tab.offsetLeft - margin;
        const right = tab.offsetLeft + tab.offsetWidth + margin - el.clientWidth;
        if (el.scrollLeft > left) el.scrollTo({ left, behavior: "smooth" });
        else if (el.scrollLeft < right) el.scrollTo({ left: right, behavior: "smooth" });
    }, [activeId, tabs.length]);

    // The only tab, still a fresh chat, has nothing to close into.
    const soleBlank = tabs.length === 1 && !tabs[0].title;
    const pendingDelete = closed.find((c) => c.id === pendingDeleteId);

    return (
        <div className="flex h-12 shrink-0 items-center gap-1 border-b border-border/60 pl-3 pr-[calc(0.75rem+var(--panel-tools-inset,0px))] md:h-10">
            <LayoutGroup id="paper-chat-tabs">
                <div
                    ref={scrollerRef}
                    onScroll={updateFade}
                    aria-label="Open chats"
                    className={cn(
                        "flex min-w-0 items-center gap-0.5 overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
                        fade.start && fade.end
                            ? "[mask-image:linear-gradient(to_right,transparent,black_20px,black_calc(100%-20px),transparent)]"
                            : fade.start
                              ? "[mask-image:linear-gradient(to_right,transparent,black_20px)]"
                              : fade.end
                                ? "[mask-image:linear-gradient(to_left,transparent,black_20px)]"
                                : undefined
                    )}
                >
                    <AnimatePresence initial={false}>
                        {tabs.map((conversation) => (
                            <ChatTab
                                key={conversation.id}
                                paperId={paperId}
                                conversation={conversation}
                                active={conversation.id === activeId}
                                closable={!soleBlank}
                                onSelect={onSelect}
                                onClose={onClose}
                                onTurnSettled={onTurnSettled}
                            />
                        ))}
                    </AnimatePresence>
                </div>
            </LayoutGroup>

            <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-9 shrink-0 text-muted-foreground hover:text-foreground md:size-7"
                onClick={onNew}
                aria-label="Start a new chat"
                title="New chat"
            >
                <PlusIcon className="size-4" />
            </Button>

            <div className="flex-1" />

            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-9 shrink-0 text-muted-foreground hover:text-foreground data-[state=open]:bg-accent data-[state=open]:text-foreground md:size-7"
                        aria-label="Chat history"
                        title="Chat history"
                    >
                        <HistoryIcon className="size-4" />
                    </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-72 max-w-[calc(100vw-1rem)] max-h-[60vh] overflow-y-auto">
                    <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                        Other chats for this paper
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {closed.length === 0 ? (
                        <DropdownMenuItem disabled className="text-sm">
                            {tabs.length > 0 ? "Every chat is already open" : "No chats yet"}
                        </DropdownMenuItem>
                    ) : (
                        closed.map((c) => (
                            <DropdownMenuItem
                                key={c.id}
                                onSelect={() => onSelect(c.id)}
                                className="flex items-center gap-2 py-2"
                            >
                                <span className="min-w-0 flex-1">
                                    <span className="block truncate text-sm">{conversationTitle(c)}</span>
                                    <span className="block text-xs text-muted-foreground">
                                        {relativeTime(c.updated_at ?? c.created_at)}
                                    </span>
                                </span>
                                <button
                                    type="button"
                                    className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground/70 hover:bg-destructive/10 hover:text-destructive"
                                    aria-label={`Delete ${conversationTitle(c)}`}
                                    onPointerDown={(e) => e.stopPropagation()}
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
                            {pendingDelete ? `“${conversationTitle(pendingDelete)}” and its` : "This conversation and its"}{" "}
                            messages will be removed. This can&apos;t be undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={() => {
                                const target = pendingDeleteId;
                                setPendingDeleteId(null);
                                if (target) onDelete(target);
                            }}
                        >
                            Delete
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}

interface ChatTabProps {
    paperId: string;
    conversation: ConversationSummary;
    active: boolean;
    closable: boolean;
    onSelect: (conversationId: string) => void;
    onClose: (conversationId: string) => void;
    onTurnSettled: () => void;
}

function ChatTab({ paperId, conversation, active, closable, onSelect, onClose, onTurnSettled }: ChatTabProps) {
    const { id } = conversation;
    const title = conversationTitle(conversation);
    const busy = usePaperChatBusy(paperId, id);

    // A turn finishing in an untitled conversation (in this tab or in the
    // background) is when the server gives it a title.
    const wasBusy = useRef(busy);
    const untitled = !conversation.title;
    useEffect(() => {
        if (wasBusy.current && !busy && untitled) onTurnSettled();
        wasBusy.current = busy;
    }, [busy, untitled, onTurnSettled]);

    return (
        <motion.div
            layout="position"
            data-tab-id={id}
            initial={{ opacity: 0, scale: 0.94 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.94, transition: { duration: 0.12 } }}
            transition={{ duration: 0.2, ease: EASE_OUT_SOFT, layout: { duration: 0.2, ease: EASE_OUT_SOFT } }}
            className={cn(
                "group/tab relative isolate flex h-9 max-w-44 shrink-0 items-center rounded-lg md:h-7",
                !active && "hover:bg-muted dark:hover:bg-accent"
            )}
        >
            {active && (
                <motion.span
                    layoutId="chat-tab-pill"
                    transition={PILL_SPRING}
                    className="absolute inset-0 -z-10 rounded-lg bg-muted ring-1 ring-border/70 ring-inset dark:bg-accent"
                />
            )}
            <button
                type="button"
                aria-current={active ? "true" : undefined}
                title={title}
                onClick={() => onSelect(id)}
                onAuxClick={(e) => {
                    if (e.button === 1 && closable) {
                        e.preventDefault();
                        onClose(id);
                    }
                }}
                className={cn(
                    "flex h-full min-w-0 items-center gap-1.5 rounded-lg pl-2.5 text-[13px] outline-none transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/50",
                    // Touch screens always show the close button, so leave it room;
                    // with a pointer it fades in over the end of the title.
                    closable ? "pr-2.5 [@media(hover:none)]:pr-8" : "pr-2.5",
                    active ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground"
                )}
            >
                {busy && !active && (
                    <Loader2Icon className="size-3 shrink-0 animate-spin text-brand" aria-label="Answering" />
                )}
                <span className="truncate">{title}</span>
            </button>
            {closable && (
                <div className="pointer-events-none absolute inset-y-px right-px flex items-center rounded-r-[7px] bg-linear-to-l from-muted from-60% to-transparent pl-4 pr-1 opacity-0 transition-opacity duration-150 group-hover/tab:opacity-100 group-focus-within/tab:opacity-100 dark:from-accent [@media(hover:none)]:bg-none [@media(hover:none)]:opacity-100">
                    <button
                        type="button"
                        aria-label={`Close ${title}`}
                        title="Close tab"
                        onClick={() => onClose(id)}
                        className="pointer-events-auto flex size-5 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-foreground/10 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 [@media(hover:none)]:size-7"
                    >
                        <XIcon className="size-3" />
                    </button>
                </div>
            )}
        </motion.div>
    );
}
