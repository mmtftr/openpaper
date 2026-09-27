"use client";

import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import { LayoutGroup, motion } from "motion/react";
import { FileText, Highlighter, MessageCircle, NotebookPen, ScrollText } from "lucide-react";
import { PaperMarkdownReader } from "@/components/PaperMarkdownReader";
import { PILL_SPRING } from "@/lib/motion";
import { cn } from "@/lib/utils";
import {
    displayedPaperAtom,
    displayedPaperIdAtom,
    mobileViewAtom,
    paperAtom,
    parentPaperIdAtom,
    sidePanelTabAtom,
    userMessageReferencesAtom,
    type MobileView,
    type SidePanelTool,
} from "./paperStore";
import { usePaperAtom, usePaperAtomValue } from "./PaperStoreProvider";
import { PaperReaderPane } from "./PaperReaderPane";
import { PaperSidePanel } from "./PaperSidePanel";
import { useSupplementaryMaterials } from "./useSupplementaryMaterials";

type MobileTab = "pdf" | "text" | SidePanelTool;

const MOBILE_TABS: { id: MobileTab; label: string; icon: ComponentType<{ className?: string; strokeWidth?: number }> }[] = [
    { id: "pdf", label: "PDF", icon: FileText },
    { id: "text", label: "Text", icon: ScrollText },
    { id: "Chat", label: "Chat", icon: MessageCircle },
    { id: "Annotations", label: "Highlights", icon: Highlighter },
    { id: "Doc", label: "Notes", icon: NotebookPen },
];

/**
 * One view at a time with a bottom tab bar: the PDF, its markdown, or one of
 * the side-panel tools. The tabs are views of two atoms — `mobileViewAtom`
 * picks reader / markdown / panel, `sidePanelTabAtom` the tool in the panel.
 *
 * The PDF stays mounted (just hidden) while another tab is up, so coming back
 * lands on the same page and zoom; a hidden pane keeps its size, so pdf.js
 * has nothing to refit. The markdown and panel mount on first visit and stay.
 */
export function MobilePaperLayout() {
    const [mobileView, setMobileView] = usePaperAtom(mobileViewAtom);
    const [tab, setTab] = usePaperAtom(sidePanelTabAtom);
    const pendingReferences = usePaperAtomValue(userMessageReferencesAtom).length > 0;

    // Read mode means "no panel", which a phone doesn't have.
    useEffect(() => {
        if (mobileView === "panel" && tab === "Read") setTab("Chat");
    }, [mobileView, tab, setTab]);

    const [visited, setVisited] = useState<Set<MobileView>>(() => new Set([mobileView]));
    useEffect(() => {
        setVisited((prev) => (prev.has(mobileView) ? prev : new Set(prev).add(mobileView)));
    }, [mobileView]);

    const current: MobileTab =
        mobileView === "reader" ? "pdf" : mobileView === "markdown" ? "text" : tab === "Read" ? "Chat" : tab;

    const select = (id: MobileTab) => {
        if (id === "pdf") setMobileView("reader");
        else if (id === "text") setMobileView("markdown");
        else {
            setTab(id);
            setMobileView("panel");
        }
    };

    const layer = (view: MobileView) =>
        cn(
            "absolute inset-0 bg-background",
            mobileView === view ? "z-10 animate-in fade-in duration-200 ease-out-soft" : "invisible pointer-events-none"
        );

    return (
        <div className="flex min-h-0 w-full flex-1 flex-col">
            <div className="relative min-h-0 flex-1">
                <div className={layer("reader")} inert={mobileView !== "reader"}>
                    <PaperReaderPane mobile active={mobileView === "reader"} />
                </div>
                {visited.has("markdown") && (
                    <div className={cn(layer("markdown"), "overflow-auto")} inert={mobileView !== "markdown"}>
                        <MarkdownPane />
                    </div>
                )}
                {visited.has("panel") && (
                    <div className={cn(layer("panel"), "flex")} inert={mobileView !== "panel"}>
                        <PaperSidePanel isMobile />
                    </div>
                )}
            </div>

            <nav aria-label="Paper views" className="shrink-0 border-t bg-background/95 pb-safe backdrop-blur-md">
                <LayoutGroup id="paper-tabbar">
                    <div className="mx-auto flex h-(--app-tabbar-h) max-w-lg items-stretch px-1">
                        {MOBILE_TABS.map(({ id, label, icon: Icon }) => {
                            const active = current === id;
                            return (
                                <button
                                    key={id}
                                    type="button"
                                    aria-pressed={active}
                                    onClick={() => select(id)}
                                    className={cn(
                                        "relative isolate flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors",
                                        active ? "text-foreground" : "text-muted-foreground"
                                    )}
                                >
                                    {active && (
                                        <motion.span
                                            layoutId="pill"
                                            transition={PILL_SPRING}
                                            className="absolute inset-x-1 inset-y-1.5 -z-10 rounded-xl bg-accent"
                                        />
                                    )}
                                    <span className="relative">
                                        <Icon className="size-5" strokeWidth={active ? 2.2 : 1.8} />
                                        {id === "Chat" && pendingReferences && !active && (
                                            <span className="absolute -top-0.5 -right-1 size-2 rounded-full bg-brand ring-2 ring-background animate-in zoom-in-50 duration-200" />
                                        )}
                                    </span>
                                    {label}
                                </button>
                            );
                        })}
                    </div>
                </LayoutGroup>
            </nav>
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
