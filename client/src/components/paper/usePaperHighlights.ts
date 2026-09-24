"use client";

import { useEffect } from "react";
import { useHighlighterHighlights } from "@/hooks/PdfHighlighterHighlights";
import { useAnnotations } from "@/hooks/PdfAnnotation";
import { useStageRefreshKey } from "@/hooks/useIngest";
import {
    activeHighlightAtom,
    displayedPaperIdAtom,
    highlightJumpRequestAtom,
    highlightsRefreshAtom,
} from "./paperStore";
import { usePaperAtom, usePaperAtomValue, usePaperStore } from "./PaperStoreProvider";

/**
 * The displayed PDF's highlights and notes (a supplementary's own, not the
 * parent's) plus the selected highlight. Both lists are SWR-cached per paper,
 * so the reader and the side panel calling this share one copy.
 */
export function usePaperHighlights() {
    const paperId = usePaperAtomValue(displayedPaperIdAtom);
    const refresh = usePaperAtomValue(highlightsRefreshAtom);
    const refreshKey = refresh.paperId === paperId ? refresh.key : "";
    const highlights = useHighlighterHighlights(paperId, refreshKey);
    const annotations = useAnnotations(paperId, refreshKey);
    const [activeHighlight, setActiveHighlight] = usePaperAtom(activeHighlightAtom);
    return { ...highlights, ...annotations, activeHighlight, setActiveHighlight };
}

/**
 * Page-level upkeep for `usePaperHighlights`, mounted once:
 *
 * - re-read highlights and notes when ingest's AI highlights stage finishes
 *   (its highlights come with note threads) — computed here once, since each
 *   `useStageRefreshKey` keeps its own baseline;
 * - another PDF drops the previous one's selection and pending jump.
 */
export function usePaperHighlightsSync() {
    const store = usePaperStore();
    const paperId = usePaperAtomValue(displayedPaperIdAtom);
    const key = useStageRefreshKey(paperId, ["highlights"]);
    useEffect(() => {
        store.set(highlightsRefreshAtom, { paperId, key });
    }, [paperId, key, store]);

    useEffect(() => {
        store.set(activeHighlightAtom, null);
        store.set(highlightJumpRequestAtom, null);
    }, [paperId, store]);
}
