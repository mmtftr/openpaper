"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FocusEvent, RefObject } from "react";
import { createPortal } from "react-dom";
import { ArrowUpRight, Copy, MessageCircle, StickyNote, Trash2 } from "lucide-react";
import type { BasicUser } from "@/lib/auth";
import type {
	HighlightColor,
	PaperHighlight,
	PaperHighlightAnnotation,
} from "@/lib/schema";
import { InlineAnnotationCard } from "@/components/InlineAnnotationCard";
import { useReaderContext } from "./ReaderContext";
import { READER_POPOVER_SELECTOR } from "./HighlightLayer";
import { HIGHLIGHT_COLOR_SWATCHES } from "./highlightColors";
import { ShortcutHint } from "./SelectionLayer";
import { toScrollerContent, visibleBounds } from "./visibleBounds";
import type { HighlightPopoverTarget } from "./useHighlightPopover";

/** Roomy on desktop; phones clamp it to the pane (minus MARGIN each side). */
const NOTE_CARD_WIDTH = 400;
const GAP = 8;
const MARGIN = 8;
const MIN_HEIGHT = 140;

interface Placement {
	left: number;
	top: number;
	width: number;
	maxHeight: number;
}

function isTyping(target: EventTarget | null): boolean {
	const el = target as HTMLElement | null;
	return (
		el?.tagName === "INPUT" || el?.tagName === "TEXTAREA" || Boolean(el?.isContentEditable)
	);
}

/**
 * Places the popover against one line rect of its highlight, in the scroll
 * container's content coordinates (it's portalled into the container, so it
 * scrolls with the page for free).
 *
 * Below vs. above — and the height budget that goes with it — is decided once
 * per target: pdf.js mutates the DOM while scrolling, and re-deciding against
 * a highlight that has scrolled out of view would flip an engaged popover
 * around under the user's cursor.
 */
function usePopoverPlacement(
	containerRef: RefObject<HTMLDivElement | null>,
	target: HighlightPopoverTarget | null,
	elRef: RefObject<HTMLDivElement | null>,
	fixedWidth: number | undefined,
	naturalHeight: (el: HTMLElement) => number
): Placement | null {
	const [placement, setPlacement] = useState<Placement | null>(null);
	const decided = useRef<{
		key: string;
		below: boolean;
		maxHeight: number;
		paneW: number;
		paneH: number;
	} | null>(null);
	// The toolbar and the note card are different boxes: switching between them
	// (Comment on a bare highlight) needs a fresh decision and a fresh observer.
	const targetKey = target
		? `${target.highlightId}:${target.rectIndex}:${fixedWidth ?? "auto"}`
		: null;

	const measure = useCallback(() => {
		const container = containerRef.current;
		const el = elRef.current;
		if (!container || !el || !target || !targetKey) {
			setPlacement(null);
			return;
		}
		const rects = container.querySelectorAll<HTMLElement>(
			`.pdf-highlight-rect[data-highlight-id="${CSS.escape(target.highlightId)}"]`
		);
		// A just-created highlight isn't drawn until the layer re-measures; the
		// mutation observer below calls back once it is.
		if (rects.length === 0) return;
		let anchorEl = rects[rects.length - 1];
		if (target.rectIndex >= 0) {
			const match = Array.from(rects).find(
				(r) => r.dataset.rectIndex === String(target.rectIndex)
			);
			if (match) anchorEl = match;
		}

		const a = anchorEl.getBoundingClientRect();
		// What's on screen of the pane, not the (possibly zoomed-wider) pages.
		const vis = visibleBounds(container);
		const visibleBottom = vis.bottom - MARGIN;
		const width = Math.min(fixedWidth ?? el.offsetWidth, vis.right - vis.left - MARGIN * 2);
		const natural = naturalHeight(el);

		// Re-decide on a new target, or when the pane itself was resized (window,
		// side panel, focus mode) — the old height budget no longer fits.
		if (
			decided.current?.key !== targetKey ||
			decided.current.paneW !== container.clientWidth ||
			decided.current.paneH !== container.clientHeight
		) {
			const below = visibleBottom - a.bottom - GAP;
			const above = a.top - GAP - (vis.top + MARGIN);
			const placeBelow = below >= natural || below >= above;
			decided.current = {
				key: targetKey,
				below: placeBelow,
				maxHeight: Math.max(MIN_HEIGHT, placeBelow ? below : above),
				paneW: container.clientWidth,
				paneH: container.clientHeight,
			};
		}
		const { below, maxHeight } = decided.current;
		const height = Math.min(natural, maxHeight);
		const vpTop = below ? a.bottom + GAP : a.top - GAP - height;
		const vpLeft = Math.max(
			vis.left + MARGIN,
			Math.min(a.left + a.width / 2 - width / 2, vis.right - width - MARGIN)
		);
		const content = toScrollerContent(container, vpLeft, vpTop);
		const next: Placement = {
			left: content.left,
			top: content.top,
			width,
			maxHeight,
		};
		setPlacement((prev) =>
			prev &&
			Math.abs(prev.left - next.left) < 0.5 &&
			Math.abs(prev.top - next.top) < 0.5 &&
			Math.abs(prev.width - next.width) < 0.5 &&
			prev.maxHeight === next.maxHeight
				? prev
				: next
		);
	}, [containerRef, elRef, target, targetKey, fixedWidth, naturalHeight]);

	useLayoutEffect(() => {
		if (!targetKey) decided.current = null;
		measure();
	}, [measure, targetKey]);

	// Zoom, spread changes, pane resizes, the highlight being (re)drawn, and the
	// popover's own content growing all move the anchor or the box.
	useEffect(() => {
		const container = containerRef.current;
		if (!container || !targetKey) return;
		let frame: number | null = null;
		const schedule = () => {
			if (frame != null) return;
			frame = requestAnimationFrame(() => {
				frame = null;
				measure();
			});
		};
		const resize = new ResizeObserver(schedule);
		resize.observe(container);
		const viewerEl = container.querySelector(".pdfViewer");
		if (viewerEl) resize.observe(viewerEl);
		if (elRef.current) resize.observe(elRef.current);
		const mutation = new MutationObserver(schedule);
		mutation.observe(container, { childList: true, subtree: true });
		window.addEventListener("resize", schedule);
		return () => {
			if (frame != null) cancelAnimationFrame(frame);
			resize.disconnect();
			mutation.disconnect();
			window.removeEventListener("resize", schedule);
		};
	}, [containerRef, elRef, measure, targetKey]);

	return placement;
}

