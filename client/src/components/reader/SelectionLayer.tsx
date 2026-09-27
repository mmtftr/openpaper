"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Copy, Highlighter, MessageCircle, StickyNote } from "lucide-react";
import type { HighlightColor } from "@/lib/schema";
import { captureAnchor, clearSelection } from "./anchoring";
import { useAtomValue } from "jotai";
import { readerActiveAtom } from "./atoms";
import { useReaderContext } from "./ReaderContext";
import { HIGHLIGHT_COLOR_SWATCHES } from "./highlightColors";
import type { TextAnchor } from "./types";

export interface SelectionLayerProps {
	/** Persist a highlight for this selection. Omit in read-only views. */
	onHighlight?: (anchor: TextAnchor, color: HighlightColor) => void;
	/** Persist a highlight and open a note composer against it. */
	onAnnotate?: (anchor: TextAnchor, color: HighlightColor) => void;
	/** Push the quote into the chat composer as a reference. */
	onAskAi?: (quote: string) => void;
	defaultColor?: HighlightColor;
}

interface SelState {
	anchor: TextAnchor;
	lines: DOMRect[];
	focusAtEnd: boolean;
	pointerX: number | null;
}

type SelectionAction = "highlight" | "annotate" | "ask";

/** Single-key shortcuts, live while the toolbar is showing. */
const SHORTCUT_KEYS: Record<string, SelectionAction> = {
	h: "highlight",
	c: "annotate",
	a: "ask",
};

// Key hints only make sense with a keyboard; touch gets the narrower toolbar.
const TOOLBAR_W_WITH_HINTS = 356;
const TOOLBAR_W_TOUCH = 296;
const TOOLBAR_H = 40;
const VIEWPORT_MARGIN = 8;
const GAP = 8;

/** Group the range's client rects into one rect per visual line. */
function lineRects(range: Range): DOMRect[] {
	const raw = Array.from(range.getClientRects()).filter(
		(r) => r.width >= 1 && r.height >= 1
	);
	const sorted = [...raw].sort((a, b) => a.top - b.top || a.left - b.left);
	const lines: DOMRect[] = [];
	for (const r of sorted) {
		const last = lines[lines.length - 1];
		if (last) {
			const lastMid = last.top + last.height / 2;
			const mid = r.top + r.height / 2;
			if (Math.abs(lastMid - mid) <= Math.max(last.height, r.height) * 0.6) {
				const left = Math.min(last.left, r.left);
				const right = Math.max(last.right, r.right);
				const top = Math.min(last.top, r.top);
				const bottom = Math.max(last.bottom, r.bottom);
				lines[lines.length - 1] = new DOMRect(
					left,
					top,
					right - left,
					bottom - top
				);
				continue;
			}
		}
		lines.push(new DOMRect(r.left, r.top, r.width, r.height));
	}
	return lines;
}

export function ShortcutHint({ children }: { children: string }) {
	return (
		<kbd className="[@media(pointer:coarse)]:hidden rounded border border-border px-1 font-sans text-[10px] leading-4 text-muted-foreground">
			{children}
		</kbd>
	);
}

/**
 * Floating toolbar over the current text selection.
 *
 * Positioning is paper-reader's: anchored to the line the selection *ended* on
 * and to the pointer's x within that line, flipping above the selection when
 * there isn't room below, with the arrow tracking the anchor point. The old
 * menu always rendered below a single (x, y) and clamped, so a selection near
 * the bottom of the viewport put the toolbar off-screen.
 */
