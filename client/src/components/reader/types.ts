import type { HighlightColor, PaperHighlight, ScaledPosition } from "@/lib/schema";

export type SpreadMode = "none" | "odd" | "even";
export type ScaleValue = number | "page-fit" | "page-width" | "auto";

/**
 * A rectangle expressed as a percentage of its page box.
 *
 * This is paper-reader's anchoring model and it is the reason the new reader
 * needs none of the old viewer's zoom bookkeeping: percentages are invariant
 * under scale, so a highlight drawn at 40% down the page stays correct at any
 * zoom, on any spread mode, without re-measuring anything.
 */
export interface PercentRect {
	left: number;
	top: number;
	width: number;
	height: number;
}

/**
 * Where a highlight lives in the document.
 *
 * `quote`/`prefix`/`suffix` are kept alongside the rects so a highlight whose
 * geometry we don't have (assistant citations arrive as text only) can still be
 * located by searching the rendered text layer.
 */
export interface TextAnchor {
	page: number;
	quote: string;
	prefix?: string;
	suffix?: string;
	rects: PercentRect[];
}

/** A highlight plus the anchor the overlay actually draws from. */
export interface AnchoredHighlight {
	highlight: PaperHighlight;
	anchor: TextAnchor;
	color?: HighlightColor;
	role: "user" | "assistant";
}

/**
 * Reported back to the side panel for every highlight the reader actually
 * placed. Shape is kept identical to the old viewer's so `AnnotationsView` and
 * `SidePanelContent` need no changes: they use presence in the map as "this has
 * a real anchor in the PDF", and `matchStrategy` to flag approximate ones.
 */
export interface RenderedHighlightPosition {
	page: number;
	top: number;
	left: number;
	width: number;
	height: number;
	/** "position" when stored coordinates were used, "text" when re-found by quote. */
	matchStrategy?: string;
}

export type { ScaledPosition };
