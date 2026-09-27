"use client";

import { useLayoutEffect, useRef } from "react";
import type { Ref } from "react";
import { ArrowUp, Check } from "lucide-react";
import { cn } from "@/lib/utils";

/** Note fields grow with their content up to `max-h-48`, then scroll. */
const NOTE_TEXTAREA_MAX_PX = 192;

export function autoResizeNoteTextarea(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, NOTE_TEXTAREA_MAX_PX)}px`;
}

const stop = (e: React.SyntheticEvent) => e.stopPropagation();

const isCoarsePointer = () =>
    typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;

interface NoteFormProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    /** Escape, and the Cancel link when `showCancel`. */
    onCancel: () => void;
    saving: boolean;
    /** Accessible name of the send button. */
    submitLabel: string;
    ariaLabel: string;
    placeholder?: string;
    /** `send` (arrow) for new notes and replies, `save` (check) for edits. */
    submitIcon?: "send" | "save";
    /**
     * A caption under the field with a Cancel link (and the key hints on
     * devices with a keyboard) — for edits and new notes, which have no other
     * way out on a phone.
     */
    showCancel?: boolean;
    /**
     * Focus the field when it mounts, caret at the end. `preventScroll` is for
     * a floating card that may not be positioned yet.
     */
    focusOnMount?: boolean;
    preventScroll?: boolean;
    className?: string;
    ref?: Ref<HTMLDivElement>;
}

/**
 * The one note input of every thread — new note, reply and edit: a compact
 * field that grows with its text, with an inline send button. Enter (or
 * Cmd/Ctrl+Enter) submits, Shift+Enter is a newline, Escape cancels. Mouse
 * events stop here so a click inside doesn't also select or collapse the
 * row/card around it.
 */
export function NoteForm({
    value,
    onChange,
    onSubmit,
    onCancel,
    saving,
    submitLabel,
    ariaLabel,
    placeholder,
    submitIcon = "send",
    showCancel = false,
    focusOnMount = true,
    preventScroll = false,
    className,
    ref,
}: NoteFormProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const hasText = value.trim().length > 0;

    useLayoutEffect(() => {
        const el = textareaRef.current;
        if (!el) return;
        autoResizeNoteTextarea(el);
        if (!focusOnMount) return;
        el.focus({ preventScroll });
        const end = el.value.length;
        el.setSelectionRange(end, end);
        // Mount only: re-focusing on every render would steal focus.
    }, []);

    // A draft cleared from outside (sent, cancelled) shrinks back to one line.
    useLayoutEffect(() => {
        if (textareaRef.current) autoResizeNoteTextarea(textareaRef.current);
    }, [value]);

    const SubmitIcon = submitIcon === "save" ? Check : ArrowUp;

    return (
        <div
            ref={ref}
            className={cn("flex min-w-0 flex-col gap-1", className)}
            onMouseDown={stop}
            onClick={stop}
        >
            <div
                className={cn(
                    "group/composer flex items-end gap-1 rounded-2xl border py-1 pl-3 pr-1 transition-[border-color,background-color,box-shadow] duration-150 ease-out-soft md:py-0.5",
                    "border-border/70 bg-muted/40 hover:border-border dark:bg-muted/30",
                    "focus-within:border-brand/60 focus-within:bg-background focus-within:ring-3 focus-within:ring-brand/15 focus-within:hover:border-brand/60 dark:focus-within:bg-background",
                    saving && "opacity-70"
                )}
                onClick={() => textareaRef.current?.focus()}
            >
                <textarea
                    ref={textareaRef}
                    value={value}
                    onChange={(e) => {
                        onChange(e.target.value);
                        autoResizeNoteTextarea(e.target);
                    }}
                    onKeyDown={(e) => {
                        if (e.nativeEvent.isComposing) return;
                        if (e.key === "Enter" && !e.shiftKey) {
                            e.preventDefault();
                            if (hasText && !saving) onSubmit();
                        } else if (e.key === "Escape") {
                            e.preventDefault();
                            onCancel();
                            e.currentTarget.blur();
                        }
                    }}
                    onFocus={(e) => {
                        // Phones: bring the field back above the on-screen keyboard
                        // once it has finished sliding in.
                        if (!isCoarsePointer()) return;
                        const el = e.currentTarget;
                        setTimeout(() => {
                            if (document.activeElement === el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
                        }, 320);
                    }}
                    placeholder={placeholder}
                    aria-label={ariaLabel}
                    aria-busy={saving || undefined}
                    // Not `disabled`: that would drop focus (and the phone keyboard) mid-save.
                    readOnly={saving}
                    enterKeyHint={submitIcon === "save" ? "done" : "send"}
                    rows={1}
                    className="my-1.5 block max-h-48 min-h-6 min-w-0 flex-1 resize-none overflow-y-auto bg-transparent text-base leading-6 text-foreground outline-none placeholder:text-muted-foreground md:my-1 md:text-sm md:leading-5"
                />
                <button
                    type="button"
                    aria-label={submitLabel}
                    title={submitLabel}
                    disabled={!hasText || saving}
                    // Keep focus (and the phone keyboard) in the field.
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={onSubmit}
                    className={cn(
                        "relative mb-0.5 flex size-8 shrink-0 items-center justify-center rounded-full transition-[background-color,color,opacity,transform] duration-150 ease-out-soft md:mb-0.5 md:size-7",
                        // Touch: a ≥ 40px hit area around the smaller disc.
                        "after:absolute after:-inset-1 md:after:hidden",
                        "bg-brand text-brand-foreground hover:bg-brand/90 motion-safe:active:scale-90",
                        "disabled:bg-transparent disabled:text-muted-foreground/60 disabled:active:scale-100",
                        // Empty and unfocused, the field reads as a quiet "Reply…" line.
                        !hasText && "opacity-0 group-focus-within/composer:opacity-100"
                    )}
                >
                    <SubmitIcon className="size-4" strokeWidth={2.25} />
                </button>
            </div>
            {showCancel && (
                <div className="flex items-center justify-between gap-2 px-1 text-xs text-muted-foreground md:text-[11px]">
                    <span className="hidden pointer-fine:inline">
                        Enter to {submitIcon === "save" ? "save" : "send"} · Shift+Enter for a new line
                    </span>
                    <button
                        type="button"
                        onClick={onCancel}
                        disabled={saving}
                        className="-my-2 ml-auto min-h-10 rounded-md px-3 font-medium hover:text-foreground md:-my-1 md:min-h-0 md:px-2 md:py-1"
                    >
                        Cancel
                    </button>
                </div>
            )}
        </div>
    );
}
