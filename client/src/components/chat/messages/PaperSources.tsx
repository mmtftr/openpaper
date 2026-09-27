"use client";

import {
    Sources,
    SourcesContent,
    SourcesTrigger,
} from "@/components/ai-elements/sources";
import { CodeCitationItem } from "@/components/code/CodeCitationItem";
import { Markdown } from "@/components/markdown/Markdown";
import {
    isCitation,
    useActiveCitation,
    useFlashedCitation,
} from "@/components/paper/useCitationJump";
import { isCodeCitation } from "@/lib/chatMessages";
import type { Citation } from "@/lib/schema";
import { cn } from "@/lib/utils";
import { CITATION_BLURB_COMPONENTS } from "./citationMarkdown";

interface PaperSourcesProps {
    citations: Citation[];
    messageIndex: number;
    /** Code citation to emphasize after a jump from its inline marker. */
    focusedCodeKey?: string | null;
    onCitationClick: (key: string, messageIndex: number) => void;
    rightSlot?: React.ReactNode;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

/**
 * An answer's sources disclosure. A PDF citation's row carries the ids the
 * citation jump reads its search text from (`citation-{key}-{index}`,
 * `citation-ref-{key}-{index}`); it lights up while it's the citation just
 * clicked, and flashes when its quote couldn't be found in the PDF.
 */
export function PaperSources({
    citations,
    messageIndex,
    focusedCodeKey,
    onCitationClick,
    rightSlot,
    open,
    onOpenChange,
}: PaperSourcesProps) {
    const activeCitation = useActiveCitation();
    const flashedCitation = useFlashedCitation();

    return (
        <Sources open={open} onOpenChange={onOpenChange} className="mb-0">
            <div className="flex items-center justify-between gap-2">
                <SourcesTrigger
                    count={citations.length}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                />
                {rightSlot}
            </div>
            <SourcesContent className="!w-full !mt-1.5 !gap-0">
                <ul className="list-none p-0 m-0">
                    {citations.map((citation, refIndex) => {
                        // Code citations point at the repo snapshot, not the
                        // PDF — they get their own row and their own click
                        // target (the code viewer).
                        if (isCodeCitation(citation)) {
                            return (
                                <CodeCitationItem
                                    key={`${citation.key}-${refIndex}`}
                                    citation={citation}
                                    messageIndex={messageIndex}
                                    focused={
                                        focusedCodeKey === String(citation.key)
                                    }
                                />
                            );
                        }
                        const active = isCitation(activeCitation, citation.key, messageIndex);
                        const flashed = isCitation(flashedCitation, citation.key, messageIndex);
                        return (
                            <li
                                key={`${citation.key}-${refIndex}`}
                                id={`citation-${citation.key}-${messageIndex}`}
                                role="button"
                                tabIndex={0}
                                aria-label={`Jump to citation ${citation.key}`}
                                onClick={() =>
                                    onCitationClick(citation.key, messageIndex)
                                }
                                onKeyDown={(e) => {
                                    if (e.key === "Enter" || e.key === " ") {
                                        e.preventDefault();
                                        onCitationClick(citation.key, messageIndex);
                                    }
                                }}
                                className={cn(
                                    "flex gap-1.5 items-baseline rounded px-1.5 py-0.5 cursor-pointer transition-colors hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                    flashed &&
                                        "bg-yellow-200/80 dark:bg-yellow-500/30 animate-pulse",
                                    !flashed &&
                                        active &&
                                        "bg-blue-100 dark:bg-blue-900/40"
                                )}
                            >
                                <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                                    [{citation.key}]
                                </span>
                                {/* A div, not a span: the renderer wraps its
                                    output in a block element. */}
                                <div
                                    id={`citation-ref-${citation.key}-${messageIndex}`}
                                    className="min-w-0 text-xs text-muted-foreground line-clamp-2 leading-snug citation-ref-md"
                                >
                                    <Markdown components={CITATION_BLURB_COMPONENTS}>
                                        {citation.reference}
                                    </Markdown>
                                </div>
                            </li>
                        );
                    })}
                </ul>
            </SourcesContent>
        </Sources>
    );
}
