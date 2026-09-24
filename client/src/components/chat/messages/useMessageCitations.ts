"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CitationClickHandler } from "@/components/paper/useCitationJump";
import { useFlashedCitation } from "@/components/paper/useCitationJump";
import {
    ChatUIMessage,
    citationsFromMessage,
    isCodeCitation,
} from "@/lib/chatMessages";
import type { Citation } from "@/lib/schema";

/**
 * One message's citations and what clicking them does.
 *
 * Every click (inline marker or sources row) goes through `onCitationClick`:
 *
 * - a code citation opens the sources list and scrolls to its inline snippet
 *   (`citation-{key}-{index}`), briefly emphasized;
 * - a PDF citation opens the sources list first — the page reads the search
 *   text from the row, which only exists while the list is open — and hands
 *   the click to the page once it has rendered. Each click is a new request,
 *   so repeats jump again.
 *
 * The list also opens by itself when one of its citations is flashed (its
 * quote wasn't found in the PDF), and stays open after the flash.
 */
export function useMessageCitations(
    message: ChatUIMessage,
    index: number,
    handleCitationClick: CitationClickHandler
) {
    const parsedCitations = useMemo(() => citationsFromMessage(message), [message]);
    // The streaming message is a new object on every chunk; key the citations
    // by value so the markdown overrides built from them stay stable.
    const citationsKey = JSON.stringify(parsedCitations);
    const citations: Citation[] = useMemo(
        () => parsedCitations,
        [citationsKey]
    );

    const [sourcesOpen, setSourcesOpen] = useState(false);
    const flashed = useFlashedCitation();
    const hasFlash =
        !!flashed &&
        flashed.messageIndex === index &&
        citations.some((c) => String(c.key) === flashed.key);
    useEffect(() => {
        if (hasFlash) setSourcesOpen(true);
    }, [hasFlash]);

    const [pendingCodeScroll, setPendingCodeScroll] = useState<string | null>(null);
    const [focusedCodeKey, setFocusedCodeKey] = useState<string | null>(null);
    const [pendingPdfJump, setPendingPdfJump] = useState<{
        key: string;
        paperId?: string;
        page?: number;
    } | null>(null);
    const handleCitationClickRef = useRef(handleCitationClick);
    handleCitationClickRef.current = handleCitationClick;

    const onCitationClick = useCallback(
        (key: string, msgIdx: number) => {
            const citation = citations.find((c) => String(c.key) === key);
            if (citation && isCodeCitation(citation)) {
                if (msgIdx === index) {
                    setSourcesOpen(true);
                    setPendingCodeScroll(key);
                    setFocusedCodeKey(key);
                }
                return;
            }
            if (msgIdx !== index) {
                handleCitationClickRef.current(
                    key,
                    msgIdx,
                    citation?.paper_id ?? undefined,
                    citation?.page ?? undefined
                );
                return;
            }
            setSourcesOpen(true);
            setPendingPdfJump({
                key,
                paperId: citation?.paper_id ?? undefined,
                page: citation?.page ?? undefined,
            });
        },
        [citations, index]
    );

    useEffect(() => {
        if (!pendingPdfJump || !sourcesOpen) return;
        const frame = requestAnimationFrame(() => {
            handleCitationClickRef.current(
                pendingPdfJump.key,
                index,
                pendingPdfJump.paperId,
                pendingPdfJump.page
            );
            setPendingPdfJump(null);
        });
        return () => cancelAnimationFrame(frame);
    }, [pendingPdfJump, sourcesOpen, index]);

    // Scroll after the sources disclosure has mounted its content.
    useEffect(() => {
        if (!pendingCodeScroll || !sourcesOpen) return;
        const frame = requestAnimationFrame(() => {
            document
                .getElementById(`citation-${pendingCodeScroll}-${index}`)
                ?.scrollIntoView({ behavior: "smooth", block: "center" });
            setPendingCodeScroll(null);
        });
        return () => cancelAnimationFrame(frame);
    }, [pendingCodeScroll, sourcesOpen, index]);

    // Emphasis is a flash, not a mode.
    useEffect(() => {
        if (!focusedCodeKey) return;
        const timer = setTimeout(() => setFocusedCodeKey(null), 2500);
        return () => clearTimeout(timer);
    }, [focusedCodeKey]);

    return {
        citations,
        onCitationClick,
        sourcesOpen,
        setSourcesOpen,
        focusedCodeKey,
    };
}
