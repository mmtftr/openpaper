"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CornerDownLeftIcon, SparklesIcon, SquareIcon, XIcon } from "lucide-react";

import { cn } from "@/lib/utils";
import { renderBlocks } from "@/lib/chatMessages";
import {
    QUICK_QUESTION_MAX_CHARS,
    type CodeQuestionModel,
} from "@/lib/quickQuestionApi";
import { useQuickQuestion } from "@/hooks/useQuickQuestion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader } from "@/components/ai-elements/loader";
import {
    Reasoning,
    ReasoningContent,
    ReasoningTrigger,
} from "@/components/ai-elements/reasoning";
import { Markdown } from "@/components/markdown/Markdown";
import { ToolActivity } from "@/components/chat/ToolActivity";

/**
 * Ephemeral Q&A over one code selection, anchored inside the code viewer.
 *
 * This is deliberately NOT the chat: the question and its answer live only as
 * long as the panel is open, are never written to a conversation, and reset
 * when the viewer closes. It docks to the half of the pane the selection isn't
 * in, so the code being asked about stays visible while the answer streams.
 */

interface QuickQuestionPanelProps {
    paperId: string;
    /** Repo-root-relative path of the file the selection came from. */
    filePath: string;
    startLine: number;
    endLine: number;
    /** Provider/model/effort the chat panel currently has selected. */
    model?: CodeQuestionModel | null;
    /** Which edge of the code pane to dock to — away from the selection. */
    side: "top" | "bottom";
    /** Px of free space on that side, so the panel can't grow over the code. */
    maxHeight: number;
    onClose: () => void;
    /**
     * Escape can't be handled with a local keydown listener when this sits
     * inside the viewer dialog: Radix listens for Escape in the *capture*
     * phase on `document`, so the whole dialog would close first. The dialog
     * lets the panel register an interceptor that runs before it dismisses.
     * The handler returns true when it consumed the key.
     */
    registerEscapeHandler?: (handler: (() => boolean) | null) => void;
}

function rangeLabel(startLine: number, endLine: number): string {
    return startLine === endLine
        ? `line ${startLine}`
        : `lines ${startLine}–${endLine}`;
}