export default function SelectionLayer({
	onHighlight,
	onAnnotate,
	onAskAi,
	defaultColor = "blue",
}: SelectionLayerProps) {
	const { containerRef } = useReaderContext();
	const [sel, setSel] = useState<SelState | null>(null);
	const [color, setColor] = useState<HighlightColor>(defaultColor);
	const [pickerOpen, setPickerOpen] = useState(false);
	const toolbarRef = useRef<HTMLDivElement>(null);
	const rangeRef = useRef<Range | null>(null);
	const pointerDownRef = useRef(false);

	const dismiss = useCallback(() => {
		rangeRef.current = null;
		setSel(null);
		setPickerOpen(false);
	}, []);

	// The toolbar is portaled to <body>, outside the hidden reader: drop the
	// selection when the reader goes out of view.
	const active = useAtomValue(readerActiveAtom);
	useEffect(() => {
		if (active) return;
		clearSelection();
		dismiss();
	}, [active, dismiss]);

	const captureFromSelection = useCallback(
		(clientX: number | null): SelState | null => {
			const active = window.getSelection();
			if (!active || active.isCollapsed || active.rangeCount === 0) return null;
			const range = active.getRangeAt(0);
			const container = containerRef.current;
			if (!container || !container.contains(range.commonAncestorContainer))
				return null;
			if (!range.toString().trim()) return null;
			const anchor = captureAnchor(container);
			if (!anchor) return null;
			const lines = lineRects(range);
			if (lines.length === 0) return null;

			// Which end the user finished on decides whether the toolbar prefers
			// to sit below (dragging forwards) or above (dragging backwards).
			let focusAtEnd = true;
			if (active.focusNode) {
				const focusRange = document.createRange();
				focusRange.selectNodeContents(active.focusNode);
				focusRange.setStart(active.focusNode, active.focusOffset);
				focusRange.collapse(true);
				focusAtEnd =
					focusRange.compareBoundaryPoints(Range.START_TO_START, range) > 0;
			}
			rangeRef.current = range;
			return { anchor, lines, focusAtEnd, pointerX: clientX };
		},
		[containerRef]
	);

	useEffect(() => {
		const trackDown = () => {
			pointerDownRef.current = true;
		};
		const trackUp = () => {
			pointerDownRef.current = false;
		};
		const onPointerUp = (e: PointerEvent) => {
			if (e.button !== 0) return;
			if (toolbarRef.current?.contains(e.target as Node)) return;
			const next = captureFromSelection(e.clientX);
			if (next) setSel(next);
		};
		document.addEventListener("pointerdown", trackDown, true);
		document.addEventListener("pointerup", trackUp, true);
		document.addEventListener("pointercancel", trackUp, true);
		document.addEventListener("pointerup", onPointerUp);
		document.addEventListener("pointercancel", onPointerUp);
		return () => {
			document.removeEventListener("pointerdown", trackDown, true);
			document.removeEventListener("pointerup", trackUp, true);
			document.removeEventListener("pointercancel", trackUp, true);
			document.removeEventListener("pointerup", onPointerUp);
			document.removeEventListener("pointercancel", onPointerUp);
		};
	}, [captureFromSelection]);

	// Keyboard selection (shift+arrows, ⌘A) never fires pointerup.
	useEffect(() => {
		const onSelectionChange = () => {
			if (pointerDownRef.current) return;
			const next = captureFromSelection(null);
			if (next) setSel(next);
		};
		document.addEventListener("selectionchange", onSelectionChange);
		return () =>
			document.removeEventListener("selectionchange", onSelectionChange);
	}, [captureFromSelection]);

	useEffect(() => {
		const onPointerDown = (e: PointerEvent) => {
			if (e.button === 2) return;
			if (toolbarRef.current?.contains(e.target as Node)) return;
			dismiss();
		};
		document.addEventListener("pointerdown", onPointerDown);
		return () => document.removeEventListener("pointerdown", onPointerDown);
	}, [dismiss]);

	const runAction = useCallback(
		(action: SelectionAction, anchor: TextAnchor) => {
			if (action === "highlight") {
				if (!onHighlight) return;
				onHighlight(anchor, color);
			} else if (action === "annotate") {
				if (!onAnnotate) return;
				onAnnotate(anchor, color);
			} else {
				if (!onAskAi) return;
				onAskAi(anchor.quote);
			}
			clearSelection();
			dismiss();
		},
		[color, onHighlight, onAnnotate, onAskAi, dismiss]
	);

	useEffect(() => {
		const onKeyDown = (e: KeyboardEvent) => {
			if (e.key === "Escape") {
				dismiss();
				return;
			}
			if (!sel || e.repeat || e.isComposing) return;
			if (e.metaKey || e.ctrlKey || e.altKey) return;
			const target = e.target as HTMLElement | null;
			if (
				target?.tagName === "INPUT" ||
				target?.tagName === "TEXTAREA" ||
				target?.isContentEditable
			)
				return;
			const action = SHORTCUT_KEYS[e.key.toLowerCase()];
			const handler = { highlight: onHighlight, annotate: onAnnotate, ask: onAskAi };
			if (!action || !handler[action]) return;
			// The toolbar can outlive its selection (collapsed by the keyboard, or
			// replaced by one outside the PDF), so act on what's selected now.
			const current = captureFromSelection(null);
			if (!current) return;
			e.preventDefault();
			e.stopPropagation();
			runAction(action, current.anchor);
		};
		document.addEventListener("keydown", onKeyDown);
		return () => document.removeEventListener("keydown", onKeyDown);
	}, [sel, dismiss, runAction, captureFromSelection, onHighlight, onAnnotate, onAskAi]);

	// Keep the toolbar glued to the selection while the page scrolls or resizes.
	const hasSel = sel !== null;
	useEffect(() => {
		if (!hasSel) return;
		const recompute = () => {
			const range = rangeRef.current;
			if (!range || !range.startContainer.isConnected) return;
			const lines = lineRects(range);
			if (lines.length === 0) return;
			setSel((prev) => (prev ? { ...prev, lines } : prev));
		};
		window.addEventListener("resize", recompute);
		document.addEventListener("scroll", recompute, true);
		return () => {
			window.removeEventListener("resize", recompute);
			document.removeEventListener("scroll", recompute, true);
		};
	}, [hasSel]);

	if (!sel || !active) return null;

	const toolbarW = window.matchMedia("(pointer: coarse)").matches
		? TOOLBAR_W_TOUCH
		: TOOLBAR_W_WITH_HINTS;

	const line = sel.focusAtEnd ? sel.lines[sel.lines.length - 1] : sel.lines[0];
	const anchorX =
		sel.pointerX != null
			? Math.min(Math.max(sel.pointerX, line.left), line.right)
			: line.left + line.width / 2;

	const spaceAbove = line.top;
	const spaceBelow = window.innerHeight - line.bottom;
	const needed = TOOLBAR_H + GAP + 16;
	const above = sel.focusAtEnd
		? !(spaceBelow >= needed || spaceBelow >= spaceAbove)
		: spaceAbove >= needed || spaceAbove >= spaceBelow;

	let x = anchorX - toolbarW / 2;
	x = Math.max(
		VIEWPORT_MARGIN,
		Math.min(x, window.innerWidth - toolbarW - VIEWPORT_MARGIN)
	);
	const arrowX = Math.min(Math.max(anchorX - x, 18), toolbarW - 18);
	const y = above ? line.top - GAP - TOOLBAR_H : line.bottom + GAP;

	const finish = () => {
		clearSelection();
		dismiss();
	};

	return createPortal(
		<div
			ref={toolbarRef}
			data-reader-selection-toolbar="true"
			className="fixed z-50"
			style={{ left: x, top: y, width: toolbarW }}
		>
			<div
				className={`pointer-events-none absolute size-2 rotate-45 border-border bg-popover ${
					above ? "top-full -mt-1 border-r border-b" : "bottom-full -mb-1 border-t border-l"
				}`}
				style={{ left: arrowX - 4 }}
			/>
			<div className="flex items-center gap-0.5 rounded-xl border border-border bg-popover/95 p-1 shadow-xl backdrop-blur">
				{onHighlight && (
				<button
					title="Highlight (H)"
					onClick={() => runAction("highlight", sel.anchor)}
					className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
				>
					<Highlighter className="size-3.5 text-blue-500" /> Highlight
					<ShortcutHint>H</ShortcutHint>
				</button>
				)}

				{onHighlight && (
				<div className="relative">
					<button
						aria-label="Highlight colour"
						onClick={() => setPickerOpen((v) => !v)}
						className="flex size-7 items-center justify-center rounded-lg hover:bg-muted"
					>
						<span
							className={`size-3.5 rounded-full ring-1 ring-border ${
								HIGHLIGHT_COLOR_SWATCHES.find((s) => s.color === color)?.bg
							}`}
						/>
					</button>
					{pickerOpen && (
						<div className="absolute left-0 top-full z-10 mt-1 flex gap-1 rounded-lg border border-border bg-popover p-1 shadow-lg">
							{HIGHLIGHT_COLOR_SWATCHES.map((swatch) => (
								<button
									key={swatch.color}
									title={swatch.label}
									aria-label={swatch.label}
									onClick={() => {
										setColor(swatch.color);
										setPickerOpen(false);
									}}
									className={`size-4 rounded-full ring-1 ring-border ${swatch.bg} ${
										swatch.color === color ? "ring-2 ring-blue-500" : ""
									}`}
								/>
							))}
						</div>
					)}
				</div>
				)}

				{onAnnotate && (
				<button
					title="Comment (C)"
					onClick={() => runAction("annotate", sel.anchor)}
					className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
				>
					<StickyNote className="size-3.5 text-blue-500" /> Comment
					<ShortcutHint>C</ShortcutHint>
				</button>
				)}

				{onAskAi && (
				<button
					title="Ask AI (A)"
					onClick={() => runAction("ask", sel.anchor)}
					className="flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
				>
					<MessageCircle className="size-3.5 text-blue-500" /> Ask
					<ShortcutHint>A</ShortcutHint>
				</button>
				)}

				<button
					title="Copy"
					aria-label="Copy selection"
					onClick={() => {
						navigator.clipboard.writeText(sel.anchor.quote).catch(() => {});
						finish();
					}}
					className="ml-auto flex size-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground"
				>
					<Copy className="size-3.5" />
				</button>
			</div>
		</div>,
		document.body
	);
}
