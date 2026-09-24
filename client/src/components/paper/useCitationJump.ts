"use client";

import { useCallback } from "react";
import { toast } from "sonner";
import { useFeatureGate } from "@/hooks/useIngest";
import {
    activeCitationAtom,
    clickCitationAtom,
    flashCitationAtom,
    parentPaperIdAtom,
    type CitationRef,
} from "./paperStore";
import { usePaperAtomValue, useSetPaperAtom } from "./PaperStoreProvider";

/**
 * The quote a chat citation refers to, read from its sources row
 * (`citation-ref-{key}-{messageIndex}`), minus a leading footnote marker and
 * wrapping quotes. Null when that row isn't rendered.
 */
function citationSearchTerm(key: string, messageIndex: number): string | null {
    if (!document.getElementById(`citation-${key}-${messageIndex}`)) return null;
    const refValueElement = document.getElementById(`citation-ref-${key}-${messageIndex}`);
    if (!refValueElement) return null;
    let searchTerm = refValueElement.innerText.replace(/^\[\^(\d+|[a-zA-Z]+)\]/, "").trim();
    // Only strip quotes that actually wrap the whole text.
    if (
        (searchTerm.startsWith('"') && searchTerm.endsWith('"')) ||
        (searchTerm.startsWith("'") && searchTerm.endsWith("'"))
    ) {
        searchTerm = searchTerm.substring(1, searchTerm.length - 1);
    }
    return searchTerm;
}

export type CitationClickHandler = (
    key: string,
    messageIndex: number,
    paperId?: string,
    page?: number
) => void;

/**
 * Click handler for chat citations: flips the reader to the cited paper (a
 * missing `paperId` means the parent, even while a supplementary is shown)
 * and searches it for the quote, starting at `page` when the agent gave one.
 * Blocked with a toast until ingest makes citation jumps available.
 */
export function useCitationClick(): CitationClickHandler {
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    const clickCitation = useSetPaperAtom(clickCitationAtom);
    const gate = useFeatureGate(parentPaperId, "citation_jump");
    const blocked = gate.ready && !gate.enabled ? gate.message : null;

    return useCallback(
        (key, messageIndex, paperId, page) => {
            if (blocked) {
                toast.info(blocked);
                return;
            }
            clickCitation({
                key,
                messageIndex,
                paperId: paperId ?? parentPaperId,
                term: citationSearchTerm(key, messageIndex),
                page,
            });
        },
        [blocked, clickCitation, parentPaperId]
    );
}

/** The citation just clicked (its sources row is highlighted for a few seconds). */
export function useActiveCitation(): CitationRef | null {
    return usePaperAtomValue(activeCitationAtom);
}

/** The citation whose quote wasn't found in the PDF, if any (flashed in its sources list). */
export function useFlashedCitation(): CitationRef | null {
    return usePaperAtomValue(flashCitationAtom);
}

export function isCitation(ref: CitationRef | null, key: string, messageIndex: number): boolean {
    return !!ref && ref.key === String(key) && ref.messageIndex === messageIndex;
}
