"use client";

import { useCallback } from "react";
import { toast } from "sonner";
import { PdfReader, type RenderedHighlightPosition } from "@/components/reader";
import { useAuth } from "@/lib/auth";
import type { PaperHighlight } from "@/lib/schema";
import {
    citationSearchSettledAtom,
    displayedPaperAtom,
    displayedPaperIdAtom,
    highlightJumpRequestAtom,
    mobileViewAtom,
    paperAtom,
    parentPaperIdAtom,
    renderedHighlightPositionsAtom,
    sidePanelTabAtom,
    textSearchAtom,
    toggleReadModeAtom,
    userMessageReferencesAtom,
} from "./paperStore";
import { usePaperAtom, usePaperAtomValue, useSetPaperAtom } from "./PaperStoreProvider";
import { usePaperHighlights } from "./usePaperHighlights";
import { useRefreshPdfUrl } from "./usePaperLoader";
import { useSupplementaryMaterials } from "./useSupplementaryMaterials";

/**
 * The PDF reader for the displayed paper, wired to the paper store. The
 * reader itself stays prop-driven (the benchmarks mount it bare).
 *
 * `mobile`: the reader shares the screen with nothing, so there is no read
 * mode and no Annotations panel to keep in step. `active`: false while the
 * phone layout keeps the reader mounted behind another tab.
 */
export function PaperReaderPane({ mobile = false, active = true }: { mobile?: boolean; active?: boolean }) {
    const { user } = useAuth();
    const paper = usePaperAtomValue(paperAtom);
    const displayedPaper = usePaperAtomValue(displayedPaperAtom);
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    const [displayedPaperId, setDisplayedPaperId] = usePaperAtom(displayedPaperIdAtom);
    const [tab, setTab] = usePaperAtom(sidePanelTabAtom);
    const toggleReadMode = useSetPaperAtom(toggleReadModeAtom);
    const setMobileView = useSetPaperAtom(mobileViewAtom);
    const setUserMessageReferences = useSetPaperAtom(userMessageReferencesAtom);
    const onSearchComplete = useSetPaperAtom(citationSearchSettledAtom);
    const highlightJumpRequest = usePaperAtomValue(highlightJumpRequestAtom);
    const textSearch = usePaperAtomValue(textSearchAtom);
    const setRenderedPositions = useSetPaperAtom(renderedHighlightPositionsAtom);
    const refreshPdfUrl = useRefreshPdfUrl();
    const { materials, refetch } = useSupplementaryMaterials();
    const {
        highlights,
        annotations,
        activeHighlight,
        setActiveHighlight,
        addHighlight,
        removeHighlight,
        recolorHighlight,
        addAnnotation,
        updateAnnotation,
        removeAnnotation,
    } = usePaperHighlights();

    const onOverlaysCreated = useCallback(
        (positions: Map<string, RenderedHighlightPosition>) => {
            setRenderedPositions(new Map(positions));
        },
        [setRenderedPositions]
    );

    const onAskStarted = useCallback(() => {
        setTab("Chat");
        // On mobile the panel is a separate view; switching to it would unmount
        // the reader and lose the page, so just confirm where the quote went.
        if (mobile) toast.success("Added to chat");
    }, [mobile, setTab]);

    /** "Open in Annotations" from a highlight's note popover. */
    const onOpenThread = useCallback(
        (highlight: PaperHighlight) => {
            setTab("Annotations");
            setActiveHighlight(highlight);
            if (mobile) setMobileView("panel");
            // Once the panel has rendered, put keyboard focus on the thread.
            requestAnimationFrame(() =>
                requestAnimationFrame(() => {
                    if (!highlight.id) return;
                    document
                        .querySelector<HTMLElement>(
                            `[data-annotation-sidebar-row][data-thread-id="${CSS.escape(highlight.id)}"]`
                        )
                        // Scrolls it into view too, even if it was already the active thread.
                        ?.focus();
                })
            );
        },
        [mobile, setActiveHighlight, setMobileView, setTab]
    );

    const pdfUrl = displayedPaper?.file_url;
    if (!pdfUrl) return null;

    return (
        <PdfReader
            pdfUrl={pdfUrl}
            highlightJumpRequest={highlightJumpRequest}
            explicitSearch={textSearch}
            onSearchComplete={onSearchComplete}
            highlights={highlights}
            annotations={annotations}
            activeHighlight={activeHighlight}
            setActiveHighlight={setActiveHighlight}
            addHighlight={addHighlight}
            removeHighlight={removeHighlight}
            recolorHighlight={recolorHighlight}
            addAnnotation={addAnnotation}
            updateAnnotation={updateAnnotation}
            removeAnnotation={removeAnnotation}
            setUserMessageReferences={setUserMessageReferences}
            onOverlaysCreated={onOverlaysCreated}
            onRefreshUrl={refreshPdfUrl}
            currentUser={user}
            annotationsPanelActive={mobile ? undefined : tab === "Annotations"}
            onAskStarted={onAskStarted}
            onOpenThread={onOpenThread}
            isReadMode={mobile ? undefined : tab === "Read"}
            onToggleReadMode={mobile ? undefined : toggleReadMode}
            parentPaperId={parentPaperId}
            displayedPaperId={displayedPaperId}
            parentPaperTitle={paper?.title ?? undefined}
            supplementaryMaterials={materials}
            onChangeDisplayed={setDisplayedPaperId}
            onSupplementaryUploaded={refetch}
            active={active}
        />
    );
}
