"use client";

import { useEffect, useMemo, useState } from "react";

import { highlightToLines, type CodeLine } from "@/lib/shiki";

/**
 * Rows for a block of code: raw text lines immediately, shiki tokens once
 * highlighting lands.
 *
 * The highlighted result is stored together with the exact source it came
 * from, so content that changes underneath (a streamed block, or a citation
 * that arrives raw and is then replaced by its reconciled version) falls back
 * to plain text rather than showing tokens from the previous text.
 */
export function useHighlightedLines(
    code: string,
    lang: string,
    enabled = true
): Array<CodeLine | string> {
    const [highlighted, setHighlighted] = useState<{
        code: string;
        lines: CodeLine[];
    } | null>(null);

    const plainLines = useMemo(() => code.split("\n"), [code]);

    useEffect(() => {
        if (!enabled || !code.trim()) return;
        let cancelled = false;
        highlightToLines(code, lang)
            .then((lines) => {
                if (!cancelled) setHighlighted({ code, lines });
            })
            .catch((error) => {
                console.error("Failed to highlight code:", error);
            });
        return () => {
            cancelled = true;
        };
    }, [code, lang, enabled]);

    if (!enabled || highlighted?.code !== code) return plainLines;
    // Shiki drops the trailing empty line; keep the raw line count so callers
    // that number lines stay consistent before and after highlighting.
    return highlighted.lines.length === plainLines.length - 1
        ? [...highlighted.lines, ""]
        : highlighted.lines;
}
