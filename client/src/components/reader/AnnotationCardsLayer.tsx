"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { BasicUser } from "@/lib/auth";
import type { PaperHighlight, PaperHighlightAnnotation } from "@/lib/schema";
import { InlineAnnotationCard } from "@/components/InlineAnnotationCard";
import { useReaderContext } from "./ReaderContext";
import type { AnchoredHighlight } from "./useAnchoredHighlights";

/** Matches `w-[280px]` on the card. */
const CARD_WIDTH_PX = 280;
const GUTTER_GAP_PX = 8;
/** Keep stacked cards from overlapping each other. */
const CARD_STACK_GAP_PX = 8;

interface CardPlacement {
	highlightId: string;
	top: number;
	left: number;
}

export interface AnnotationCardsLayerProps {
	highlights: AnchoredHighlight[];
	annotations: PaperHighlightAnnotation[];
	activeHighlight: PaperHighlight | null;
	composeHighlightId?: string | null;
	currentUser?: BasicUser | null;
	addAnnotation?: (
		highlightId: string,
		content: string
	) => Promise<PaperHighlightAnnotation>;
	updateAnnotation?: (
		annotationId: string,
		content: string
	) => Promise<unknown> | void;
	removeAnnotation?: (annotationId: string) => void;
	onClose: (highlightId: string) => void;
	onCardFocus?: (highlightId: string) => void;
}

/**
 * Notes pinned in the page margin, next to the text they annotate.
 *
 * Card geometry is the one thing that genuinely has to be measured in pixels:
 * a card sits beside its highlight but is far wider than the gutter percentages
 * would allow, so it is placed in the scroll container's *content* box rather
 * than inside a page element. Because the cards are portalled into that
 * container, the computed offsets scroll with the pages for free — only zoom
 * and relayout require a fresh measurement.
 */
