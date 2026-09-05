"use client";

import { cn } from "@/lib/utils";
import { tokenStyle, type CodeLine } from "@/lib/shiki";
import { useCodeWrap } from "@/hooks/useCodeWrap";

/**
 * Line-numbered code rows shared by the repo file viewer and the inline
 * citation snippets. Rows are either shiki token arrays or raw strings (the
 * pre-highlight state), so the same markup renders both without a flicker
 * when highlighting lands.
 */

export interface LineRange {
    start: number;
    end: number;
}

interface CodeLinesProps {
    rows: Array<CodeLine | string>;
    /** Line number of the first row (a snippet starts partway into a file). */
    startLine?: number;
    /** Absolute line numbers to emphasize, if any. */
    range?: LineRange | null;
    showLineNumbers?: boolean;
    /** Gutter background — must match the surface the rows sit on. */
    gutterClassName?: string;
    className?: string;
}

export function CodeLines({
    rows,
    startLine = 1,
    range = null,
    showLineNumbers = true,
    gutterClassName = "bg-background",
    className,
}: CodeLinesProps) {
    const wrap = useCodeWrap();
    return (
        <div
            className={cn(
                "font-mono text-xs leading-[1.55]",
                // Wrapping needs a width to wrap against; without it the rows
                // size to their longest line and never break.
                wrap ? "w-full" : "w-max min-w-full",
                className
            )}
        >
            {rows.map((line, index) => {
                const lineNumber = startLine + index;
                const inRange =
                    !!range &&
                    lineNumber >= range.start &&
                    lineNumber <= range.end;
                return (
                    <div
                        key={lineNumber}
                        data-line={lineNumber}
                        className={cn(
                            "flex",
                            inRange && "bg-yellow-200/50 dark:bg-yellow-400/15"
                        )}
                    >
                        {showLineNumbers && (
                            <span
                                className={cn(
                                    // `select-none` keeps gutter numbers out
                                    // of copied / attached selections.
                                    "sticky left-0 w-12 shrink-0 self-stretch select-none border-r border-border/50 px-2 text-right text-[11px] text-muted-foreground/70 tabular-nums",
                                    gutterClassName,
                                    inRange &&
                                        "bg-yellow-200/50 text-foreground dark:bg-yellow-400/15"
                                )}
                            >
                                {/* Wrapped continuation lines carry no number,
                                    so the number stays on the first row. */}
                                {lineNumber}
                            </span>
                        )}
                        <span
                            className={cn(
                                "px-3",
                                wrap
                                    ? "min-w-0 flex-1 whitespace-pre-wrap break-words"
                                    : "whitespace-pre"
                            )}
                        >
                            {typeof line === "string" ? (
                                line || " "
                            ) : line.length === 0 ? (
                                " "
                            ) : (
                                line.map((token, tokenIndex) => (
                                    <span
                                        key={tokenIndex}
                                        className="op-code-token"
                                        style={tokenStyle(token)}
                                    >
                                        {token.content}
                                    </span>
                                ))
                            )}
                        </span>
                    </div>
                );
            })}
        </div>
    );
}
