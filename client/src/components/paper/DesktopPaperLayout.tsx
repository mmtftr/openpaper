"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { PaperSidebar } from "@/components/PaperSidebar";
import { cn } from "@/lib/utils";
import { sidePanelTabAtom } from "./paperStore";
import { usePaperAtomValue } from "./PaperStoreProvider";
import { PaperReaderPane } from "./PaperReaderPane";
import { PaperSidePanel } from "./PaperSidePanel";

const DEFAULT_SPLIT = 60;
const MIN_SPLIT = 30;
const MAX_SPLIT = 80;
const clampSplit = (percent: number) => Math.min(Math.max(percent, MIN_SPLIT), MAX_SPLIT);

/**
 * The reader's share of the row (30–80 %), set by dragging the divider,
 * arrow keys on it, or a double-click to reset. Measured against the row
 * itself, not the window.
 */
function useResizableSplit() {
    const rowRef = useRef<HTMLDivElement>(null);
    const [leftPercent, setLeftPercent] = useState(DEFAULT_SPLIT);
    const [isDragging, setIsDragging] = useState(false);

    const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        setIsDragging(true);
    }, []);

    const onPointerMove = useCallback(
        (e: React.PointerEvent<HTMLDivElement>) => {
            if (!isDragging || !rowRef.current) return;
            const rect = rowRef.current.getBoundingClientRect();
            setLeftPercent(clampSplit(((e.clientX - rect.left) / rect.width) * 100));
        },
        [isDragging]
    );

    const endDrag = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) {
            e.currentTarget.releasePointerCapture(e.pointerId);
        }
        setIsDragging(false);
    }, []);

    const onKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
        const step = e.shiftKey ? 10 : 2;
        if (e.key === "ArrowLeft") setLeftPercent((p) => clampSplit(p - step));
        else if (e.key === "ArrowRight") setLeftPercent((p) => clampSplit(p + step));
        else if (e.key === "Home") setLeftPercent(MIN_SPLIT);
        else if (e.key === "End") setLeftPercent(MAX_SPLIT);
        else return;
        e.preventDefault();
    }, []);

    // Text selection and the cursor would flicker across the panes mid-drag.
    useEffect(() => {
        if (!isDragging) return;
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
        return () => {
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        };
    }, [isDragging]);

    const dividerProps = {
        role: "separator",
        "aria-orientation": "vertical" as const,
        "aria-label": "Resize reader and side panel",
        "aria-valuemin": MIN_SPLIT,
        "aria-valuemax": MAX_SPLIT,
        "aria-valuenow": Math.round(leftPercent),
        tabIndex: 0,
        onPointerDown,
        onPointerMove,
        onPointerUp: endDrag,
        onPointerCancel: endDrag,
        onKeyDown,
        onDoubleClick: () => setLeftPercent(DEFAULT_SPLIT),
    };

    return { rowRef, leftPercent, isDragging, dividerProps };
}

/** Reader on the left, a resizable side panel on the right (hidden in read mode). */
export function DesktopPaperLayout() {
    const isReadMode = usePaperAtomValue(sidePanelTabAtom) === "Read";
    const { rowRef, leftPercent, isDragging, dividerProps } = useResizableSplit();

    // No width transition: every intermediate width would refit and re-render
    // the PDF pages.
    return (
        <div ref={rowRef} className="relative flex min-h-0 w-full flex-1 flex-row">
            <div className="h-full min-w-0" style={{ width: isReadMode ? "100%" : `${leftPercent}%` }}>
                <div className="relative h-full w-full">
                    <PaperReaderPane />
                </div>
            </div>

            {!isReadMode && (
                <div
                    {...dividerProps}
                    data-dragging={isDragging || undefined}
                    className="group/divider relative z-10 -mr-3 w-3 shrink-0 cursor-col-resize touch-none outline-none"
                    title="Drag to resize · double-click to reset"
                >
                    <div className="absolute inset-y-0 left-0 w-px bg-border transition-colors duration-150 group-hover/divider:bg-brand/50 group-focus-visible/divider:bg-brand group-data-[dragging]/divider:bg-brand" />
                    <div className="absolute top-1/2 left-0 h-10 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-border opacity-0 transition-[opacity,background-color] duration-150 ease-out-soft group-hover/divider:bg-brand/60 group-hover/divider:opacity-100 group-focus-visible/divider:opacity-100 group-data-[dragging]/divider:bg-brand group-data-[dragging]/divider:opacity-100" />
                </div>
            )}

            {/* In read mode the column leaves the flow but keeps its width, so
                the hidden chat inside keeps its size and scroll position. */}
            <div
                className={cn(
                    "flex h-full min-w-0 flex-row",
                    isReadMode ? "pointer-events-none absolute inset-y-0 right-0" : "relative"
                )}
                style={{ width: `${100 - leftPercent}%` }}
            >
                <PaperSidePanel isMobile={false} />
                <div className="pointer-events-auto contents">
                    <PaperSidebar />
                </div>
            </div>
        </div>
    );
}
