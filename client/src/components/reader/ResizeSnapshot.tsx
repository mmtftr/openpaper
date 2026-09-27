"use client";

import { useEffect, useLayoutEffect, useRef } from "react";
import type { PDFViewer } from "./pdfjs";

// Mirrors of pdf.js internals (web/pdf_viewer.mjs, v5.5) used by `fitScale`.
const MAX_AUTO_SCALE = 1.25;
const SCROLLBAR_PADDING = 40;
const VERTICAL_PADDING = 5;
const SCROLL_MODE_HORIZONTAL = 1;
const RENDERING_FINISHED = 3;

const FADE_MS = 100;
/** Drop the snapshot even if pdf.js is still busy after this long. */
const SETTLE_CAP_MS = 600;
const USER_INTENT_EVENTS = ["wheel", "pointerdown", "touchstart", "keydown"] as const;

interface PageViewLike {
	width: number;
	height: number;
	scale: number;
	renderingState: number;
}

/**
 * The scale pdf.js's named fit would pick for a scroller of this client size,
 * following `PDFViewer#setScale`. Null for a numeric zoom, which a pane
 * resize doesn't change.
 */
function fitScale(viewer: PDFViewer, clientWidth: number, clientHeight: number): number | null {
	const value = viewer.currentScaleValue;
	if (parseFloat(value) > 0) return null;
	const pageView = viewer.getPageView(viewer.currentPageNumber - 1) as PageViewLike | undefined;
	if (!pageView?.width || !pageView.height) return null;
	const horizontal = viewer.scrollMode === SCROLL_MODE_HORIZONTAL;
	const [hPad, vPad] = horizontal
		? [VERTICAL_PADDING, SCROLLBAR_PADDING]
		: [SCROLLBAR_PADDING, VERTICAL_PADDING];
	const spreadFactor = viewer.spreadMode !== 0 && !horizontal ? 2 : 1;
	const pageWidth = (((clientWidth - hPad) / pageView.width) * pageView.scale) / spreadFactor;
	const pageHeight = ((clientHeight - vPad) / pageView.height) * pageView.scale;
	switch (value) {
		case "page-width":
			return pageWidth;
		case "page-fit":
			return Math.min(pageWidth, pageHeight);
		case "auto":
			return Math.min(
				MAX_AUTO_SCALE,
				pageView.width <= pageView.height ? pageWidth : Math.min(pageWidth, pageHeight)
			);
		default:
			return null;
	}
}

interface SnapshotPage {
	box: HTMLElement;
	/**
	 * Signed sum of the gaps between this page's row and the row at the top of
	 * the viewport. Pages scale with the zoom but pdf.js's page gaps don't, so
	 * each page shifts by `gaps * (1/s - 1)` inside the scaled stage.
	 */
	gaps: number;
}

/**
 * Copies what the scroller currently shows into `stage`, in scroller-viewport
 * coordinates: every page from the top of the viewport down to a few
 * viewport-heights below (a narrower pane shows more of the document), as its
 * already-rendered canvas or a blank page-coloured box, with its highlights.
 */
