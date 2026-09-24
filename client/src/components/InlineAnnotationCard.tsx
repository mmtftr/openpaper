"use client";

import { Button } from "@/components/ui/button";
import { BasicUser } from "@/lib/auth";
import { PaperHighlightAnnotation } from "@/lib/schema";
import { cn } from "@/lib/utils";
import { NoteForm } from "@/components/notes/NoteForm";
import { NoteThread, type NoteActions } from "@/components/notes/NoteThread";
import { UserAvatar } from "@/components/notes/NoteAuthor";
import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

interface InlineAnnotationCardProps extends NoteActions {
    highlightId: string;
    annotations: PaperHighlightAnnotation[];
    user: BasicUser | null;
    onClose: () => void;
    /** Reports whether an unsaved draft (new note, reply or edit) is in progress. */
    onDirtyChange?: (dirty: boolean) => void;
    widthPx?: number;
    className?: string;
    style?: CSSProperties;
    footer?: ReactNode;
}

/**
 * The note card in the PDF's highlight popover: a composer for a highlight
 * without notes, otherwise its thread (always the active one here). Without
 * `addAnnotation` the card is display-only.
 */
export function InlineAnnotationCard({
    highlightId,
    annotations,
    user,
    addAnnotation,
    updateAnnotation,
    removeAnnotation,
    onClose,
    onDirtyChange,
    widthPx = 280,
    className,
    style,
    footer,
}: InlineAnnotationCardProps) {
    const isNewThread = annotations.length === 0;
    const canWrite = Boolean(addAnnotation);

    const [newContent, setNewContent] = useState("");
    const [isSaving, setIsSaving] = useState(false);
    const [threadDirty, setThreadDirty] = useState(false);
    const cardRef = useRef<HTMLDivElement>(null);

    const isDirty = Boolean(newContent.trim()) || (!isNewThread && threadDirty);
    useEffect(() => {
        onDirtyChange?.(isDirty);
    }, [isDirty, onDirtyChange]);

    // A new note that's still empty closes on a click outside the card.
    useEffect(() => {
        if (!isNewThread || newContent.trim()) return;
        const handleOutsideClick = (e: MouseEvent) => {
            if (cardRef.current && !cardRef.current.contains(e.target as Node)) onClose();
        };
        const timerId = setTimeout(() => {
            document.addEventListener("mousedown", handleOutsideClick);
        }, 100);
        return () => {
            clearTimeout(timerId);
            document.removeEventListener("mousedown", handleOutsideClick);
        };
    }, [onClose, newContent, isNewThread]);

    const handleSaveNew = async () => {
        if (!newContent.trim() || isSaving || !addAnnotation) return;
        setIsSaving(true);
        try {
            await addAnnotation(highlightId, newContent.trim());
            setNewContent("");
        } finally {
            setIsSaving(false);
        }
    };

    const displayName = user?.name || "Anonymous";

    return (
        <div
            ref={cardRef}
            data-inline-annotation-card=""
            className={cn(
                "relative rounded-xl shadow-lg flex flex-col transition-[top,left,background-color,border-color] duration-200 ease-out motion-reduce:transition-none overflow-hidden border border-border bg-background",
                className
            )}
            style={{ width: widthPx, ...style }}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
        >
            {isNewThread ? (
                <div className="p-4 flex flex-col gap-3">
                    <div className="flex items-center gap-3">
                        <UserAvatar user={user} className="h-9 w-9" fallbackClassName="text-xs" />
                        <div className="flex flex-col leading-tight flex-1 min-w-0">
                            <span className="text-sm font-medium">{displayName}</span>
                            <span className="text-xs text-muted-foreground">Just now</span>
                        </div>
                        {canWrite && (
                            <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6 text-muted-foreground hover:text-foreground flex-shrink-0"
                                onClick={onClose}
                                disabled={isSaving}
                                title="Close"
                            >
                                <X size={14} />
                            </Button>
                        )}
                    </div>
                    {canWrite ? (
                        <NoteForm
                            value={newContent}
                            onChange={setNewContent}
                            onSubmit={() => void handleSaveNew()}
                            // Closing mid-save could discard the highlight the note is landing on.
                            onCancel={() => {
                                if (!isSaving) onClose();
                            }}
                            saving={isSaving}
                            submitLabel="Save"
                            ariaLabel="New annotation"
                            placeholder="Write your notes here…"
                            // The floating card may not be positioned yet.
                            preventScroll
                        />
                    ) : (
                        <p className="text-sm text-muted-foreground italic">No annotation yet.</p>
                    )}
                </div>
            ) : (
                <NoteThread
                    variant="card"
                    highlightId={highlightId}
                    notes={annotations}
                    user={user}
                    isActive
                    addAnnotation={addAnnotation}
                    updateAnnotation={updateAnnotation}
                    removeAnnotation={removeAnnotation}
                    onDirtyChange={setThreadDirty}
                />
            )}
            {footer}
        </div>
    );
}
