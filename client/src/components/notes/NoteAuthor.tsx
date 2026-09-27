"use client";

import type { ReactNode } from "react";
import { Sparkles } from "lucide-react";
import type { PaperHighlightAnnotation } from "@/lib/schema";
import { cn, formatAnnotationDate } from "@/lib/utils";

export type NoteThreadVariant = "card" | "panel";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "5m", "3h", "yesterday", then a date. */
export function relativeNoteTime(iso: string | undefined, now = Date.now()): string {
    const t = iso ? Date.parse(iso) : NaN;
    if (!Number.isFinite(t)) return "";
    const diff = now - t;
    if (diff < MINUTE) return "just now";
    if (diff < HOUR) return `${Math.floor(diff / MINUTE)}m ago`;
    if (diff < DAY) return `${Math.floor(diff / HOUR)}h ago`;
    const date = new Date(t);
    const today = new Date(now);
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    if (date >= yesterday) return "yesterday";
    return date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        ...(date.getFullYear() !== today.getFullYear() ? { year: "numeric" } : {}),
    });
}

function wasEdited(note: PaperHighlightAnnotation): boolean {
    const created = note.created_at ? Date.parse(note.created_at) : NaN;
    const updated = note.updated_at ? Date.parse(note.updated_at) : NaN;
    return Number.isFinite(created) && Number.isFinite(updated) && updated - created > MINUTE;
}

interface NoteAuthorProps {
    note: PaperHighlightAnnotation;
    /** Trailing actions (edit / delete). */
    children?: ReactNode;
    className?: string;
}

/**
 * The line above one note. Single-user app: the owner's notes are just
 * "You"; AI notes carry the "Open Paper" mark so they stay distinguishable.
 */
export function NoteAuthor({ note, children, className }: NoteAuthorProps) {
    const isAI = note.role === "assistant";
    return (
        <div className={cn("flex min-h-6 items-center gap-1.5 text-xs", className)}>
            {isAI ? (
                <span className="flex items-center gap-1.5 font-medium text-brand">
                    <span className="flex size-5 items-center justify-center rounded-full bg-brand/10">
                        <Sparkles className="size-3" />
                    </span>
                    Open Paper
                </span>
            ) : (
                <span className="font-medium text-foreground">You</span>
            )}
            <span aria-hidden className="text-muted-foreground/60">·</span>
            <time
                dateTime={note.created_at}
                title={formatAnnotationDate(note.created_at ?? "")}
                className="text-muted-foreground"
            >
                {relativeNoteTime(note.created_at)}
            </time>
            {wasEdited(note) && <span className="text-muted-foreground/80">· edited</span>}
            {children}
        </div>
    );
}
