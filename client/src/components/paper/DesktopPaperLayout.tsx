"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { PaperSidebar } from "@/components/PaperSidebar";
import { PaneResizingContext } from "@/components/reader/paneResizing";
import { cn } from "@/lib/utils";
import { sidePanelTabAtom } from "./paperStore";
import { usePaperAtomValue } from "./PaperStoreProvider";
import { PaperReaderPane } from "./PaperReaderPane";
import { PaperSidePanel } from "./PaperSidePanel";

const DEFAULT_SPLIT = 60;
const MIN_SPLIT = 30;
const MAX_SPLIT = 80;
const clampSplit = (percent: number) => Math.min(Math.max(percent, MIN_SPLIT), MAX_SPLIT);
/** Arrow-key resizes within this window count as one resize for the reader. */
const KEY_RESIZE_SETTLE_MS = 150;

/**
 * The reader's share of the row (30–80 %), set by dragging the divider,
 * arrow keys on it, or a double-click to reset. Measured against the row
 * itself, not the window.
 *
 * `isResizing` spans a drag (pointer down to up) or a burst of arrow keys;
 * the reader shows a scaled snapshot meanwhile and refits once at the end.
 */
function useResizableSplit() {
    const rowRef = useRef<HTMLDivElement>(null);
    const [leftPercent, setLeftPercent] = useState(DEFAULT_SPLIT);
    const [isDragging, setIsDragging] = useState(false);
    const [isKeyResizing, setIsKeyResizing] = useState(false);
    const keySettleRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    const endKeyResize = useCallback(() => {
        clearTimeout(keySettleRef.current);
        setIsKeyResizing(false);
    }, []);
    useEffect(() => () => clearTimeout(keySettleRef.current), []);

    const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        endKeyResize();
        // Discrete event: committed before the first move changes any width,
        // so the reader snapshots the layout it is currently showing.
        setIsDragging(true);
    }, [endKeyResize]);

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
        let next: (p: number) => number;
        if (e.key === "ArrowLeft") next = (p) => clampSplit(p - step);
        else if (e.key === "ArrowRight") next = (p) => clampSplit(p + step);
        else if (e.key === "Home") next = () => MIN_SPLIT;
        else if (e.key === "End") next = () => MAX_SPLIT;
        else return;
        e.preventDefault();
        // The reader must snapshot before the width moves, so commit the mode
        // on its own first.
        flushSync(() => setIsKeyResizing(true));
        setLeftPercent(next);
        clearTimeout(keySettleRef.current);
        keySettleRef.current = setTimeout(() => setIsKeyResizing(false), KEY_RESIZE_SETTLE_MS);
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
        onDoubleClick: () => {
            endKeyResize();
            setLeftPercent(DEFAULT_SPLIT);
        },
    };

    return { rowRef, leftPercent, isDragging, isResizing: isDragging || isKeyResizing, dividerProps };
}

/** Reader on the left, a resizable side panel on the right (hidden in read mode). */
export function DesktopPaperLayout() {
    const isReadMode = usePaperAtomValue(sidePanelTabAtom) === "Read";
    const { rowRef, leftPercent, isDragging, isResizing, dividerProps } = useResizableSplit();
    // Stable elements, so a width change re-renders only this row, not the
    // panes (each subscribes to the paper store itself).
    const readerPane = useMemo(() => <PaperReaderPane />, []);
    const sidePanel = useMemo(() => <PaperSidePanel isMobile={false} />, []);
    const sidebar = useMemo(() => <PaperSidebar />, []);

    // No width transition: every intermediate width would refit and re-render
    // the PDF pages.
    return (
        <div ref={rowRef} className="relative flex min-h-0 w-full flex-1 flex-row">
            <div className="h-full min-w-0" style={{ width: isReadMode ? "100%" : `${leftPercent}%` }}>
                <div className="relative h-full w-full">
                    <PaneResizingContext.Provider value={isResizing}>{readerPane}</PaneResizingContext.Provider>
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
                {sidePanel}
                <div className="pointer-events-auto contents">{sidebar}</div>
            </div>
        </div>
    );
}
