"use client";

import { useLayoutEffect, useRef } from "react";
import type { Ref } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Note fields grow with their content up to `max-h-48`, then scroll. */
const NOTE_TEXTAREA_MAX_PX = 192;

export function autoResizeNoteTextarea(el: HTMLTextAreaElement) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, NOTE_TEXTAREA_MAX_PX)}px`;
}

const TEXTAREA_CLASS =
    "text-sm text-foreground placeholder:text-muted-foreground resize-none w-full min-h-[4rem] max-h-48 px-3 py-2 overflow-y-auto overflow-x-hidden box-border rounded-md border border-black bg-background focus:outline-none focus:ring-0 focus:border-black dark:border-white dark:focus:border-white";

const stop = (e: React.SyntheticEvent) => e.stopPropagation();

interface NoteFormProps {
    value: string;
    onChange: (value: string) => void;
    onSubmit: () => void;
    /** Cancel button and Escape. */
    onCancel: () => void;
    saving: boolean;
    submitLabel: string;
    ariaLabel: string;
    placeholder?: string;
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
 * A note textarea with Cancel / submit underneath — the new-note, reply and
 * edit fields of every note thread. Enter submits, Shift+Enter is a newline,
 * Escape cancels. Mouse events stop here so a click inside the form doesn't
 * also select or collapse the row/card around it.
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
    focusOnMount = true,
    preventScroll = false,
    className,
    ref,
}: NoteFormProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);

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

    return (
        <div
            ref={ref}
            className={cn("flex flex-col gap-2", className)}
            onMouseDown={stop}
            onClick={stop}
        >
            <textarea
                ref={textareaRef}
                value={value}
                onChange={(e) => {
                    onChange(e.target.value);
                    autoResizeNoteTextarea(e.target);
                }}
                onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        onSubmit();
                    } else if (e.key === "Escape") {
                        e.preventDefault();
                        onCancel();
                    }
                }}
                placeholder={placeholder}
                aria-label={ariaLabel}
                className={TEXTAREA_CLASS}
                disabled={saving}
                rows={3}
            />
            <div className="flex items-center justify-end gap-2">
                <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 px-2 text-xs text-muted-foreground"
                    onClick={onCancel}
                    disabled={saving}
                >
                    Cancel
                </Button>
                <Button
                    type="button"
                    size="sm"
                    className="h-7 px-3 text-xs"
                    onClick={onSubmit}
                    disabled={!value.trim() || saving}
                >
                    {submitLabel}
                </Button>
            </div>
        </div>
    );
}
