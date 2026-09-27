"use client";

import { useCallback, useEffect, useState } from "react";
import { AnnotationsView } from "@/components/AnnotationsView";
import { PaperChatPanel } from "@/components/chat/PaperChatPanel";
import { PaperDocEditor } from "@/components/PaperDocEditor";
import { FeatureGate } from "@/components/ingest/FeatureGate";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import type { PaperHighlight } from "@/lib/schema";
import {
    displayedPaperIdAtom,
    jumpToHighlightAtom,
    openHighlightInTextAtom,
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
 *
 * Chat mounts on first use and then stays mounted, hidden, under the other
 * tools and through read mode: remounting replayed the transcript's
 * scroll-to-bottom animation and the streaming turn's entrance. It is hidden
 * with `visibility: hidden` (plus `inert`), not `display: none`, so it keeps
 * its box: use-stick-to-bottom keeps measuring real heights and following a
 * stream while hidden, and the scroll position and CSS animations are left
 * alone, so re-showing it is a no-op. Annotations and Doc mount per visit.
 *
 * On desktop the tool switcher (PaperSidebar) floats at the right end of each
 * tool's top bar; `--panel-tools-inset` is the room those bars leave for it.
 */
export function PaperSidePanel({ isMobile }: { isMobile: boolean }) {
    const { user } = useAuth();
    const tab = usePaperAtomValue(sidePanelTabAtom);
    const paper = usePaperAtomValue(paperAtom);
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    const displayedPaperId = usePaperAtomValue(displayedPaperIdAtom);

    // Keyed by paper so another paper starts without a hidden chat.
    const [chatSeenFor, setChatSeenFor] = useState<string | null>(null);
    useEffect(() => {
        if (tab === "Chat") setChatSeenFor(parentPaperId);
    }, [tab, parentPaperId]);
    const chatMounted = tab === "Chat" || chatSeenFor === parentPaperId;

    if (!paper || (tab === "Read" && !chatMounted)) return null;

    return (
        <div
            className={cn(
                "min-w-0 flex-grow h-full overflow-hidden",
                !isMobile && "[--panel-tools-inset:2.25rem]",
                tab === "Read" && "invisible"
            )}
            inert={tab === "Read"}
        >
            <div className="relative h-full">
                {chatMounted && (
                    <div
                        className={cn("absolute inset-0", tab !== "Chat" && "invisible")}
                        inert={tab !== "Chat"}
                    >
                        <FeatureGate paperId={parentPaperId} feature="chat" className="h-full">
                            <PaperChatPanel id={parentPaperId} paperData={paper} />
                        </FeatureGate>
                    </div>
                )}

                {tab === "Annotations" && user && (
                    <div className="absolute inset-0 flex flex-col overflow-y-auto">
                        <FeatureGate
                            paperId={displayedPaperId || parentPaperId}
                            feature="ai_highlights"
                            variant="inline"
                            className="border-b"
                        />
                        <AnnotationsPanel isMobile={isMobile} />
                    </div>
                )}

                {/* The notes editor's top bar is its root's first row. */}
                {tab === "Doc" && (
                    <div className="absolute inset-0 flex flex-col [&>div>div:first-child]:pr-[calc(0.5rem+var(--panel-tools-inset,0px))]">
                        <PaperDocEditor paperId={parentPaperId} />
                    </div>
                )}
            </div>
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
    const openHighlightInText = useSetPaperAtom(openHighlightInTextAtom);

    // Phones open the passage in the Text view (the PDF when it isn't there).
    const onHighlightClick = useCallback(
        (highlight: PaperHighlight) => {
            if (isMobile) openHighlightInText(highlight);
            else jumpToHighlight(highlight);
        },
        [isMobile, jumpToHighlight, openHighlightInText]
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
