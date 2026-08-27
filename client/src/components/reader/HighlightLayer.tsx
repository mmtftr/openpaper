"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useAtomValue } from "jotai";
import type { PaperHighlight } from "@/lib/schema";
import { numPagesAtom } from "./atoms";
import { useReaderContext } from "./ReaderContext";
import {
	getAssistantHighlightFill,
	getUserHighlightFill,
} from "./highlightColors";
import type { AnchoredHighlight } from "./useAnchoredHighlights";
import type { RenderedHighlightPosition } from "./types";

export interface HighlightLayerProps {
	highlights: AnchoredHighlight[];
	activeHighlightId?: string | null;
	onHighlightClick?: (highlight: PaperHighlight) => void;
	onHighlightHover?: (
		highlight: PaperHighlight | null,
		point: { x: number; y: number } | null
	) => void;
	onRenderedPositions?: (
		positions: Map<string, RenderedHighlightPosition>
	) => void;
}

/** A page's box in the scroll container's content coordinates. */
interface PageBox {
	page: number;
	left: number;
	top: number;
	width: number;
	height: number;
}

/**
 * Draws highlight rectangles over the pages.
 *
 * **Why this doesn't render inside the page elements.** The obvious
 * implementation portals a rect layer into each pdf.js `.page` div, which makes
 * percentage geometry scale with zoom for free. It is also unsafe:
 * `PDFPageView.reset()` walks the page div's children and `remove()`s every
 * node that isn't one of its own known layers (canvasWrapper, textLayer,
 * annotationLayer, …). pdf.js calls it on every zoom and re-render, so it would
 * delete a React-owned container behind React's back — highlights vanish, and
 * React's next commit throws trying to touch a node that no longer exists.
 *
 * So the overlay lives in a subtree React fully owns: one absolutely-positioned
 * root inside the scroll container (a sibling of `.pdfViewer`, which pdf.js
 * never touches), holding one box per page. Only those per-page boxes are
 * measured in pixels; the rects inside stay percentage-based, so a re-measure
 * is needed on zoom and relayout but never on scroll.
 *
 * Rects are `pointer-events: none` so text selection still works straight
 * through a highlight; clicks and hovers are hit-tested against the geometry
 * instead.
 */
