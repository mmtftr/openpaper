"use client";

import { Book, Box, ScrollText } from "lucide-react";
import type { ComponentType } from "react";
import { Button } from "@/components/ui/button";
import { PaperMarkdownReader } from "@/components/PaperMarkdownReader";
import { PaperSidebar } from "@/components/PaperSidebar";
import {
    displayedPaperAtom,
    displayedPaperIdAtom,
    mobileViewAtom,
    paperAtom,
    parentPaperIdAtom,
    type MobileView,
} from "./paperStore";
import { usePaperAtom, usePaperAtomValue } from "./PaperStoreProvider";
import { PaperReaderPane } from "./PaperReaderPane";
import { PaperSidePanel } from "./PaperSidePanel";
import { useSupplementaryMaterials } from "./useSupplementaryMaterials";

const MOBILE_VIEWS: { view: MobileView; label: string; icon: ComponentType<{ size?: number }> }[] = [
    { view: "reader", label: "Reader", icon: Book },
    { view: "markdown", label: "Markdown", icon: ScrollText },
    { view: "panel", label: "Tools", icon: Box },
];

/** One view at a time (reader, markdown, side panel) with a bottom nav to switch. */
export function MobilePaperLayout() {
    const [mobileView, setMobileView] = usePaperAtom(mobileViewAtom);

    return (
        <div className="flex flex-col w-full h-[calc(100vh-64px)]">
            <div className="flex-grow overflow-auto min-h-0">
                {mobileView === "reader" ? (
                    <div className="relative w-full h-full">
                        <PaperReaderPane mobile />
                    </div>
                ) : mobileView === "markdown" ? (
                    <MarkdownPane />
                ) : (
                    <div className="w-full h-full">
                        <div className="flex flex-row h-full relative">
                            <PaperSidePanel isMobile />
                            <PaperSidebar />
                        </div>
                    </div>
                )}
            </div>
            <div className="flex-shrink-0 border-t border-gray-200 dark:border-gray-800">
                <div className="flex justify-around items-center h-16">
                    {MOBILE_VIEWS.map(({ view, label, icon: Icon }) => (
                        <Button
                            key={view}
                            variant="ghost"
                            onClick={() => setMobileView(view)}
                            className={`flex flex-col items-center gap-1 ${mobileView === view ? "text-blue-500" : ""}`}
                        >
                            <Icon size={24} />
                            <span className="text-xs">{label}</span>
                        </Button>
                    ))}
                </div>
            </div>
        </div>
    );
}

/** The displayed paper's markdown, with the same supplementary switcher as the reader. */
function MarkdownPane() {
    const paper = usePaperAtomValue(paperAtom);
    const displayedPaper = usePaperAtomValue(displayedPaperAtom);
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    const [displayedPaperId, setDisplayedPaperId] = usePaperAtom(displayedPaperIdAtom);
    const { materials } = useSupplementaryMaterials();
    return (
        <PaperMarkdownReader
            paperId={displayedPaperId}
            title={displayedPaper?.title ?? paper?.title}
            parentPaperId={parentPaperId}
            displayedPaperId={displayedPaperId}
            parentPaperTitle={paper?.title ?? undefined}
            supplementaryMaterials={materials}
            onChangeDisplayed={setDisplayedPaperId}
        />
    );
}