const noteCardHeight = (el: HTMLElement) =>
	el.querySelector<HTMLElement>("[data-inline-annotation-card]")?.scrollHeight ??
	el.offsetHeight;
const ownHeight = (el: HTMLElement) => el.offsetHeight;

export interface HighlightPopoverProps {
	target: HighlightPopoverTarget | null;
	/** The target's highlight, resolved from current state (null once deleted). */
	highlight: PaperHighlight | null;
	notes: PaperHighlightAnnotation[];
	currentUser?: BasicUser | null;

	addAnnotation?: (highlightId: string, content: string) => Promise<PaperHighlightAnnotation>;
	updateAnnotation?: (annotationId: string, content: string) => Promise<unknown> | void;
	removeAnnotation?: (annotationId: string) => void;

	/** Highlight-toolbar actions (highlights without notes). */
	onRecolor?: (highlight: PaperHighlight, color: HighlightColor) => void;
	onDeleteHighlight?: (highlight: PaperHighlight) => void;
	onAskAi?: (quote: string) => void;
	/** Jump to this thread in the Annotations side panel. */
	onOpenThread?: (highlight: PaperHighlight) => void;

	// Controller (useHighlightPopover)
	close: () => void;
	clickAway: () => void;
	popoverEnter: () => void;
	popoverLeave: () => void;
	engageCurrent: () => void;
	setCompose: (highlightId: string) => void;
	setDirty: (dirty: boolean) => void;
	isDirty: () => boolean;
	cancelPending: () => void;
}

/**
 * What hovering (or clicking) a highlight shows: its note thread — fully
 * editable, like the margin cards it replaces — or, for a highlight without
 * notes, the same actions the selection toolbar offers.
 */