export function HighlightLayer({
	highlights,
	activeHighlightId,
	onHighlightClick,
	onHighlightHover,
	onRenderedPositions,
}: HighlightLayerProps) {
	const { containerRef } = useReaderContext();
	const numPages = useAtomValue(numPagesAtom);
	const [pageBoxes, setPageBoxes] = useState<Map<number, PageBox>>(new Map());
	const frameRef = useRef<number | null>(null);

	const byPage = useMemo(() => {
		const map = new Map<number, AnchoredHighlight[]>();
		for (const entry of highlights) {
			const list = map.get(entry.anchor.page);
			if (list) list.push(entry);
			else map.set(entry.anchor.page, [entry]);
		}
		return map;
	}, [highlights]);

	const measure = useCallback(() => {
		const container = containerRef.current;
		if (!container) return;
		const containerRect = container.getBoundingClientRect();
		const next = new Map<number, PageBox>();

		container
			.querySelectorAll<HTMLElement>(".pdfViewer .page[data-page-number]")
			.forEach((el) => {
				const page = Number(el.getAttribute("data-page-number"));
				if (!page) return;
				const rect = el.getBoundingClientRect();
				if (!rect.width || !rect.height) return;
				next.set(page, {
					page,
					left: rect.left - containerRect.left + container.scrollLeft,
					top: rect.top - containerRect.top + container.scrollTop,
					width: rect.width,
					height: rect.height,
				});
			});

		setPageBoxes((prev) => {
			if (prev.size === next.size) {
				let same = true;
				for (const [page, box] of next) {
					const old = prev.get(page);
					if (
						!old ||
						Math.abs(old.left - box.left) > 0.5 ||
						Math.abs(old.top - box.top) > 0.5 ||
						Math.abs(old.width - box.width) > 0.5 ||
						Math.abs(old.height - box.height) > 0.5
					) {
						same = false;
						break;
					}
				}
				if (same) return prev;
			}
			return next;
		});
	}, [containerRef]);

	const scheduleMeasure = useCallback(() => {
		if (frameRef.current != null) return;
		frameRef.current = requestAnimationFrame(() => {
			frameRef.current = null;
			measure();
		});
	}, [measure]);

	useEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		scheduleMeasure();

		// Page boxes move and resize on zoom, spread changes and pane resizes —
		// but not on scroll, since the coordinates are content-relative.
		const viewerEl = container.querySelector(".pdfViewer");
		const resize = new ResizeObserver(scheduleMeasure);
		resize.observe(container);
		if (viewerEl) resize.observe(viewerEl);

		const mutation = new MutationObserver(scheduleMeasure);
		mutation.observe(container, { childList: true, subtree: true });
		window.addEventListener("resize", scheduleMeasure);

		return () => {
			resize.disconnect();
			mutation.disconnect();
			window.removeEventListener("resize", scheduleMeasure);
			if (frameRef.current != null) {
				cancelAnimationFrame(frameRef.current);
				// Must clear, not just cancel: the next effect calls
				// scheduleMeasure, sees a non-null (but dead) frame id, and
				// silently drops every later measurement.
				frameRef.current = null;
			}
		};
	}, [containerRef, scheduleMeasure, numPages]);

	// Report what we placed, so the annotations sidebar can tell a highlight
	// that is anchored in the PDF from one that only exists as text.
	const lastReportedRef = useRef<string | null>(null);
	useEffect(() => {
		if (!onRenderedPositions) return;
		const positions = new Map<string, RenderedHighlightPosition>();
		for (const { highlight, anchor } of highlights) {
			if (!highlight.id || anchor.rects.length === 0) continue;
			let left = Infinity;
			let top = Infinity;
			let right = -Infinity;
			let bottom = -Infinity;
			for (const r of anchor.rects) {
				left = Math.min(left, r.left);
				top = Math.min(top, r.top);
				right = Math.max(right, r.left + r.width);
				bottom = Math.max(bottom, r.top + r.height);
			}
			positions.set(highlight.id, {
				page: anchor.page,
				left,
				top,
				width: right - left,
				height: bottom - top,
				matchStrategy: highlight.position ? "position" : "text",
			});
		}
		// Only notify when the placement actually changed — the parent copies the
		// map into state, so an unconditional call would loop.
		// Every emitted field participates: keying on page/top alone would hide a
		// horizontal re-anchor or a size change from the parent entirely.
		const signature = Array.from(positions.entries())
			.map(
				([id, p]) =>
					`${id}:${p.page}:${p.left.toFixed(2)}:${p.top.toFixed(2)}:` +
					`${p.width.toFixed(2)}:${p.height.toFixed(2)}:${p.matchStrategy}`
			)
			.sort()
			.join("|");
		if (signature === lastReportedRef.current) return;
		lastReportedRef.current = signature;
		onRenderedPositions(positions);
	}, [highlights, onRenderedPositions]);

	// --- Hit testing -------------------------------------------------------
	// The rects don't take pointer events (so selection works through them), so
	// pointer interaction is resolved against the geometry we just measured.
	const hitTest = useCallback(
		(clientX: number, clientY: number): PaperHighlight | null => {
			const container = containerRef.current;
			if (!container) return null;
			const containerRect = container.getBoundingClientRect();
			const x = clientX - containerRect.left + container.scrollLeft;
			const y = clientY - containerRect.top + container.scrollTop;

			for (const [page, entries] of byPage) {
				const box = pageBoxes.get(page);
				if (!box) continue;
				if (x < box.left || x > box.left + box.width) continue;
				if (y < box.top || y > box.top + box.height) continue;
				// Last drawn wins, matching what the user sees on overlap.
				for (let i = entries.length - 1; i >= 0; i--) {
					const { highlight, anchor } = entries[i];
					for (const r of anchor.rects) {
						const rx = box.left + (r.left / 100) * box.width;
						const ry = box.top + (r.top / 100) * box.height;
						const rw = (r.width / 100) * box.width;
						const rh = (r.height / 100) * box.height;
						if (x >= rx && x <= rx + rw && y >= ry && y <= ry + rh) {
							return highlight;
						}
					}
				}
			}
			return null;
		},
		[byPage, pageBoxes, containerRef]
	);

	useEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		if (!onHighlightClick && !onHighlightHover) return;

		const onClick = (e: MouseEvent) => {
			// A drag that ends in a selection isn't a click on the highlight.
			if (!window.getSelection()?.isCollapsed) return;
			const hit = hitTest(e.clientX, e.clientY);
			if (hit) onHighlightClick?.(hit);
		};

		let hoveredId: string | null = null;
		const onMove = (e: MouseEvent) => {
			const hit = hitTest(e.clientX, e.clientY);
			const id = hit?.id ?? null;
			if (id === hoveredId) return;
			hoveredId = id;
			onHighlightHover?.(hit, hit ? { x: e.clientX, y: e.clientY } : null);
		};
		// Leaving the pane straight off a highlight never fires another
		// mousemove, so without this the hover card stays up indefinitely.
		const onLeave = () => {
			if (hoveredId === null) return;
			hoveredId = null;
			onHighlightHover?.(null, null);
		};

		container.addEventListener("click", onClick);
		// Only track the pointer when someone is actually listening: the scan is
		// synchronous over every rect on the candidate page, and the caller drops
		// the hover callback whenever inline cards are showing the same content.
		if (onHighlightHover) {
			container.addEventListener("mousemove", onMove);
			container.addEventListener("mouseleave", onLeave);
		}
		return () => {
			container.removeEventListener("click", onClick);
			container.removeEventListener("mousemove", onMove);
			container.removeEventListener("mouseleave", onLeave);
		};
	}, [containerRef, hitTest, onHighlightClick, onHighlightHover]);

	const container = containerRef.current;
	if (!container) return null;

	return createPortal(
		<div className="pdf-highlight-root">
			{Array.from(byPage.entries()).map(([page, entries]) => {
				const box = pageBoxes.get(page);
				if (!box) return null;
				return (
					<div
						key={page}
						className="pdf-highlight-page"
						style={{
							left: box.left,
							top: box.top,
							width: box.width,
							height: box.height,
						}}
					>
						{entries.map(({ key: entryKey, highlight, anchor }) => {
							
							const isActive = Boolean(
								activeHighlightId && highlight.id === activeHighlightId
							);
							const fill =
								highlight.role === "assistant"
									? getAssistantHighlightFill(isActive)
									: getUserHighlightFill(highlight.color, isActive);
							return anchor.rects.map((rect, i) => (
								<div
									key={`${entryKey}:${i}`}
									data-highlight-id={highlight.id ?? ""}
									data-highlight-role={highlight.role}
									className="pdf-highlight-rect"
									style={{
										left: `${rect.left}%`,
										top: `${rect.top}%`,
										width: `${rect.width}%`,
										height: `${rect.height}%`,
										backgroundColor: fill,
									}}
								/>
							));
						})}
					</div>
				);
			})}
		</div>,
		container
	);
}
