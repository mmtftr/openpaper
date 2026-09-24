"use client";

import type { ReactNode } from "react";
import { File, User as UserIcon } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import type { BasicUser } from "@/lib/auth";
import type { PaperHighlightAnnotation } from "@/lib/schema";
import { cn, formatAnnotationDate, getAlphaHashToBackgroundColor, getInitials } from "@/lib/utils";

export type NoteThreadVariant = "card" | "panel";

interface NoteAuthorProps {
    variant: NoteThreadVariant;
    note: PaperHighlightAnnotation;
    user: BasicUser | null;
    /** Trailing actions (edit / delete). */
    children?: ReactNode;
}

/** Avatar, author and date above one note. AI notes are "Open Paper". */
export function NoteAuthor({ variant, note, user, children }: NoteAuthorProps) {
    const isAI = note.role === "assistant";

    if (variant === "panel") {
        return (
            <div className="flex items-center gap-2">
                <div
                    className={cn(
                        "w-8 h-8 rounded-full overflow-hidden flex-shrink-0 flex items-center justify-center",
                        isAI ? "bg-blue-100 dark:bg-blue-900" : "bg-muted"
                    )}
                >
                    {isAI ? (
                        <File size={14} className="text-blue-500" />
                    ) : user?.picture ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={user.picture} alt={user.name ?? undefined} className="w-full h-full object-cover" />
                    ) : (
                        <UserIcon size={14} className="text-muted-foreground" />
                    )}
                </div>
                <span className="text-sm font-medium text-foreground">
                    {isAI ? "Open Paper" : user?.name || "User"}
                </span>
                <span className="text-xs text-muted-foreground">{formatAnnotationDate(note.created_at)}</span>
                {children}
            </div>
        );
    }

    const displayName = user?.name || "Anonymous";
    return (
        <div className="flex items-center gap-3">
            {isAI ? (
                <div className="h-7 w-7 flex-shrink-0 rounded-full flex items-center justify-center bg-blue-100 dark:bg-blue-900">
                    <File size={12} className="text-blue-500" />
                </div>
            ) : (
                <UserAvatar user={user} className="h-7 w-7" fallbackClassName="text-[10px]" />
            )}
            <div className="flex flex-col leading-tight flex-1 min-w-0">
                <span className="text-xs font-medium">{isAI ? "Open Paper" : displayName}</span>
                <span className="text-[11px] text-muted-foreground">{formatAnnotationDate(note.created_at)}</span>
            </div>
            {children}
        </div>
    );
}

/** The note card's avatar: the user's picture, else coloured initials. */
export function UserAvatar({
    user,
    className,
    fallbackClassName,
}: {
    user: BasicUser | null;
    className?: string;
    fallbackClassName?: string;
}) {
    const displayName = user?.name || "Anonymous";
    return (
        <Avatar className={cn("flex-shrink-0", className)}>
            {user?.picture && <AvatarImage src={user.picture} alt={displayName} />}
            <AvatarFallback
                className={cn(
                    "text-white font-medium",
                    user?.name ? getAlphaHashToBackgroundColor(user.name) : "bg-muted",
                    fallbackClassName
                )}
            >
                {getInitials(displayName)}
            </AvatarFallback>
        </Avatar>
    );
}
