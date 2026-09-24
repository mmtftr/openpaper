"use client";

import { useCallback } from "react";
import { AnnotationsView } from "@/components/AnnotationsView";
import { PaperChatPanel } from "@/components/chat/PaperChatPanel";
import { MetadataPopover } from "@/components/chat/MetadataPopover";
import { PaperDocEditor } from "@/components/PaperDocEditor";
import { FeatureGate } from "@/components/ingest/FeatureGate";
import { useAuth } from "@/lib/auth";
import type { PaperHighlight } from "@/lib/schema";
import {
    displayedPaperIdAtom,
    jumpToHighlightAtom,
    mobileViewAtom,
    paperAtom,
    parentPaperIdAtom,
    renderedHighlightPositionsAtom,
    sidePanelTabAtom,
} from "./paperStore";
import { usePaperAtomValue, useSetPaperAtom } from "./PaperStoreProvider";
import { usePaperHighlights } from "./usePaperHighlights";

/**
 * The side panel's current tab. Chat and notes belong to the parent paper
 * even while a supplementary's PDF is shown; annotations follow the PDF.
 */
export function PaperSidePanel({ isMobile }: { isMobile: boolean }) {
    const { user } = useAuth();
    const tab = usePaperAtomValue(sidePanelTabAtom);
    const paper = usePaperAtomValue(paperAtom);
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    const displayedPaperId = usePaperAtomValue(displayedPaperIdAtom);

    const heightClass = isMobile ? "h-[calc(100vh-128px)]" : "h-[calc(100vh-64px)]";

    if (tab === "Read" || !paper) return null;

    return (
        <div className={`flex-grow h-full overflow-hidden ${isMobile ? "" : "pr-[60px]"}`}>
            {tab === "Annotations" && user && (
                <div className={`flex flex-col ${heightClass} overflow-y-auto`}>
                    <FeatureGate
                        paperId={displayedPaperId || parentPaperId}
                        feature="ai_highlights"
                        variant="inline"
                        className="border-b"
                    />
                    <AnnotationsPanel isMobile={isMobile} />
                </div>
            )}

            {tab === "Doc" && (
                <div className={`flex flex-col ${heightClass}`}>
                    <PaperDocEditor paperId={parentPaperId} />
                </div>
            )}

            {tab === "Chat" && (
                <FeatureGate paperId={parentPaperId} feature="chat" className={heightClass}>
                    <PaperChatPanel
                        id={parentPaperId}
                        paperData={paper}
                        isMobile={isMobile}
                        headerSlot={<MetadataPopover paperData={paper} />}
                    />
                </FeatureGate>
            )}
        </div>
    );
}

/** The displayed PDF's note threads; picking one scrolls the reader to its highlight. */
function AnnotationsPanel({ isMobile }: { isMobile: boolean }) {
    const { user } = useAuth();
    const {
        highlights,
        annotations,
        activeHighlight,
        addAnnotation,
        updateAnnotation,
        removeAnnotation,
    } = usePaperHighlights();
    const renderedHighlightPositions = usePaperAtomValue(renderedHighlightPositionsAtom);
    const jumpToHighlight = useSetPaperAtom(jumpToHighlightAtom);
    const setMobileView = useSetPaperAtom(mobileViewAtom);

    const onHighlightClick = useCallback(
        (highlight: PaperHighlight) => {
            jumpToHighlight(highlight);
            if (isMobile) setMobileView("reader");
        },
        [isMobile, jumpToHighlight, setMobileView]
    );

    if (!user) return null;
    return (
        <AnnotationsView
            annotations={annotations}
            highlights={highlights}
            user={user}
            onHighlightClick={onHighlightClick}
            activeHighlight={activeHighlight}
            renderedHighlightPositions={renderedHighlightPositions}
            addAnnotation={addAnnotation}
            updateAnnotation={updateAnnotation}
            removeAnnotation={removeAnnotation}
        />
    );
}
