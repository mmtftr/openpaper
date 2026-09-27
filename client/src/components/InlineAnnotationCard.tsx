"use client";

import { toast } from "sonner";
import { PaperHighlightAnnotation } from "@/lib/schema";
import { cn } from "@/lib/utils";
import { NoteForm } from "@/components/notes/NoteForm";
import { NoteThread, type NoteActions } from "@/components/notes/NoteThread";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";

interface InlineAnnotationCardProps extends NoteActions {
    highlightId: string;
    annotations: PaperHighlightAnnotation[];
    onClose: () => void;
    /** Reports whether an unsaved draft (new note, reply or edit) is in progress. */
    onDirtyChange?: (dirty: boolean) => void;
    widthPx?: number;
    className?: string;
    style?: CSSProperties;
    /** Pinned to the card's top-right corner (e.g. "Open in Annotations"). */
    cornerAction?: ReactNode;
}

/**
 * The note card in the PDF's highlight popover: a composer for a highlight
 * without notes, otherwise its thread (always the active one here). Without
 * `addAnnotation` the card is display-only.
 */
export function InlineAnnotationCard({
    highlightId,
    annotations,
    addAnnotation,
    updateAnnotation,
    removeAnnotation,
    onClose,
    onDirtyChange,
    widthPx = 400,
    className,
    style,
    cornerAction,
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
        } catch {
            toast.error("Couldn't save the note.");
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div
            ref={cardRef}
            data-inline-annotation-card=""
            className={cn(
                "relative flex flex-col overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-xl shadow-black/5 transition-[top,left,background-color,border-color] duration-200 ease-out motion-reduce:transition-none dark:shadow-black/40",
                className
            )}
            style={{ width: widthPx, ...style }}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
        >
            {cornerAction && !isNewThread && (
                <div className="absolute right-2.5 top-2.5 z-10">{cornerAction}</div>
            )}
            {isNewThread ? (
                <div className="flex flex-col gap-2 px-4 pb-3.5 pt-3">
                    <p className="text-xs font-medium text-muted-foreground">New note</p>
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
                            submitLabel="Save note"
                            showCancel
                            ariaLabel="New note"
                            placeholder="Add a note…"
                            // The floating card may not be positioned yet.
                            preventScroll
                        />
                    ) : (
                        <p className="text-sm italic text-muted-foreground">No note yet.</p>
                    )}
                </div>
            ) : (
                <NoteThread
                    variant="card"
                    highlightId={highlightId}
                    notes={annotations}
                    isActive
                    addAnnotation={addAnnotation}
                    updateAnnotation={updateAnnotation}
                    removeAnnotation={removeAnnotation}
                    onDirtyChange={setThreadDirty}
                />
            )}
        </div>
    );
}
