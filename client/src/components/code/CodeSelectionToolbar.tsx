"use client";

import {
    CheckIcon,
    CopyIcon,
    MessageCircleIcon,
    SparklesIcon,
} from "lucide-react";

/**
 * Floating affordance over a code selection, mirroring the PDF reader's
 * selection toolbar (`SelectionLayer`): same popover chrome, same "Ask"
 * wording, so attaching code feels like attaching a passage.
 */

export interface CodeSelection {
    /** Selected code, gutter line numbers excluded. */
    text: string;
    startLine: number;
    endLine: number;
    /** Position within the code pane, in px. */
    x: number;
    y: number;
}

interface CodeSelectionToolbarProps {
    selection: CodeSelection;
    /** Undefined when there's no chat composer to attach to. */
    onAttach?: () => void;
    /**
     * Opens the inline, throwaway Q&A over this selection — distinct from
     * "Ask", which hands the snippet to the real conversation.
     */
    onQuickQuestion?: () => void;
    onCopy: () => void;
    /** Set right after attaching, so the button can confirm in place. */
    attached: boolean;
}

export function CodeSelectionToolbar({
    selection,
    onAttach,
    onQuickQuestion,
    onCopy,
    attached,
}: CodeSelectionToolbarProps) {
    return (
        <div
            className="pointer-events-auto absolute z-30 -translate-x-1/2 -translate-y-full pb-1.5"
            style={{ left: selection.x, top: selection.y }}
            // The pane clears the selection on mousedown elsewhere; keep
            // clicks inside the toolbar from counting as "elsewhere".
            onMouseDown={(event) => event.preventDefault()}
        >
            <div className="flex items-center gap-0.5 rounded-xl border border-border bg-popover/95 p-1 shadow-xl backdrop-blur">
                {onAttach && (
                    <button
                        type="button"
                        onClick={onAttach}
                        className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
                    >
                        {attached ? (
                            <>
                                <CheckIcon className="size-3.5 text-green-500" />
                                Added to chat
                            </>
                        ) : (
                            <>
                                <MessageCircleIcon className="size-3.5 text-blue-500" />
                                Ask
                            </>
                        )}
                    </button>
                )}
                {onQuickQuestion && (
                    <button
                        type="button"
                        onClick={onQuickQuestion}
                        className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
                    >
                        <SparklesIcon className="size-3.5 text-amber-500" />
                        Quick question
                    </button>
                )}
                <button
                    type="button"
                    title="Copy"
                    aria-label="Copy selection"
                    onClick={onCopy}
                    className="flex size-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                    <CopyIcon className="size-3.5" />
                </button>
            </div>
        </div>
    );
}
