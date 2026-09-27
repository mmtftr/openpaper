"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ChevronDown, Pencil, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { CollapsibleNoteText } from "@/components/CollapsibleNoteText";
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
    /**
     * Edit/delete and the reply field show only on the active thread; an
     * inactive one collapses to its first note and drops any reply/edit.
     */
    isActive: boolean;
    /**
     * Panel only: another thread is active. An inactive panel thread keeps
     * its expansion until then (deselecting everything doesn't fold it).
     */
    otherActive?: boolean;
    /**
     * `card`: the note popover over the PDF — an untouched edit collapses when
     * the user clicks elsewhere. `panel`: a row of the Annotations side panel.
     */
    variant: NoteThreadVariant;
    /** Rendered above the notes (the panel's quoted passage). */
    header?: ReactNode;
    /** Whether an unsaved reply or a changed edit is in progress. */
    onDirtyChange?: (dirty: boolean) => void;
}

function createdMs(iso: string | undefined): number {
    const t = iso ? Date.parse(iso) : NaN;
    return Number.isFinite(t) ? t : 0;
}

const stop = (e: React.SyntheticEvent) => e.stopPropagation();

/** Small icon buttons with a ≥ 40px hit area on touch screens. */
const ACTION_CLASS =
    "relative flex size-6 items-center justify-center rounded-md text-muted-foreground transition-colors after:absolute after:-inset-2 md:after:hidden hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40";

/** How long the trash button waits for its confirming second click. */
const DELETE_CONFIRM_MS = 3000;

/**
 * One highlight's notes: the list (collapsed to the first note until the
 * thread is active), in-place edit and delete of the user's own notes, and a
 * reply field. Shared by the PDF note popover and the Annotations panel.
 */