export function QuickQuestionPanel({
    paperId,
    filePath,
    startLine,
    endLine,
    model,
    side,
    maxHeight,
    onClose,
    registerEscapeHandler,
}: QuickQuestionPanelProps) {
    const [draft, setDraft] = useState("");
    const inputRef = useRef<HTMLInputElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    /** Set when submitting from the input, so focus can be handed back. */
    const restoreFocusRef = useRef(false);
    const { state, ask, stop } = useQuickQuestion();
    const streaming = state.status === "streaming";

    // The panel opens because the user asked for it — start them in the box.
    useEffect(() => {
        inputRef.current?.focus();
    }, []);

    // Disabling the input while streaming blurs it and the focus lands
    // wherever the dialog's focus scope puts it, so it has to be handed back
    // once the answer lands or a follow-up needs a click first. Focus the user
    // moved inside the panel (a copy button, the answer text) is left alone.
    useEffect(() => {
        if (streaming || !restoreFocusRef.current) return;
        restoreFocusRef.current = false;
        const active = document.activeElement;
        if (active && active !== document.body && containerRef.current?.contains(active)) {
            return;
        }
        inputRef.current?.focus();
    }, [streaming]);

    useEffect(() => {
        if (!registerEscapeHandler) return;
        registerEscapeHandler(() => {
            onClose();
            return true;
        });
        return () => registerEscapeHandler(null);
    }, [registerEscapeHandler, onClose]);

    const handleSubmit = useCallback(
        (event: React.FormEvent) => {
            event.preventDefault();
            if (streaming || !draft.trim()) return;
            restoreFocusRef.current =
                document.activeElement === inputRef.current;
            void ask(draft, {
                paperId,
                filePath,
                startLine,
                endLine,
                model,
            });
            setDraft("");
        },
        [ask, draft, endLine, filePath, model, paperId, startLine, streaming]
    );

    // A question that failed before any answer arrived goes back in the box,
    // so a quota or ingest error doesn't cost the user their typing.
    const failedOutright =
        state.status === "error" && !state.message && !!state.question;
    useEffect(() => {
        if (!failedOutright) return;
        setDraft((current) => (current ? current : state.question ?? ""));
    }, [failedOutright, state.question]);

    const blocks = useMemo(
        () => (state.message ? renderBlocks(state.message) : []),
        [state.message]
    );
    const hasAnswer = blocks.some(
        (block) => block.kind === "text" && block.text.trim()
    );
    const fileName = filePath.split("/").pop() || filePath;
    const lines = rangeLabel(startLine, endLine);

    return (
        <div
            ref={containerRef}
            className={cn(
                "absolute inset-x-3 z-40 flex flex-col overflow-hidden rounded-xl border border-border bg-popover/95 shadow-xl backdrop-blur",
                side === "top" ? "top-3" : "bottom-3"
            )}
            style={{ maxHeight }}
            // Not `dialog`: it has no focus trap of its own and sits inside the
            // viewer's real dialog, so a second dialog announcement misleads.
            role="region"
            aria-label="Quick question about the selected code"
            onKeyDown={(event) => {
                // Fallback for hosts that don't register the interceptor
                // above; inside the dialog that path wins first.
                if (event.key !== "Escape") return;
                event.preventDefault();
                event.stopPropagation();
                onClose();
            }}
        >
            <div className="flex shrink-0 items-center gap-2 border-b border-border/60 px-3 py-2">
                <SparklesIcon className="size-3.5 shrink-0 text-amber-500" />
                <span className="shrink-0 text-xs font-medium">
                    Quick question
                </span>
                <span
                    className="min-w-0 truncate font-mono text-[11px] text-muted-foreground"
                    title={`${filePath} ${lines}`}
                >
                    {fileName} · {lines}
                </span>
                <span className="ml-auto hidden shrink-0 text-[11px] text-muted-foreground sm:inline">
                    Not saved to your chat
                </span>
                <button
                    type="button"
                    onClick={onClose}
                    aria-label="Close quick question"
                    title="Close"
                    className="shrink-0 rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                    <XIcon className="size-3.5" />
                </button>
            </div>

            <form
                onSubmit={handleSubmit}
                className="flex shrink-0 items-center gap-2 px-3 py-2"
            >
                <Input
                    ref={inputRef}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder={`Ask about ${lines}…`}
                    maxLength={QUICK_QUESTION_MAX_CHARS}
                    disabled={streaming}
                    aria-label="Your question about the selected code"
                    className="h-8 text-xs md:text-xs"
                />
                {streaming ? (
                    <Button
                        type="button"
                        variant="secondary"
                        size="icon"
                        className="size-8 shrink-0"
                        onClick={stop}
                        title="Stop"
                        aria-label="Stop answering"
                    >
                        <SquareIcon className="size-3.5" />
                    </Button>
                ) : (
                    <Button
                        type="submit"
                        size="icon"
                        className="size-8 shrink-0"
                        disabled={!draft.trim()}
                        title="Ask"
                        aria-label="Ask"
                    >
                        <CornerDownLeftIcon className="size-3.5" />
                    </Button>
                )}
            </form>

            {state.question !== null && (
                <div
                    className="min-h-0 flex-1 overflow-y-auto border-t border-border/60 px-3 py-2"
                    aria-live="polite"
                    aria-busy={streaming}
                >
                    <p className="mb-2 text-[11px] break-words text-muted-foreground">
                        {state.question}
                    </p>

                    {blocks.map((block, index) => {
                        if (block.kind === "reasoning") {
                            return (
                                <Reasoning
                                    key={`reasoning-${index}`}
                                    isStreaming={streaming && block.streaming}
                                    defaultOpen={false}
                                    className="mb-2"
                                >
                                    <ReasoningTrigger />
                                    <ReasoningContent>
                                        {block.text}
                                    </ReasoningContent>
                                </Reasoning>
                            );
                        }
                        if (block.kind === "tool") {
                            // The answer may look things up elsewhere in the
                            // repo; each lookup gets the same compact row the
                            // chat panel uses.
                            return (
                                <ToolActivity
                                    key={`tool-${block.part.toolCallId ?? index}`}
                                    part={block.part}
                                />
                            );
                        }
                        return (
                            <div
                                key={`text-${index}`}
                                className="prose dark:prose-invert prose-sm !max-w-none text-xs"
                            >
                                <Markdown
                                    streaming={
                                        streaming && index === blocks.length - 1
                                    }
                                >
                                    {block.text}
                                </Markdown>
                            </div>
                        );
                    })}

                    {streaming && !hasAnswer && (
                        <div className="flex items-center gap-2 py-1 text-xs text-muted-foreground">
                            <Loader size={14} />
                            <span>Thinking…</span>
                        </div>
                    )}

                    {state.error && (
                        <p className="mt-2 text-xs text-destructive">
                            {state.error}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
