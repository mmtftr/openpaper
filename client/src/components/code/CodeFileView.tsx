"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon, ExternalLinkIcon } from "lucide-react";

import {
    getRepoFileCached,
    formatBytes,
    isRepoApiStatus,
    PLAIN_TEXT_BYTE_THRESHOLD,
    type RepoFile,
} from "@/lib/repoApi";
import { languageFromPath, type CodeLine } from "@/lib/shiki";
import { useHighlightedLines } from "@/hooks/useHighlightedLines";
import { formatCodeReference } from "@/lib/userReferences";
import { Button } from "@/components/ui/button";
import { Loader } from "@/components/ai-elements/loader";
import { CodeLines, type LineRange } from "@/components/code/CodeLines";
import {
    CodeSelectionToolbar,
    type CodeSelection,
} from "@/components/code/CodeSelectionToolbar";
import { QuickQuestionPanel } from "@/components/code/QuickQuestionPanel";
import { useCodeViewer } from "@/components/code/CodeViewerProvider";
import type { CodeQuestionModel } from "@/lib/quickQuestionApi";

/**
 * Right pane of the code viewer: one file from the ingested snapshot, with
 * line numbers, syntax highlighting, and an optional highlighted line range
 * that the view scrolls to.
 */

export type { LineRange };

/** The `[data-line]` row a selection boundary sits in, if any. */
function rowElementOf(node: Node): HTMLElement | null {
    const element =
        node.nodeType === Node.ELEMENT_NODE
            ? (node as HTMLElement)
            : node.parentElement;
    return element?.closest<HTMLElement>("[data-line]") ?? null;
}

/** Lines rendered before the view truncates (DOM-node budget, not bytes). */
const MAX_RENDERED_LINES = 5000;
/** Extra lines kept after a cited range that sits past the render limit. */
const RANGE_CONTEXT_LINES = 200;

/** An open quick-question panel: which lines it asks about, and where it sits. */
interface QuickQuestionAnchor {
    startLine: number;
    endLine: number;
    /** Docked to the pane edge opposite the selection, never over it. */
    side: "top" | "bottom";
    /** Free px on that side, so a long answer can't grow over the code. */
    maxHeight: number;
}

/** Below this the panel is too small to read an answer in; overlap is better. */
const QUICK_QUESTION_MIN_HEIGHT = 180;
/** Inset between the panel and the pane edge, top and bottom. */
const QUICK_QUESTION_INSET = 24;

interface CodeFileViewProps {
    paperId: string;
    path: string;
    /** Cited range to highlight and scroll to, if any. */
    range: LineRange | null;
    /** Bumped by the caller to re-run the scroll for a repeated request. */
    scrollToken: number;
    /**
     * Whether the viewer is open. The dialog keeps its content mounted through
     * the close animation, so this is what actually ends a quick question.
     */
    viewerOpen?: boolean;
    /** Model the chat panel has selected, forwarded to quick questions. */
    chatModel?: CodeQuestionModel | null;
    /** Lets the quick-question panel take Escape before the dialog closes. */
    registerEscapeHandler?: (handler: (() => boolean) | null) => void;
}