export function NoteThread({
    highlightId,
    notes,
    isActive,
    otherActive = false,
    variant,
    header,
    addAnnotation,
    updateAnnotation,
    removeAnnotation,
    onDirtyChange,
}: NoteThreadProps) {
    const [expanded, setExpanded] = useState(false);
    const [replyDraft, setReplyDraft] = useState("");
    const [editingId, setEditingId] = useState<string | null>(null);
    const [editDraft, setEditDraft] = useState("");
    const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);

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
            setReplyDraft("");
            setEditingId(null);
            setEditDraft("");
            setConfirmDeleteId(null);
            if (variant === "card" || otherActive) setExpanded(false);
        } else if (hasMulti) {
            setExpanded(true);
        }
    }, [isActive, hasMulti, variant, otherActive]);

    // An unconfirmed delete disarms itself.
    useEffect(() => {
        if (!confirmDeleteId) return;
        const timer = setTimeout(() => setConfirmDeleteId(null), DELETE_CONFIRM_MS);
        return () => clearTimeout(timer);
    }, [confirmDeleteId]);

    // Card only: a mousedown outside the edit field cancels an untouched edit.
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

    const saveReply = async () => {
        if (!addAnnotation || !replyDraft.trim() || saving) return;
        setSaving(true);
        try {
            await addAnnotation(highlightId, replyDraft.trim());
            setReplyDraft("");
            setExpanded(true);
        } catch {
            toast.error("Couldn't save the reply.");
        } finally {
            setSaving(false);
        }
    };

    const startEdit = (note: PaperHighlightAnnotation) => {
        editOriginalRef.current = note.content;
        setEditingId(note.id);
        setEditDraft(note.content);
        setConfirmDeleteId(null);
        setExpanded(true);
    };

    const cancelEdit = () => {
        setEditingId(null);
        setEditDraft("");
    };

    const saveEdit = async (noteId: string) => {
        if (!updateAnnotation || !editDraft.trim() || saving) return;
        if (editDraft.trim() === editOriginalRef.current.trim()) {
            cancelEdit();
            return;
        }
        setSaving(true);
        try {
            await updateAnnotation(noteId, editDraft.trim());
            cancelEdit();
        } catch {
            toast.error("Couldn't save the note.");
        } finally {
            setSaving(false);
        }
    };

    const deleteNote = (noteId: string) => {
        // Two clicks: the first arms the button, the second deletes.
        if (confirmDeleteId !== noteId) {
            setConfirmDeleteId(noteId);
            return;
        }
        setConfirmDeleteId(null);
        removeAnnotation?.(noteId);
        if (editingId === noteId) cancelEdit();
    };

    const isCard = variant === "card";
    const showReply = isActive && Boolean(addAnnotation) && !editingId;

    return (
        <>
            <div className={cn("flex flex-col gap-3.5", isCard && "px-4 pt-3.5", isCard && !showReply && "pb-4")}>
                {header}
                {visible.map((note, i) => {
                    const own = note.role !== "assistant";
                    const isEditing = editingId === note.id;
                    const canEdit = own && isActive && Boolean(updateAnnotation);
                    const canDelete = own && isActive && Boolean(removeAnnotation);
                    const confirming = confirmDeleteId === note.id;
                    return (
                        <div key={note.id} className="group/note flex min-w-0 flex-col gap-1">
                            <NoteAuthor
                                note={note}
                                // The card's first line shares its row with the open-in-panel button.
                                className={cn(isCard && i === 0 && "pr-8")}
                            >
                                {(canEdit || canDelete) && !isEditing && (
                                    <div
                                        className={cn(
                                            "ml-auto flex items-center gap-1 transition-opacity duration-150",
                                            // Pointer devices: reveal on hover/focus; touch: always there.
                                            !confirming &&
                                                "pointer-fine:opacity-0 pointer-fine:group-hover/note:opacity-100 pointer-fine:focus-within:opacity-100"
                                        )}
                                        onMouseDown={stop}
                                        onClick={stop}
                                    >
                                        {canEdit && (
                                            <button
                                                type="button"
                                                className={cn(ACTION_CLASS, "hover:text-foreground")}
                                                title="Edit"
                                                aria-label="Edit note"
                                                onClick={() => startEdit(note)}
                                            >
                                                <Pencil className="size-3.5" />
                                            </button>
                                        )}
                                        {canDelete &&
                                            (confirming ? (
                                                <button
                                                    type="button"
                                                    className="relative h-6 rounded-md bg-destructive/10 px-2 text-xs font-medium text-destructive transition-colors after:absolute after:-inset-2 hover:bg-destructive/15 md:after:hidden"
                                                    aria-label="Confirm delete note"
                                                    onClick={() => deleteNote(note.id)}
                                                    onBlur={() => setConfirmDeleteId(null)}
                                                    autoFocus
                                                >
                                                    Delete?
                                                </button>
                                            ) : (
                                                <button
                                                    type="button"
                                                    className={cn(ACTION_CLASS, "hover:text-destructive")}
                                                    title="Delete"
                                                    aria-label="Delete note"
                                                    onClick={() => deleteNote(note.id)}
                                                >
                                                    <Trash2 className="size-3.5" />
                                                </button>
                                            ))}
                                    </div>
                                )}
                            </NoteAuthor>
                            {isEditing ? (
                                <NoteForm
                                    ref={editBlockRef}
                                    className="mt-0.5 w-full"
                                    value={editDraft}
                                    onChange={setEditDraft}
                                    onSubmit={() => void saveEdit(note.id)}
                                    onCancel={cancelEdit}
                                    saving={saving}
                                    submitLabel="Save"
                                    submitIcon="save"
                                    showCancel
                                    ariaLabel="Edit note"
                                />
                            ) : (
                                <CollapsibleNoteText
                                    content={note.content}
                                    isActive={isActive}
                                    paragraphClassName="text-sm leading-relaxed text-foreground whitespace-pre-wrap break-words"
                                />
                            )}
                        </div>
                    );
                })}
                {moreCount > 0 && (
                    <p className="-mt-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
                        <ChevronDown className="size-3.5" />
                        {moreCount} more {moreCount === 1 ? "reply" : "replies"}
                    </p>
                )}
            </div>

            {showReply && (
                <div
                    className={cn(
                        isCard ? "sticky bottom-0 bg-popover px-4 pb-3.5 pt-3" : "mt-3"
                    )}
                    onMouseDown={stop}
                    onClick={stop}
                >
                    <NoteForm
                        value={replyDraft}
                        onChange={setReplyDraft}
                        onSubmit={() => void saveReply()}
                        onCancel={() => setReplyDraft("")}
                        saving={saving}
                        submitLabel="Send reply"
                        ariaLabel="Reply"
                        placeholder="Reply…"
                        focusOnMount={false}
                    />
                </div>
            )}
        </>
    );
}
