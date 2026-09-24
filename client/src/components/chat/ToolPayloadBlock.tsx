"use client";

import { Fragment, useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import { tokenStyle } from "@/lib/shiki";
import { useHighlightedLines } from "@/hooks/useHighlightedLines";
import { clipJson, clipString, formatJson } from "@/lib/jsonPreview";
import type { ToolPayload } from "@/lib/chatMessages";

/**
 * One payload (input, output or error) inside an expanded tool row.
 *
 * JSON payloads are pretty-printed and highlighted with the shared shiki
 * grammar; text payloads (sandbox stdout, error text) render as-is. Both are
 * clipped for display with a "Show all" toggle — structurally for JSON, so
 * the clipped view is still valid JSON (see `jsonPreview`). A server-side
 * wire-cap preview arrives already trimmed; `note` says so under the block.
 */

/** Text payloads longer than this get the same Show-all toggle. */
const TEXT_CLIP_CHARS = 2000;
/** Past this, highlighting an expanded payload costs more than it's worth. */
const MAX_HIGHLIGHT_CHARS = 20_000;

interface ToolPayloadBlockProps {
    payload: ToolPayload;
    /** Footer line, e.g. what the server left out of a truncated output. */
    note?: string | null;
    /** Highlight JSON payloads. Off while the input is still streaming. */
    highlight?: boolean;
    tone?: "muted" | "destructive";
}

export function ToolPayloadBlock({
    payload,
    note,
    highlight = true,
    tone = "muted",
}: ToolPayloadBlockProps) {
    const [expanded, setExpanded] = useState(false);

    const { code, clipped } = useMemo(() => {
        if (payload.kind === "text") {
            const shown = expanded
                ? payload.text
                : clipString(payload.text, TEXT_CLIP_CHARS);
            return { code: shown, clipped: shown !== payload.text };
        }
        if (expanded) return { code: formatJson(payload.value), clipped: false };
        const result = clipJson(payload.value);
        return { code: formatJson(result.value), clipped: result.clipped };
    }, [payload, expanded]);

    const lines = useHighlightedLines(
        code,
        "json",
        highlight && payload.kind === "json" && code.length <= MAX_HIGHLIGHT_CHARS
    );

    const showToggle = clipped || expanded;

    return (
        <div>
            <pre
                className={cn(
                    "whitespace-pre-wrap break-words max-h-40 overflow-y-auto rounded p-1.5",
                    tone === "destructive" ? "bg-destructive/10" : "bg-muted/40"
                )}
            >
                {lines.map((line, index) => (
                    <Fragment key={index}>
                        {typeof line === "string"
                            ? line
                            : line.map((token, tokenIndex) => (
                                  <span
                                      key={tokenIndex}
                                      className="op-code-token"
                                      style={tokenStyle(token)}
                                  >
                                      {token.content}
                                  </span>
                              ))}
                        {index < lines.length - 1 ? "\n" : null}
                    </Fragment>
                ))}
            </pre>
            {(showToggle || note) && (
                <div className="mt-0.5 flex items-center gap-2 px-1 text-[10px] font-sans">
                    {showToggle && (
                        <button
                            type="button"
                            className="underline-offset-2 hover:underline hover:text-foreground"
                            onClick={() => setExpanded((value) => !value)}
                        >
                            {expanded ? "Show less" : "Show all"}
                        </button>
                    )}
                    {note && <span className="italic">{note}</span>}
                </div>
            )}
        </div>
    );
}