export function AnnotationCardsLayer({
	highlights,
	annotations,
	activeHighlight,
	composeHighlightId,
	currentUser,
	addAnnotation,
	updateAnnotation,
	removeAnnotation,
	onClose,
	onCardFocus,
}: AnnotationCardsLayerProps) {
	const { containerRef } = useReaderContext();
	const [placements, setPlacements] = useState<CardPlacement[]>([]);
	const heightsRef = useRef<Map<string, number>>(new Map());
	const frameRef = useRef<number | null>(null);

	const annotationsByHighlight = useMemo(() => {
		const map = new Map<string, PaperHighlightAnnotation[]>();
		for (const annotation of annotations) {
			const list = map.get(annotation.highlight_id);
			if (list) list.push(annotation);
			else map.set(annotation.highlight_id, [annotation]);
		}
		return map;
	}, [annotations]);

	/** Highlights that warrant a card: they have notes, or are being composed. */
	const visible = useMemo(
		() =>
			highlights.filter(({ highlight }) => {
				if (!highlight.id) return false;
				if (highlight.id === composeHighlightId) return true;
				return (annotationsByHighlight.get(highlight.id)?.length ?? 0) > 0;
			}),
		[highlights, annotationsByHighlight, composeHighlightId]
	);

	const measure = useCallback(() => {
		const container = containerRef.current;
		if (!container) return;
		const containerRect = container.getBoundingClientRect();

		const next: CardPlacement[] = [];
		let previousBottom = -Infinity;

		// Sorted by document order so the stack-avoidance pass below is stable.
		const ordered = [...visible].sort((a, b) => {
			if (a.anchor.page !== b.anchor.page) return a.anchor.page - b.anchor.page;
			return (a.anchor.rects[0]?.top ?? 0) - (b.anchor.rects[0]?.top ?? 0);
		});

		for (const { highlight, anchor } of ordered) {
			const pageEl = container.querySelector<HTMLElement>(
				`.pdfViewer .page[data-page-number="${anchor.page}"]`
			);
			if (!pageEl) continue;
			const pageRect = pageEl.getBoundingClientRect();
			// An anchor with no rects would reduce to Infinity and emit invalid CSS.
			if (!anchor.rects.length) continue;
			const topPercent = Math.min(...anchor.rects.map((r) => r.top));

			const top =
				pageRect.top -
				containerRect.top +
				container.scrollTop +
				(topPercent / 100) * pageRect.height;

			// Prefer the right gutter; fall back to the left when the page sits too
			// close to the container's right edge to fit a card.
			const pageRight = pageRect.right - containerRect.left + container.scrollLeft;
			const pageLeft = pageRect.left - containerRect.left + container.scrollLeft;
			const rightGutter = pageRight + GUTTER_GAP_PX;
			// Fit against the *visible* width, not scrollWidth: the content box can
			// be far wider than the pane when zoomed in, and a card placed out
			// there is simply cut off by the side panel rather than being reachable.
			const visibleRight = container.scrollLeft + container.clientWidth;
			const fitsRight =
				rightGutter + CARD_WIDTH_PX <= visibleRight ||
				pageLeft - GUTTER_GAP_PX - CARD_WIDTH_PX < container.scrollLeft;
			const rawLeft = fitsRight
				? rightGutter
				: pageLeft - GUTTER_GAP_PX - CARD_WIDTH_PX;
			const left = Math.max(
				container.scrollLeft,
				Math.min(rawLeft, visibleRight - CARD_WIDTH_PX)
			);

			const resolvedTop = Math.max(top, previousBottom + CARD_STACK_GAP_PX);
			previousBottom =
				resolvedTop + (heightsRef.current.get(highlight.id!) ?? 96);

			next.push({ highlightId: highlight.id!, top: resolvedTop, left });
		}

		setPlacements((prev) => {
			if (
				prev.length === next.length &&
				prev.every(
					(p, i) =>
						p.highlightId === next[i].highlightId &&
						Math.abs(p.top - next[i].top) < 0.5 &&
						Math.abs(p.left - next[i].left) < 0.5
				)
			) {
				return prev;
			}
			return next;
		});
	}, [containerRef, visible]);

	const scheduleMeasure = useCallback(() => {
		if (frameRef.current != null) return;
		frameRef.current = requestAnimationFrame(() => {
			frameRef.current = null;
			measure();
		});
	}, [measure]);

	useEffect(() => {
		scheduleMeasure();
		const container = containerRef.current;
		if (!container) return;

		container.addEventListener("scroll", scheduleMeasure, { passive: true });
		window.addEventListener("resize", scheduleMeasure);
		// Zoom resizes `.pdfViewer` and the page boxes, not the fixed-size scroll
		// container — and near the top of a document it need not produce a scroll
		// event either, so observing only the container leaves cards stranded at
		// their old coordinates.
		const observer = new ResizeObserver(scheduleMeasure);
		observer.observe(container);
		const viewerEl = container.querySelector(".pdfViewer");
		if (viewerEl) observer.observe(viewerEl);
		const mutation = new MutationObserver(scheduleMeasure);
		mutation.observe(container, { childList: true, subtree: true });

		return () => {
			container.removeEventListener("scroll", scheduleMeasure);
			window.removeEventListener("resize", scheduleMeasure);
			observer.disconnect();
			mutation.disconnect();
			if (frameRef.current != null) {
				cancelAnimationFrame(frameRef.current);
				// Must clear, not just cancel: the next effect calls
				// scheduleMeasure, sees a non-null (but dead) frame id, and
				// silently drops every later measurement.
				frameRef.current = null;
			}
		};
	}, [containerRef, scheduleMeasure]);

	const container = containerRef.current;
	if (!container) return null;

	return createPortal(
		<>
			{placements.map(({ highlightId, top, left }) => (
				<InlineAnnotationCard
					key={highlightId}
					highlightId={highlightId}
					topPosition={top}
					leftPosition={left}
					annotations={annotationsByHighlight.get(highlightId) ?? []}
					isActive={activeHighlight?.id === highlightId}
					user={currentUser ?? null}
					addAnnotation={addAnnotation}
					updateAnnotation={updateAnnotation}
					removeAnnotation={removeAnnotation}
					onClose={() => onClose(highlightId)}
					onCardFocus={() => onCardFocus?.(highlightId)}
					onHeightChange={(height) => {
						const prev = heightsRef.current.get(highlightId);
						if (prev === height) return;
						heightsRef.current.set(highlightId, height);
						scheduleMeasure();
					}}
				/>
			))}
		</>,
		container
	);
}