function buildStage(container: HTMLElement, stage: HTMLElement): SnapshotPage[] {
	const origin = container.getBoundingClientRect();
	const reach = container.clientHeight * 3;
	const highlightRoot = container.querySelector<HTMLElement>(".pdf-highlight-root");
	const highlightPages = highlightRoot
		? Array.from(highlightRoot.querySelectorAll<HTMLElement>(".pdf-highlight-page"))
		: [];
	let pageBackground: string | null = null;
	let canvasFilter: string | null = null;
	const snapshot: (SnapshotPage & { top: number; bottom: number })[] = [];
	const fragment = document.createDocumentFragment();

	for (const page of container.querySelectorAll<HTMLElement>(".pdfViewer .page")) {
		const rect = page.getBoundingClientRect();
		const left = rect.left - origin.left;
		const top = rect.top - origin.top;
		if (top + rect.height < 0 || top > reach || left + rect.width < 0 || left > origin.width) continue;
		pageBackground ??= getComputedStyle(page).backgroundColor;
		const box = document.createElement("div");
		box.style.cssText = `position:absolute;overflow:hidden;left:${left}px;top:${top}px;width:${rect.width}px;height:${rect.height}px;background:${pageBackground}`;

		for (const source of page.querySelectorAll<HTMLCanvasElement>(".canvasWrapper canvas")) {
			if (source.hidden || !source.width || !source.height) continue;
			// Dark mode inverts `.canvasWrapper`, which the copy isn't inside.
			canvasFilter ??= getComputedStyle(source.parentElement ?? source).filter;
			const at = source.getBoundingClientRect();
			const copy = document.createElement("canvas");
			copy.width = source.width;
			copy.height = source.height;
			copy.getContext("2d")?.drawImage(source, 0, 0);
			copy.style.cssText = `position:absolute;left:${at.left - rect.left}px;top:${at.top - rect.top}px;width:${at.width}px;height:${at.height}px;filter:${canvasFilter}`;
			box.append(copy);
		}

		// The highlight layer keeps one box per page in scroll-content
		// coordinates; carry this page's into the copy (inside a bare root, which
		// holds the colour variables). Painted after the canvas and in the same
		// stacking context, so `mix-blend-mode` still tints the text.
		if (highlightRoot) {
			const contentLeft = left + container.scrollLeft;
			const contentTop = top + container.scrollTop;
			const match = highlightPages.find(
				(hp) =>
					Math.abs(parseFloat(hp.style.left) - contentLeft) < 2 &&
					Math.abs(parseFloat(hp.style.top) - contentTop) < 2
			);
			if (match) {
				const root = highlightRoot.cloneNode(false) as HTMLElement;
				const clone = match.cloneNode(true) as HTMLElement;
				clone.style.left = "0px";
				clone.style.top = "0px";
				root.append(clone);
				box.append(root);
			}
		}
		fragment.append(box);
		snapshot.push({ box, gaps: 0, top, bottom: top + rect.height });
	}
	stage.replaceChildren(fragment);

	// Rows (a spread puts two pages side by side), then gaps counted outwards
	// from the row at the top of the viewport, which is what pdf.js keeps put.
	const rows: { top: number; bottom: number; pages: SnapshotPage[] }[] = [];
	for (const page of snapshot.sort((a, b) => a.top - b.top)) {
		const row = rows.at(-1);
		if (row && Math.abs(row.top - page.top) < 2) {
			row.bottom = Math.max(row.bottom, page.bottom);
			row.pages.push(page);
		} else {
			rows.push({ top: page.top, bottom: page.bottom, pages: [page] });
		}
	}
	const anchor = Math.max(0, rows.findIndex((row) => row.bottom > 0));
	const rowGaps = rows.map(() => 0);
	for (let i = anchor + 1; i < rows.length; i++) rowGaps[i] = rowGaps[i - 1] + rows[i].top - rows[i - 1].bottom;
	for (let i = anchor - 1; i >= 0; i--) rowGaps[i] = rowGaps[i + 1] - (rows[i + 1].top - rows[i].bottom);
	rows.forEach((row, i) => row.pages.forEach((page) => (page.gaps = rowGaps[i])));
	return snapshot;
}

function visiblePagesRendered(viewer: PDFViewer, container: HTMLElement) {
	const view = container.getBoundingClientRect();
	for (const page of container.querySelectorAll<HTMLElement>(".pdfViewer .page")) {
		const rect = page.getBoundingClientRect();
		if (rect.bottom <= view.top || rect.top >= view.bottom) continue;
		const pageView = viewer.getPageView(Number(page.dataset.pageNumber) - 1) as PageViewLike | undefined;
		if (pageView && pageView.renderingState !== RENDERING_FINISHED) return false;
	}
	return true;
}

interface Frozen {
	width: number;
	observer: ResizeObserver;
}

/**
 * While `resizing`, pins the pdf.js scroller at its current width (so neither
 * pdf.js nor our own resize observers see a change) and covers it with a
 * snapshot of the visible pages that is only CSS-transformed to track the
 * pane. When `resizing` ends the scroller takes the real width once, pdf.js
 * refits (keeping the reading position, as for any scale change) and the
 * snapshot fades out as soon as the visible pages have re-rendered.
 */