export function CodeFileView({
    paperId,
    path,
    range,
    scrollToken,
    viewerOpen = true,
    chatModel,
    registerEscapeHandler,
}: CodeFileViewProps) {
    const [file, setFile] = useState<RepoFile | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [copied, setCopied] = useState(false);
    const scrollRef = useRef<HTMLDivElement>(null);
    const paneRef = useRef<HTMLDivElement>(null);
    const { attachReference } = useCodeViewer();
    const [selection, setSelection] = useState<CodeSelection | null>(null);
    const [attached, setAttached] = useState(false);
    const [quickQuestion, setQuickQuestion] =
        useState<QuickQuestionAnchor | null>(null);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        setFile(null);
        getRepoFileCached(paperId, path)
            .then((result) => {
                if (!cancelled) setFile(result);
            })
            .catch((err: unknown) => {
                if (cancelled) return;
                if (isRepoApiStatus(err, 404)) {
                    setError(`“${path}” isn't in this repo snapshot.`);
                    return;
                }
                const message =
                    err instanceof Error ? err.message : "Unknown error";
                setError(`Could not load ${path}: ${message}`);
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [paperId, path]);

    // Big files skip shiki entirely — tokenizing hundreds of KB blocks the
    // main thread for longer than the highlighting is worth.
    const plainOnly =
        !!file && (file.size ?? 0) > PLAIN_TEXT_BYTE_THRESHOLD;

    useEffect(() => {
        if (!copied) return;
        const timer = setTimeout(() => setCopied(false), 2000);
        return () => clearTimeout(timer);
    }, [copied]);

    // Highlighted tokens when they're ready, raw text until then.
    const allRows: Array<CodeLine | string> = useHighlightedLines(
        file?.content ?? "",
        languageFromPath(path),
        !!file && !plainOnly
    );
    const lineCount = allRows.length;
    // Rendering every line of a very large file costs tens of thousands of DOM
    // nodes; render a prefix, extended when the cited range sits past it.
    const renderLimit = range
        ? Math.max(MAX_RENDERED_LINES, range.end + RANGE_CONTEXT_LINES)
        : MAX_RENDERED_LINES;
    const rows =
        allRows.length > renderLimit ? allRows.slice(0, renderLimit) : allRows;
    const truncatedRows = rows.length < allRows.length;

    // While a quick question is open its lines carry the emphasis, so the code
    // being asked about stays marked once the text selection is gone.
    const highlightRange: LineRange | null = quickQuestion
        ? { start: quickQuestion.startLine, end: quickQuestion.endLine }
        : range;

    // Scroll the cited range into view once the content it points at exists.
    useEffect(() => {
        if (!range || loading || !file) return;
        const container = scrollRef.current;
        if (!container) return;
        const target = container.querySelector<HTMLElement>(
            `[data-line="${range.start}"]`
        );
        if (!target) return;
        const frame = requestAnimationFrame(() => {
            // Rect-relative rather than offsetTop: the row's offsetParent is
            // whatever happens to be positioned above it, which isn't
            // necessarily this scroll container.
            const top =
                target.getBoundingClientRect().top -
                container.getBoundingClientRect().top +
                container.scrollTop -
                container.clientHeight / 3;
            container.scrollTo({ top: Math.max(top, 0), behavior: "smooth" });
        });
        return () => cancelAnimationFrame(frame);
        // `allRows` is a dep so the scroll re-runs once highlighting swaps the
        // rows (row heights can shift when tokens replace raw text).
    }, [range, loading, file, allRows, scrollToken]);

    // Read the current text selection as a line range plus its code text.
    // The text is rebuilt from the rows rather than taken from
    // `Selection.toString()`, so gutter numbers can never leak in and a
    // partial first/last line still yields valid, whole-line code.
    const readSelection = useCallback(() => {
        const pane = paneRef.current;
        const scroller = scrollRef.current;
        if (!pane || !scroller) return;

        const active = window.getSelection();
        if (!active || active.isCollapsed || active.rangeCount === 0) {
            setSelection(null);
            return;
        }
        const domRange = active.getRangeAt(0);
        if (!scroller.contains(domRange.commonAncestorContainer)) {
            setSelection(null);
            return;
        }
        const startRow = rowElementOf(domRange.startContainer);
        const endRow = rowElementOf(domRange.endContainer);
        if (!startRow || !endRow) {
            setSelection(null);
            return;
        }
        const startLine = Number(startRow.dataset.line);
        const endLine = Number(endRow.dataset.line);
        if (!Number.isFinite(startLine) || !Number.isFinite(endLine)) {
            setSelection(null);
            return;
        }

        const lines: string[] = [];
        for (let line = startLine; line <= endLine; line++) {
            const row = scroller.querySelector<HTMLElement>(
                `[data-line="${line}"]`
            );
            // The code sits in the row's last child; the gutter, when shown,
            // is the first.
            lines.push(row?.lastElementChild?.textContent ?? "");
        }
        const text = lines.join("\n");
        if (!text.trim()) {
            setSelection(null);
            return;
        }

        const rect = domRange.getBoundingClientRect();
        const paneRect = pane.getBoundingClientRect();
        setSelection({
            text,
            startLine,
            endLine,
            x: rect.left + rect.width / 2 - paneRect.left,
            // Keep the toolbar inside the pane when selecting the top line.
            y: Math.max(rect.top - paneRect.top, 32),
        });
        setAttached(false);
    }, []);

    // A new file (or a scroll that moves the selection away) drops the
    // affordance rather than leaving it floating over unrelated code. An open
    // quick question goes with it — its answer is about the old file.
    useEffect(() => {
        setSelection(null);
        setAttached(false);
        setQuickQuestion(null);
    }, [path]);

    // Closing the viewer ends the question immediately — unmounting the panel
    // aborts its request — rather than letting it stream on through the exit
    // animation, or survive into the next open if that animation is cancelled.
    useEffect(() => {
        if (viewerOpen) return;
        setSelection(null);
        setQuickQuestion(null);
    }, [viewerOpen]);

    useEffect(() => {
        if (!attached) return;
        const timer = setTimeout(() => {
            setAttached(false);
            setSelection(null);
        }, 1200);
        return () => clearTimeout(timer);
    }, [attached]);

    const handleAttach = () => {
        if (!attachReference || !selection) return;
        attachReference(
            formatCodeReference(
                path,
                selection.startLine,
                selection.endLine,
                selection.text
            )
        );
        setAttached(true);
    };

    // Same line range select-to-attach derives, kept as an anchor so the
    // panel survives the selection being cleared.
    const handleQuickQuestion = () => {
        if (!selection) return;
        const pane = paneRef.current;
        const scroller = scrollRef.current;
        const paneRect = pane?.getBoundingClientRect();
        const paneHeight = paneRect?.height ?? 0;

        // Measure the selection's whole extent, not just where the toolbar
        // sat: docking has to clear the last selected line too.
        const endRow = scroller?.querySelector<HTMLElement>(
            `[data-line="${selection.endLine}"]`
        );
        const selectionBottom =
            endRow && paneRect
                ? endRow.getBoundingClientRect().bottom - paneRect.top
                : selection.y;
        const spaceAbove = Math.max(selection.y, 0);
        const spaceBelow = Math.max(paneHeight - selectionBottom, 0);
        // Dock into whichever side has more room, and never grow past it.
        const side = spaceAbove > spaceBelow ? "top" : "bottom";
        const free = (side === "top" ? spaceAbove : spaceBelow) -
            QUICK_QUESTION_INSET;
        const cap = Math.max(
            paneHeight * 0.55,
            QUICK_QUESTION_MIN_HEIGHT
        );

        setQuickQuestion({
            startLine: selection.startLine,
            endLine: selection.endLine,
            side,
            maxHeight: Math.min(
                Math.max(free, QUICK_QUESTION_MIN_HEIGHT),
                cap
            ),
        });
        setSelection(null);
        setAttached(false);
    };

    const handleCloseQuickQuestion = useCallback(
        () => setQuickQuestion(null),
        []
    );

    const handleCopySelection = () => {
        if (!selection) return;
        navigator.clipboard.writeText(selection.text).catch((err) => {
            console.error("Failed to copy selection:", err);
        });
        setSelection(null);
    };

    const handleCopy = async () => {
        if (!file) return;
        try {
            await navigator.clipboard.writeText(file.content);
            setCopied(true);
        } catch (err) {
            console.error("Failed to copy file contents:", err);
        }
    };

    return (
        <div
            ref={paneRef}
            className="relative flex h-full min-h-0 min-w-0 flex-col"
        >
            <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2">
                <span
                    className="min-w-0 flex-1 truncate font-mono text-xs"
                    title={path}
                >
                    {path}
                </span>
                {file && (
                    <span className="shrink-0 text-[11px] text-muted-foreground tabular-nums">
                        {lineCount} lines · {formatBytes(file.size)}
                    </span>
                )}
                <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7 text-muted-foreground hover:text-foreground"
                    onClick={handleCopy}
                    disabled={!file}
                    title="Copy file contents"
                    aria-label="Copy file contents"
                >
                    {copied ? (
                        <CheckIcon className="size-3.5 text-green-500" />
                    ) : (
                        <CopyIcon className="size-3.5" />
                    )}
                </Button>
                {file?.github_url && (
                    <Button
                        asChild
                        variant="ghost"
                        size="icon"
                        className="size-7 text-muted-foreground hover:text-foreground"
                        title="Open on GitHub"
                    >
                        <a
                            href={file.github_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label="Open on GitHub"
                        >
                            <ExternalLinkIcon className="size-3.5" />
                        </a>
                    </Button>
                )}
            </div>

            {plainOnly && (
                <p className="border-b border-border/60 bg-muted/40 px-3 py-1.5 text-[11px] text-muted-foreground">
                    Large file — shown without syntax highlighting.
                </p>
            )}
            {truncatedRows && (
                <p className="border-b border-border/60 bg-muted/40 px-3 py-1.5 text-[11px] text-muted-foreground">
                    Showing the first {rows.length} of {lineCount} lines — use
                    “Open on GitHub” or copy the file for the rest.
                </p>
            )}

            <div
                ref={scrollRef}
                className="min-h-0 flex-1 overflow-auto"
                onMouseUp={readSelection}
                onKeyUp={readSelection}
                onScroll={() => {
                    if (selection) setSelection(null);
                }}
            >
                {loading ? (
                    <div className="flex items-center gap-2 p-4 text-xs text-muted-foreground">
                        <Loader size={14} />
                        <span>Loading {path}…</span>
                    </div>
                ) : error ? (
                    <p className="p-4 text-xs text-destructive">{error}</p>
                ) : (
                    <CodeLines rows={rows} range={highlightRange} />
                )}
            </div>

            {selection && (
                <CodeSelectionToolbar
                    selection={selection}
                    onAttach={attachReference ? handleAttach : undefined}
                    onQuickQuestion={handleQuickQuestion}
                    onCopy={handleCopySelection}
                    attached={attached}
                />
            )}

            {quickQuestion && (
                <QuickQuestionPanel
                    // A new selection is a new question: remounting drops the
                    // previous answer instead of showing it under new lines.
                    // The path is in the key so the same range in a different
                    // file can't inherit the old answer for a frame.
                    key={`${path}:${quickQuestion.startLine}-${quickQuestion.endLine}`}
                    paperId={paperId}
                    filePath={path}
                    startLine={quickQuestion.startLine}
                    endLine={quickQuestion.endLine}
                    model={chatModel}
                    side={quickQuestion.side}
                    maxHeight={quickQuestion.maxHeight}
                    onClose={handleCloseQuickQuestion}
                    registerEscapeHandler={registerEscapeHandler}
                />
            )}
        </div>
    );
}
