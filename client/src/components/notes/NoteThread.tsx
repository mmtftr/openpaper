"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Pencil, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CollapsibleNoteText } from "@/components/CollapsibleNoteText";
import type { BasicUser } from "@/lib/auth";
import type { PaperHighlightAnnotation } from "@/lib/schema";
import { cn } from "@/lib/utils";
import { NoteForm } from "./NoteForm";
import { NoteAuthor, type NoteThreadVariant } from "./NoteAuthor";

export type { NoteThreadVariant };

export interface NoteActions {
    /** Omit (with the other two) for a read-only thread. */
    addAnnotation?: (highlightId: string, content: string) => Promise<PaperHighlightAnnotation>;
    updateAnnotation?: (annotationId: string, content: string) => Promise<unknown> | void;
    removeAnnotation?: (annotationId: string) => void;
}

interface NoteThreadProps extends NoteActions {
    highlightId: string;
    /** The thread's notes, any order: shown oldest first. */
    notes: PaperHighlightAnnotation[];
    user: BasicUser | null;
    /**
     * Edit/delete and the reply composer show only on the active thread; an
     * inactive one collapses to its first note and drops any open reply/edit.
     */
    isActive: boolean;
    /**
     * `card`: the note popover over the PDF — an edit or reply collapses when
     * the user clicks elsewhere. `panel`: a row of the Annotations side panel.
     */
    variant: NoteThreadVariant;
    /** Rendered above the notes (the panel's quoted passage). */
    header?: ReactNode;
    /** Whether an unsaved reply or a changed edit is in progress. */
    onDirtyChange?: (dirty: boolean) => void;
}

const LIST_CLASS: Record<NoteThreadVariant, string> = {
    card: "px-4 pt-4 flex flex-col gap-3",
    panel: "flex flex-col gap-3",
};
const REPLY_SECTION_CLASS: Record<NoteThreadVariant, string> = {
    card: "px-4 pb-4 pt-0",
    panel: "mt-2 pt-0",
};

function createdMs(iso: string | undefined): number {
    const t = iso ? Date.parse(iso) : NaN;
    return Number.isFinite(t) ? t : 0;
}

const stop = (e: React.SyntheticEvent) => e.stopPropagation();

/**
 * One highlight's notes: the list (collapsed to the first note until the
 * thread is active), in-place edit and delete of the user's own notes, and a
 * reply composer. Shared by the PDF note popover and the Annotations panel.
 */