export function ResizeSnapshot({
	resizing,
	containerRef,
	viewer,
}: {
	resizing: boolean;
	containerRef: React.RefObject<HTMLDivElement | null>;
	viewer: PDFViewer | null;
}) {
	const overlayRef = useRef<HTMLDivElement>(null);
	const stageRef = useRef<HTMLDivElement>(null);
	const frozenRef = useRef<Frozen | null>(null);
	/** Tears down a pending settle/fade. */
	const settleCleanupRef = useRef<(() => void) | null>(null);

	useLayoutEffect(() => {
		const container = containerRef.current;
		const overlay = overlayRef.current;
		const stage = stageRef.current;
		if (!container || !overlay || !stage) return;

		const hide = () => {
			overlay.hidden = true;
			overlay.style.transition = "";
			overlay.style.opacity = "";
			stage.replaceChildren();
		};
		const unfreeze = () => {
			const frozen = frozenRef.current;
			if (!frozen) return null;
			frozenRef.current = null;
			frozen.observer.disconnect();
			container.style.width = "";
			container.style.right = "";
			container.style.isolation = "";
			if (overlay.parentElement) overlay.parentElement.style.overflow = "";
			return frozen;
		};

		if (resizing) {
			settleCleanupRef.current?.();
			if (frozenRef.current || !viewer || !viewer.pagesCount || !container.clientWidth) return;

			const width = container.offsetWidth;
			const clientWidth = container.clientWidth;
			const scrollbar = width - clientWidth;
			const baseScale = fitScale(viewer, clientWidth, container.clientHeight);
			const pages = buildStage(container, stage);
			stage.style.width = `${width}px`;
			stage.style.height = `${container.clientHeight}px`;
			stage.style.transform = "";

			container.style.width = `${width}px`;
			container.style.right = "auto";
			// pdf.js gives its annotation layers z-indexes; contained here, they
			// can't paint through the snapshot.
			container.style.isolation = "isolate";
			// A pinned scroller wider than a shrinking pane must not overflow it.
			if (overlay.parentElement) overlay.parentElement.style.overflow = "hidden";
			overlay.style.transition = "";
			overlay.style.opacity = "1";
			overlay.hidden = false;

			// Horizontally, pdf.js centres pages in the scroller's client area;
			// vertically it keeps the point at the top of the viewport in place.
			const track = (paneWidth: number) => {
				const newClientWidth = paneWidth - scrollbar;
				const nextScale = baseScale ? fitScale(viewer, newClientWidth, container.clientHeight) : null;
				const s = baseScale && nextScale ? nextScale / baseScale : 1;
				const tx = newClientWidth / 2 - (s * clientWidth) / 2;
				stage.style.transform = `translate3d(${tx}px,0,0) scale(${s})`;
				for (const { box, gaps } of pages) {
					box.style.transform = gaps ? `translate3d(0,${gaps * (1 / s - 1)}px,0)` : "";
				}
			};
			const observer = new ResizeObserver(() => track(overlay.clientWidth));
			observer.observe(overlay);
			frozenRef.current = { width, observer };
			return;
		}

		const frozen = unfreeze();
		if (!frozen) return;
		if (Math.abs(overlay.clientWidth - frozen.width) < 1 || !viewer) {
			hide();
			return;
		}

		// Keep the snapshot up until pdf.js has refit and redrawn what's visible.
		let raf = 0;
		let fadeTimer: ReturnType<typeof setTimeout> | undefined;
		const stopWaiting = () => {
			clearTimeout(capTimer);
			cancelAnimationFrame(raf);
			viewer.eventBus.off("pagerendered", check);
			for (const type of USER_INTENT_EVENTS) container.removeEventListener(type, finish, true);
		};
		const finish = () => {
			stopWaiting();
			if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
				settleCleanupRef.current = null;
				hide();
				return;
			}
			overlay.style.transition = `opacity ${FADE_MS}ms ease-out`;
			overlay.style.opacity = "0";
			fadeTimer = setTimeout(() => {
				settleCleanupRef.current = null;
				hide();
			}, FADE_MS + 20);
		};
		const check = () => {
			if (visiblePagesRendered(viewer, container)) finish();
		};
		settleCleanupRef.current = () => {
			stopWaiting();
			clearTimeout(fadeTimer);
			settleCleanupRef.current = null;
			hide();
		};
		const capTimer = setTimeout(finish, SETTLE_CAP_MS);
		viewer.eventBus.on("pagerendered", check);
		// The user reaching for the page means the snapshot is in the way. Not
		// `scroll`: the refit itself scrolls to keep the reading position.
		for (const type of USER_INTENT_EVENTS) container.addEventListener(type, finish, { capture: true, passive: true });
		// Two frames: the resize observers that trigger the refit run after
		// this frame's layout.
		raf = requestAnimationFrame(() => {
			raf = requestAnimationFrame(check);
		});
	}, [resizing, containerRef, viewer]);

	// Unmounting mid-resize must not leave the scroller pinned.
	useEffect(() => {
		const container = containerRef.current;
		const overlay = overlayRef.current;
		return () => {
			settleCleanupRef.current?.();
			const frozen = frozenRef.current;
			if (!frozen) return;
			frozenRef.current = null;
			frozen.observer.disconnect();
			if (container) {
				container.style.width = "";
				container.style.right = "";
				container.style.isolation = "";
			}
			if (overlay?.parentElement) overlay.parentElement.style.overflow = "";
		};
	}, [containerRef]);

	return (
		<div
			ref={overlayRef}
			hidden
			aria-hidden
			className="pointer-events-none absolute inset-0 overflow-hidden bg-muted"
			style={{ contain: "strict" }}
		>
			<div ref={stageRef} className="absolute top-0 left-0 origin-top-left will-change-transform" />
		</div>
	);
}
