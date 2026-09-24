"use client";

import { useEffect, useState } from "react";
import { PaperSidebar } from "@/components/PaperSidebar";
import { sidePanelTabAtom } from "./paperStore";
import { usePaperAtomValue } from "./PaperStoreProvider";
import { PaperReaderPane } from "./PaperReaderPane";
import { PaperSidePanel } from "./PaperSidePanel";

/** Reader width while dragging the divider: 30–80 % of the window. */
function useResizableSplit(initialPercent: number) {
    const [leftPercent, setLeftPercent] = useState(initialPercent);
    const [isDragging, setIsDragging] = useState(false);

    useEffect(() => {
        if (!isDragging) return;
        const handleMouseMove = (e: MouseEvent) => {
            const percent = (e.clientX / window.innerWidth) * 100;
            setLeftPercent(Math.min(Math.max(percent, 30), 80));
        };
        const handleMouseUp = () => {
            setIsDragging(false);
            document.body.style.cursor = "default";
            document.body.style.userSelect = "auto";
        };
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
        document.addEventListener("mousemove", handleMouseMove);
        document.addEventListener("mouseup", handleMouseUp);
        return () => {
            document.removeEventListener("mousemove", handleMouseMove);
            document.removeEventListener("mouseup", handleMouseUp);
        };
    }, [isDragging]);

    return { leftPercent, isDragging, startDrag: () => setIsDragging(true) };
}

/** Reader on the left, a resizable side panel on the right (hidden in read mode). */
export function DesktopPaperLayout() {
    const isReadMode = usePaperAtomValue(sidePanelTabAtom) === "Read";
    const { leftPercent, isDragging, startDrag } = useResizableSplit(60);
    const transition = isDragging ? "none" : "width 300ms ease";

    return (
        <div className="flex flex-row w-full h-[calc(100vh-64px)]">
            <div className="w-full h-full flex items-center justify-center gap-0">
                <div
                    className="border-r-2 dark:border-gray-800 border-gray-200 p-0 h-full"
                    style={{ width: isReadMode ? "100%" : `${leftPercent}%`, transition }}
                >
                    <div className="relative w-full h-full">
                        <PaperReaderPane />
                    </div>
                </div>

                {!isReadMode && (
                    <div
                        className="w-2 bg-background hover:bg-blue-100 dark:hover:bg-blue-400 cursor-col-resize transition-colors duration-200 flex-shrink-0 h-full rounded-2xl"
                        onMouseDown={(e) => {
                            e.preventDefault();
                            startDrag();
                        }}
                    />
                )}

                <div
                    className="flex flex-row h-full relative"
                    style={{ width: isReadMode ? "auto" : `${100 - leftPercent}%`, transition }}
                >
                    <PaperSidePanel isMobile={false} />
                    <PaperSidebar />
                </div>
            </div>
        </div>
    );
}