export function NoteThread({
    highlightId,
    notes,
    user,
    isActive,
    variant,
    header,
    addAnnotation,
    updateAnnotation,
    removeAnnotation,
    onDirtyChange,
}: NoteThreadProps) {
    const [expanded, setExpanded] = useState(false);
    const [replyOpen, setReplyOpen] = useState(false);
    const [replyDraft, setReplyDraft] = useState("");
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editDraft, setEditDraft] = useState("");
    const [saving, setSaving] = useState(false);

    const replySectionRef = useRef<HTMLDivElement>(null);
    const editBlockRef = useRef<HTMLDivElement>(null);
    /** Saved text of the note being edited — an edit only counts as a draft once it differs. */
    const editOriginalRef = useRef("");

    const sorted = useMemo(
        () => [...notes].sort((a, b) => createdMs(a.created_at) - createdMs(b.created_at)),
        [notes]
    );
    const hasMulti = sorted.length > 1;
    const visible = !hasMulti || expanded ? sorted : sorted.slice(0, 1);
    const moreCount = hasMulti && !expanded ? sorted.length - 1 : 0;

    const editChanged = Boolean(editingId) && editDraft.trim() !== editOriginalRef.current.trim();
    const editChangedRef = useRef(editChanged);
    editChangedRef.current = editChanged;
    const isDirty = Boolean(replyDraft.trim() || editChanged);
    useEffect(() => {
        onDirtyChange?.(isDirty);
    }, [isDirty, onDirtyChange]);

    // Inactive: drop reply/edit and collapse. Active: show the whole thread.
    useEffect(() => {
        if (!isActive) {
            setReplyOpen(false);
            setReplyDraft("");
            setEditingId(null);
            setEditDraft("");
            setExpanded(false);
        } else if (hasMulti) {
            setExpanded(true);
        }
    }, [isActive, hasMulti]);

    // Card only: a mousedown outside the edit field cancels an untouched edit,
    // and one outside the reply row collapses it to the pill (draft kept).
    // Capture phase: the card stops propagation of its own mouse events.
    const collapseOnOutsideClick = variant === "card";
    useEffect(() => {
        if (!collapseOnOutsideClick || !editingId) return;
        const onMouseDown = (e: MouseEvent) => {
            const el = editBlockRef.current;
            if (el && !el.contains(e.target as Node) && !editChangedRef.current) {
                setEditingId(null);
            }
        };
        document.addEventListener("mousedown", onMouseDown, true);
        return () => document.removeEventListener("mousedown", onMouseDown, true);
    }, [collapseOnOutsideClick, editingId]);
    useEffect(() => {
        if (!collapseOnOutsideClick || !replyOpen) return;
        const onMouseDown = (e: MouseEvent) => {
            const el = replySectionRef.current;
            if (el && !el.contains(e.target as Node)) setReplyOpen(false);
        };
        document.addEventListener("mousedown", onMouseDown, true);
        return () => document.removeEventListener("mousedown", onMouseDown, true);
    }, [collapseOnOutsideClick, replyOpen]);

    const saveReply = async () => {
        if (!addAnnotation || !replyDraft.trim() || saving) return;
        setSaving(true);
        try {
            await addAnnotation(highlightId, replyDraft.trim());
            setReplyDraft("");
            setReplyOpen(false);
            setExpanded(true);
        } finally {
            setSaving(false);
        }
    };

    const startEdit = (note: PaperHighlightAnnotation) => {
        editOriginalRef.current = note.content;
        setEditingId(note.id);
        setEditDraft(note.content);
        // One bordered field at a time.
        setReplyOpen(false);
        setExpanded(true);
    };

    const cancelEdit = () => {
        setEditingId(null);
        setEditDraft("");
    };

    const saveEdit = async (noteId: string) => {
        if (!updateAnnotation || !editDraft.trim() || saving) return;
        setSaving(true);
        try {
            await updateAnnotation(noteId, editDraft.trim());
            cancelEdit();
        } finally {
            setSaving(false);
        }
    };

    const deleteNote = (noteId: string) => {
        // No confirm: a note is one click to re-type.
        removeAnnotation?.(noteId);
        if (editingId === noteId) cancelEdit();
    };

    const indent = variant === "panel" ? "pl-10" : undefined;

    return (
        <>
            <div className={cn(LIST_CLASS[variant], variant === "card" && (addAnnotation && isActive ? "pb-2" : "pb-4"))}>
                {header}
                {visible.map((note) => {
                    const own = note.role !== "assistant";
                    const isEditing = editingId === note.id;
                    const canEdit = own && isActive && Boolean(updateAnnotation);
                    const canDelete = own && isActive && Boolean(removeAnnotation);
                    return (
                        <div key={note.id} className="flex flex-col gap-2">
                            <NoteAuthor variant={variant} note={note} user={user}>
                                {(canEdit || canDelete) && !isEditing && (
                                    <div
                                        className={cn(
                                            "flex items-center gap-0.5",
                                            variant === "panel" ? "ml-auto" : "flex-shrink-0"
                                        )}
                                        onMouseDown={stop}
                                        onClick={stop}
                                    >
                                        {canEdit && (
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="icon"
                                                className="h-6 w-6 text-muted-foreground hover:text-foreground"
                                                title="Edit"
                                                aria-label="Edit annotation"
                                                onClick={() => startEdit(note)}
                                            >
                                                <Pencil size={12} />
                                            </Button>
                                        )}
                                        {canDelete && (
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="icon"
                                                className="h-6 w-6 text-muted-foreground hover:text-destructive"
                                                title="Delete"
                                                aria-label="Delete annotation"
                                                onClick={() => deleteNote(note.id)}
                                            >
                                                <Trash2 size={12} />
                                            </Button>
                                        )}
                                    </div>
                                )}
                            </NoteAuthor>
                            <div className={indent}>
                                {isEditing ? (
                                    <NoteForm
                                        ref={editBlockRef}
                                        className="w-full min-w-0"
                                        value={editDraft}
                                        onChange={setEditDraft}
                                        onSubmit={() => void saveEdit(note.id)}
                                        onCancel={cancelEdit}
                                        saving={saving}
                                        submitLabel="Save"
                                        ariaLabel="Edit annotation"
                                    />
                                ) : (
                                    <CollapsibleNoteText
                                        content={note.content}
                                        isActive={isActive}
                                        paragraphClassName={
                                            variant === "panel"
                                                ? "text-sm text-foreground leading-snug whitespace-pre-wrap break-words"
                                                : undefined
                                        }
                                    />
                                )}
                            </div>
                        </div>
                    );
                })}
                {moreCount > 0 && (
                    <p className={cn("text-xs text-muted-foreground", indent)}>
                        +{moreCount} more {moreCount === 1 ? "reply" : "replies"} — click to show
                    </p>
                )}
            </div>

            {isActive && addAnnotation && (
                <div
                    ref={replySectionRef}
                    className={REPLY_SECTION_CLASS[variant]}
                    onMouseDown={stop}
                    onClick={stop}
                >
                    {replyOpen ? (
                        <NoteForm
                            value={replyDraft}
                            onChange={setReplyDraft}
                            onSubmit={() => void saveReply()}
                            onCancel={() => {
                                setReplyDraft("");
                                setReplyOpen(false);
                            }}
                            saving={saving}
                            submitLabel="Reply"
                            ariaLabel="Reply"
                            placeholder="Write a reply…"
                        />
                    ) : (
                        <button
                            type="button"
                            className="w-full text-left text-sm text-muted-foreground rounded-full border border-border px-3 py-1.5 hover:bg-muted/50 transition-colors cursor-text"
                            onClick={() => setReplyOpen(true)}
                        >
                            Reply…
                        </button>
                    )}
                </div>
            )}
        </>
    );
}
