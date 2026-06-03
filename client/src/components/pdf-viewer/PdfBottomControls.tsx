"use client";

import { Button } from "@/components/ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
    ArrowLeft,
    ArrowRight,
    Highlighter,
    ToggleLeft,
    ToggleRight,
} from "lucide-react";
import { HighlightColor } from "@/lib/schema";
import { HIGHLIGHT_COLOR_SWATCHES } from "@/components/pdf-viewer/highlightColors";

interface PdfBottomControlsProps {
    currentPage: number;
    numPages: number | null;
    goToPreviousPage: () => void;
    goToNextPage: () => void;
    highlightColor: HighlightColor;
    setHighlightColor: (color: HighlightColor) => void;
    showAnnotationCards?: boolean;
    onToggleAnnotationCards?: () => void;
}

export function PdfBottomControls({
    currentPage,
    numPages,
    goToPreviousPage,
    goToNextPage,
    highlightColor,
    setHighlightColor,
    showAnnotationCards = true,
    onToggleAnnotationCards,
}: PdfBottomControlsProps) {
    const currentColorConfig =
        HIGHLIGHT_COLOR_SWATCHES.find((c) => c.color === highlightColor) || HIGHLIGHT_COLOR_SWATCHES[2];

    return (
        <div className="absolute left-1/2 -translate-x-1/2 bottom-3 z-20 pointer-events-none group">
            <div className="pointer-events-auto flex items-center gap-1 rounded-full border border-zinc-200/60 dark:border-zinc-700/60 bg-white/70 dark:bg-zinc-900/70 hover:bg-white/95 dark:hover:bg-zinc-900/95 backdrop-blur-sm px-1.5 py-1 transition-colors shadow-sm">
                {/* Page navigation */}
                <Button
                    onClick={goToPreviousPage}
                    size="sm"
                    variant="ghost"
                    className="h-6 w-6 p-0 text-zinc-700 dark:text-zinc-200 opacity-60 group-hover:opacity-100 transition-opacity"
                    disabled={currentPage <= 1}
                    title="Previous page"
                >
                    <ArrowLeft size={12} />
                </Button>
                <span className="text-[11px] font-medium text-zinc-800 dark:text-zinc-100 min-w-12 text-center tabular-nums">
                    {currentPage} / {numPages || "?"}
                </span>
                <Button
                    onClick={goToNextPage}
                    size="sm"
                    variant="ghost"
                    className="h-6 w-6 p-0 text-zinc-700 dark:text-zinc-200 opacity-60 group-hover:opacity-100 transition-opacity"
                    disabled={!numPages || currentPage >= numPages}
                    title="Next page"
                >
                    <ArrowRight size={12} />
                </Button>

                <div className="h-3 w-px bg-zinc-300 dark:bg-zinc-600 mx-1" />

                {/* Highlight color */}
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button
                            size="sm"
                            variant="ghost"
                            className="h-6 px-1.5 gap-1 text-zinc-700 dark:text-zinc-200 opacity-60 group-hover:opacity-100 transition-opacity"
                            title="Highlight color"
                        >
                            <Highlighter size={12} />
                            <div className={`w-2.5 h-2.5 rounded-sm ${currentColorConfig.bg}`} />
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="center" side="top" className="min-w-0">
                        <div className="flex gap-1 p-1">
                            {HIGHLIGHT_COLOR_SWATCHES.map(({ color, bg }) => (
                                <button
                                    key={color}
                                    type="button"
                                    onClick={() => setHighlightColor(color)}
                                    className={`w-5 h-5 rounded-sm ${bg} hover:scale-110 transition-transform ${
                                        highlightColor === color ? "ring-2 ring-offset-1 ring-gray-400" : ""
                                    }`}
                                    title={color}
                                />
                            ))}
                        </div>
                    </DropdownMenuContent>
                </DropdownMenu>

                {/* Inline annotation cards toggle */}
                {onToggleAnnotationCards && (
                    <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className={`h-6 px-1.5 opacity-60 group-hover:opacity-100 transition-opacity ${
                            showAnnotationCards
                                ? "text-blue-600 dark:text-blue-400"
                                : "text-zinc-700 dark:text-zinc-200"
                        }`}
                        onClick={onToggleAnnotationCards}
                        title={showAnnotationCards ? "Hide inline annotations" : "Show inline annotations"}
                        aria-label={showAnnotationCards ? "Hide inline annotations" : "Show inline annotations"}
                    >
                        {showAnnotationCards ? <ToggleRight size={14} /> : <ToggleLeft size={14} />}
                    </Button>
                )}
            </div>
        </div>
    );
}