export function HighlightPopover(props: HighlightPopoverProps) {
	const {
		target,
		highlight,
		notes,
		addAnnotation,
		updateAnnotation,
		removeAnnotation,
		onRecolor,
		onDeleteHighlight,
		onAskAi,
		onOpenThread,
		close,
		clickAway,
		popoverEnter,
		popoverLeave,
		engageCurrent,
		setCompose,
		setDirty,
		isDirty,
		cancelPending,
	} = props;
	const { containerRef } = useReaderContext();
	const elRef = useRef<HTMLDivElement>(null);

	const showNotes = Boolean(target && (notes.length > 0 || (target.compose && addAnnotation)));
	const placement = usePopoverPlacement(
		containerRef,
		highlight ? target : null,
		elRef,
		showNotes ? NOTE_CARD_WIDTH : undefined,
		showNotes ? noteCardHeight : ownHeight
	);

	// The target's highlight was deleted (here or elsewhere).
	useEffect(() => {
		if (target && !highlight) close();
	}, [target, highlight, close]);

	const engaged = target?.engaged ?? false;

	// Scrolling dismisses a transient popover, like the citation preview, and
	// drops one about to open; an engaged one stays put in the page.
	useEffect(() => {
		const container = containerRef.current;
		if (!container || engaged) return;
		const onScroll = () => {
			cancelPending();
			if (target) close();
		};
		container.addEventListener("scroll", onScroll, { passive: true });
		return () => container.removeEventListener("scroll", onScroll);
	}, [containerRef, target, engaged, close, cancelPending]);

	// Engaging moves focus in (so Escape and Tab work from the keyboard);
	// closing hands it back to wherever it was.
	const restoreFocusRef = useRef<HTMLElement | null>(null);
	useEffect(() => {
		if (!engaged) return;
		const el = elRef.current;
		if (!el || el.contains(document.activeElement)) return;
		if (document.activeElement instanceof HTMLElement) {
			restoreFocusRef.current = document.activeElement;
		}
		el.focus({ preventScroll: true });
	}, [engaged, target?.highlightId, showNotes]);
	useEffect(() => {
		if (target) return;
		const back = restoreFocusRef.current;
		restoreFocusRef.current = null;
		if (back?.isConnected && document.activeElement === document.body) {
			back.focus({ preventScroll: true });
		}
	}, [target]);

	// Clicks outside the pages. Clicks *on* the pages are HighlightLayer's to
	// judge (a click on another highlight re-targets rather than closes).
	useEffect(() => {
		if (!target) return;
		const onDocClick = (e: MouseEvent) => {
			// The path is fixed at dispatch. By the time this listener runs, React
			// may already have re-rendered the popover (e.g. toolbar → composer on
			// Comment) and detached the clicked button, so `closest()` on the
			// target would wrongly report a click outside.
			const path = e.composedPath();
			const container = containerRef.current;
			for (const node of path) {
				if (node === container) return;
				if (node instanceof Element && node.matches(READER_POPOVER_SELECTOR)) return;
			}
			clickAway();
		};
		document.addEventListener("click", onDocClick);
		return () => document.removeEventListener("click", onDocClick);
	}, [target, containerRef, clickAway]);

	const quote = highlight?.raw_text ?? "";
	const isUserHighlight = highlight?.role === "user";
	const canCompose = Boolean(addAnnotation);


	// Escape closes; C / A act on a note-less highlight like on a selection.
	useEffect(() => {
		if (!target || !highlight) return;
		const onKeyDown = (e: KeyboardEvent) => {
			if (e.metaKey || e.ctrlKey || e.altKey || e.isComposing) return;
			if (isTyping(e.target)) return;
			if (e.key === "Escape") {
				close();
				return;
			}
			if (showNotes || e.repeat) return;
			// A live selection belongs to the selection toolbar's shortcuts.
			const sel = window.getSelection();
			if (sel && !sel.isCollapsed && sel.toString().trim()) return;
			const key = e.key.toLowerCase();
			if (key === "c" && canCompose) {
				e.preventDefault();
				setCompose(target.highlightId);
			} else if (key === "a" && onAskAi) {
				e.preventDefault();
				onAskAi(quote);
				close();
			}
		};
		document.addEventListener("keydown", onKeyDown);
		return () => document.removeEventListener("keydown", onKeyDown);
	}, [target, highlight, showNotes, canCompose, onAskAi, quote, setCompose, close]);

	const [visible, setVisible] = useState(false);
	useEffect(() => {
		if (!placement) {
			setVisible(false);
			return;
		}
		const id = requestAnimationFrame(() => setVisible(true));
		return () => cancelAnimationFrame(id);
	}, [placement === null]); // eslint-disable-line react-hooks/exhaustive-deps

	const container = containerRef.current;
	if (!target || !highlight || !container) return null;

	const shellStyle = {
		position: "absolute" as const,
		left: placement?.left ?? 0,
		top: placement?.top ?? 0,
		zIndex: 30,
		// Not `visibility: hidden` while unmeasured: that would make the
		// composer's autofocus (which runs before placement lands) a no-op.
		opacity: placement && visible ? 1 : 0,
		pointerEvents: placement ? ("auto" as const) : ("none" as const),
		transition: "opacity 150ms ease-out",
	};
	const shellHandlers = {
		onMouseEnter: popoverEnter,
		onMouseLeave: popoverLeave,
		// Capture: the card stops propagation of its own mouse events.
		onMouseDownCapture: engageCurrent,
		onFocusCapture: engageCurrent,
		// Leaving a text field with the pointer already gone counts as leaving.
		onBlurCapture: (e: FocusEvent) => {
			const el = elRef.current;
			if (!el || el.contains(e.relatedTarget as Node | null)) return;
			if (!el.matches(":hover")) popoverLeave();
		},
	};

	if (showNotes) {
		return createPortal(
			<div
				ref={elRef}
				data-reader-popover=""
				role={engaged ? "dialog" : "group"}
				aria-label="Notes on highlight"
				tabIndex={-1}
				className="outline-none"
				style={{ ...shellStyle, width: placement?.width ?? NOTE_CARD_WIDTH }}
				{...shellHandlers}
			>
				<InlineAnnotationCard
					key={target.highlightId}
					highlightId={target.highlightId}
					widthPx={placement?.width ?? NOTE_CARD_WIDTH}
					className="z-auto"
					// Inline: the card's own `overflow-hidden` class would win over a
					// utility override, and the thread must scroll within the budget.
					style={{
						maxHeight: placement?.maxHeight,
						overflowY: "auto",
						overscrollBehavior: "contain",
					}}
					annotations={notes}
					addAnnotation={addAnnotation}
					updateAnnotation={updateAnnotation}
					removeAnnotation={removeAnnotation}
					onClose={close}
					onDirtyChange={setDirty}
					cornerAction={
						onOpenThread ? (
							<button
								type="button"
								title="Open in Annotations"
								aria-label="Open in Annotations"
								onClick={(e) => {
									e.stopPropagation();
									onOpenThread(highlight);
									// An unsaved reply stays here rather than vanishing.
									if (!isDirty()) close();
								}}
								className="relative flex size-7 items-center justify-center rounded-lg text-muted-foreground transition-colors after:absolute after:-inset-1.5 hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 md:after:hidden"
							>
								<ArrowUpRight className="size-4" />
							</button>
						) : undefined
					}
				/>
			</div>,
			container
		);
	}

	const btn =
		"flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-foreground hover:bg-muted";
	return createPortal(
		<div
			ref={elRef}
			data-reader-popover=""
			role="toolbar"
			aria-label="Highlight actions"
			tabIndex={-1}
			className="outline-none"
			// max-content: an auto width would shrink to the room left of `left`,
			// which in horizontally scrolled (zoomed) pages is next to nothing.
			style={{
				...shellStyle,
				width: "max-content",
				maxWidth: `calc(100% - ${MARGIN * 2}px)`,
			}}
			{...shellHandlers}
		>
			<div className="flex flex-wrap items-center gap-0.5 whitespace-nowrap rounded-xl border border-border bg-popover/95 p-1 shadow-xl backdrop-blur">
				{onRecolor && isUserHighlight && (
					<div className="flex items-center gap-1 px-1.5">
						{HIGHLIGHT_COLOR_SWATCHES.map((swatch) => (
							<button
								key={swatch.color}
								type="button"
								title={swatch.label}
								aria-label={`Change colour to ${swatch.label}`}
								aria-pressed={highlight.color === swatch.color}
								onClick={() => onRecolor(highlight, swatch.color)}
								className={`size-3.5 rounded-full ring-1 ring-border ${swatch.bg} ${
									highlight.color === swatch.color ? "ring-2 ring-blue-500" : ""
								}`}
							/>
						))}
					</div>
				)}
				{canCompose && (
					<button
						type="button"
						title="Comment (C)"
						onClick={() => setCompose(target.highlightId)}
						className={btn}
					>
						<StickyNote className="size-3.5 text-blue-500" /> Comment
						<ShortcutHint>C</ShortcutHint>
					</button>
				)}
				{onAskAi && (
					<button
						type="button"
						title="Ask AI (A)"
						onClick={() => {
							onAskAi(quote);
							close();
						}}
						className={btn}
					>
						<MessageCircle className="size-3.5 text-blue-500" /> Ask
						<ShortcutHint>A</ShortcutHint>
					</button>
				)}
				<button
					type="button"
					title="Copy"
					aria-label="Copy highlighted text"
					onClick={() => {
						navigator.clipboard.writeText(quote).catch(() => {});
						close();
					}}
					className="flex size-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
				>
					<Copy className="size-3.5" />
				</button>
				{onDeleteHighlight && isUserHighlight && (
					<button
						type="button"
						title="Delete highlight"
						aria-label="Delete highlight"
						onClick={() => {
							onDeleteHighlight(highlight);
							close();
						}}
						className="flex size-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-destructive"
					>
						<Trash2 className="size-3.5" />
					</button>
				)}
			</div>
		</div>,
		container
	);
}
