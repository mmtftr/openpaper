"use client";

import { useState } from "react";
import { CheckIcon, MessageSquarePlusIcon, MessagesSquareIcon, Trash2Icon } from "lucide-react";

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
import type { ConversationSummary } from "./useConversationList";

interface ConversationSwitcherProps {
    conversations: ConversationSummary[];
    activeId: string | null;
    onNew: () => void;
    onSelect: (conversationId: string) => void;
    /** Called once the user confirms. */
    onDelete: (conversationId: string) => void;
}

const conversationTitle = (c: ConversationSummary, index: number) =>
    c.title?.trim() || `Chat ${index + 1}`;

/** "New chat", the paper's chat list (switch / delete), and the delete confirmation. */
export function ConversationSwitcher({
    conversations,
    activeId,
    onNew,
    onSelect,
    onDelete,
}: ConversationSwitcherProps) {
    const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

    return (
        <>
            <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-7 text-muted-foreground hover:text-foreground"
                onClick={onNew}
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
                <DropdownMenuContent align="start" className="w-72 max-h-[60vh] overflow-y-auto">
                    <DropdownMenuLabel className="text-xs text-muted-foreground">
                        Chats for this paper
                    </DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {conversations.length === 0 ? (
                        <DropdownMenuItem disabled>No chats yet</DropdownMenuItem>
                    ) : (
                        conversations.map((c, i) => (
                            <DropdownMenuItem
                                key={c.id}
                                onSelect={(e) => {
                                    e.preventDefault();
                                    onSelect(c.id);
                                }}
                                className="flex items-center gap-2"
                            >
                                <span className="flex-1 truncate text-sm">
                                    {conversationTitle(c, i)}
                                </span>
                                {c.id === activeId && (
                                    <CheckIcon className="size-3.5 text-green-500 shrink-0" />
                                )}
                                <button
                                    type="button"
                                    className="text-muted-foreground/70 hover:text-destructive shrink-0 p-0.5"
                                    aria-label={`Delete ${conversationTitle(c, i)}`}
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
                            This conversation and its messages will be removed.
                            This can&apos;t be undone.
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
        </>
    );
}
