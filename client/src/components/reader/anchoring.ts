"use client";

import type { ScaledPosition, ScaledRect } from "@/lib/schema";
import { pdfjsLib, type PDFDocumentProxy } from "./pdfjs";
import type { PercentRect, TextAnchor } from "./types";
import {
	foldPdfMatchChar,
	normalizeForPdfMatch,
	findServerAlignedMatchInNormalizedPdfText,
	SOFT_HYPHEN,
} from "./textNormalization";

/**
 * Highlight geometry, in one place.
 *
 * Everything the reader draws is expressed as a percentage of its page box.
 * That single choice removes the entire class of bugs documented in
 * PDF_HIGHLIGHTER_CAVEATS.md §5: percentages don't change when the user zooms,
 * resizes the side panel, or switches spread mode, so nothing has to be
 * re-measured on layout and no MutationObserver has to rebuild overlays.
 *
 * Two coordinate systems meet here:
 *
 *  - **Stored** (`ScaledPosition`, unchanged in the database): rect coords in
 *    unrotated PDF points with a *top-left* origin, carried alongside the page
 *    dimensions they were measured against. Both producers agree on this —
 *    `jobs/src/highlight_anchor.py` (PyMuPDF `search_for`) and the old
 *    client-side text-layer anchoring — and neither sets `usePdfCoordinates`.
 *  - **Rendered** (`PercentRect`): the same box as a share of the page.
 *
 * Conversion is therefore linear, and lossless in both directions.
 */

/** Percentages below this are treated as measurement noise, not a real box. */
const MIN_RECT_PERCENT = 0.05;

/**
 * Baseline-to-top offset as a fraction of font height.
 *
 * pdf.js derives this per font from real canvas metrics via a private
 * `#getAscent`, which isn't reachable from outside the library. 0.8 is the
 * typical value for the serif faces papers use, and a highlight is a soft band
 * — being a couple of percent out is invisible, whereas using the full font
 * height (the obvious-looking choice) is not.
 */
const ASCENT_RATIO = 0.8;

/** Unicode combining marks, folded together with the base character. */
const COMBINING_MARK_RE = /\p{M}/u;

function toPercentRect(rect: ScaledRect, usePdfCoordinates: boolean): PercentRect {
	const pageWidth = rect.width || 1;
	const pageHeight = rect.height || 1;

	// `usePdfCoordinates` means the rect is in PDF-native space, whose origin is
	// the bottom-left corner. Nothing in this codebase writes it (see the note
	// in jobs/src/highlight_anchor.py), but a legacy row carrying the flag would
	// render mirrored vertically if we ignored it.
	const top = usePdfCoordinates
		? pageHeight - Math.max(rect.y1, rect.y2)
		: Math.min(rect.y1, rect.y2);

	return {
		left: (Math.min(rect.x1, rect.x2) / pageWidth) * 100,
		top: (top / pageHeight) * 100,
		width: (Math.abs(rect.x2 - rect.x1) / pageWidth) * 100,
		height: (Math.abs(rect.y2 - rect.y1) / pageHeight) * 100,
	};
}

/** Stored position → the rects the overlay draws, plus the page they sit on. */
export function anchorFromScaledPosition(
	position: ScaledPosition,
	quote: string
): TextAnchor | null {
	const source = position.rects?.length
		? position.rects
		: position.boundingRect
			? [position.boundingRect]
			: [];
	// A row with neither `rects` nor `boundingRect` is malformed; bail rather
	// than dereferencing undefined further down.
	if (!source.length) return null;

	const usePdfCoordinates = position.usePdfCoordinates === true;
	// Merged per line: highlights saved from a selection before captureAnchor
	// merged its rects carry overlapping duplicates, and overlapping rects
	// multiply-blend into a darker band than the rest of the highlight.
	const rects = mergeIntoLines(
		source
			.map((r) => toPercentRect(r, usePdfCoordinates))
			.filter((r) => r.width > MIN_RECT_PERCENT && r.height > MIN_RECT_PERCENT),
		MAX_SELECTION_GAP_PERCENT
	);
	if (!rects.length) return null;

	const page =
		position.boundingRect?.pageNumber ?? source[0]?.pageNumber ?? 1;

	return { page, quote, rects };
}

/**
 * Rects → stored position, so newly drawn highlights keep the existing database
 * shape and stay readable by anything else that reads `PaperHighlight.position`.
 */
export function scaledPositionFromAnchor(
	anchor: TextAnchor,
	pageWidth: number,
	pageHeight: number
): ScaledPosition {
	const toScaled = (r: PercentRect): ScaledRect => ({
		x1: (r.left / 100) * pageWidth,
		y1: (r.top / 100) * pageHeight,
		x2: ((r.left + r.width) / 100) * pageWidth,
		y2: ((r.top + r.height) / 100) * pageHeight,
		width: pageWidth,
		height: pageHeight,
		pageNumber: anchor.page,
	});

	if (!anchor.rects.length || !(pageWidth > 0) || !(pageHeight > 0)) {
		// Reducing an empty list below would produce Infinity bounds and persist
		// a position no reader could ever interpret.
		throw new Error("scaledPositionFromAnchor: empty anchor or page size");
	}

	const rects = anchor.rects.map(toScaled);
	const boundingRect: ScaledRect = {
		x1: Math.min(...rects.map((r) => r.x1)),
		y1: Math.min(...rects.map((r) => r.y1)),
		x2: Math.max(...rects.map((r) => r.x2)),
		y2: Math.max(...rects.map((r) => r.y2)),
		width: pageWidth,
		height: pageHeight,
		pageNumber: anchor.page,
	};

	return { boundingRect, rects };
}

export function normalizeWs(s: string) {
	return s.replace(/\s+/g, " ").trim();
}

function buildIndexMap(text: string) {
	const map: number[] = [];
	let stripped = "";
	for (let i = 0; i < text.length; i++) {
		if (!/\s/.test(text[i])) {
			map.push(i);
			stripped += text[i];
		}
	}
	return { map, stripped };
}

/**
 * Turn the live selection into an anchor, measured against the page box it
 * started in. Ported from paper-reader, with the scroll container passed in
 * rather than looked up by a document-wide id.
 */
export function captureAnchor(container: HTMLElement | null): TextAnchor | null {
	const sel = window.getSelection();
	if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
	const range = sel.getRangeAt(0);
	if (!container || !container.contains(range.commonAncestorContainer)) return null;

	const startEl =
		range.startContainer instanceof Element
			? range.startContainer
			: range.startContainer.parentElement;
	const pageEl = startEl?.closest("[data-page-number]") as HTMLElement | null;
	if (!pageEl) return null;

	const pageRect = pageEl.getBoundingClientRect();
	if (!pageRect.width || !pageRect.height) return null;

	// A fully selected text-layer span reports two client rects — the span's
	// own box and its text's — while a partially selected one reports only the
	// text fragment. Drawn as-is, whole lines get a double (darker) tint, so
	// fold everything down to one rect per line.
	const rects: PercentRect[] = mergeIntoLines(
		Array.from(range.getClientRects())
			.filter((r) => r.width > 1 && r.height > 1)
			.map((r) => ({
				left: ((r.left - pageRect.left) / pageRect.width) * 100,
				top: ((r.top - pageRect.top) / pageRect.height) * 100,
				width: (r.width / pageRect.width) * 100,
				height: (r.height / pageRect.height) * 100,
			})),
		MAX_SELECTION_GAP_PERCENT
	);
	if (rects.length === 0) return null;

	const quote = normalizeWs(sel.toString());
	if (!quote) return null;

	// Neighbouring text disambiguates the quote if it appears more than once on
	// the page and we later have to re-find it by text alone.
	const fullText = pageEl.textContent ?? "";
	const { map, stripped } = buildIndexMap(fullText);
	const strippedQuote = quote.replace(/\s/g, "");
	const at = stripped.indexOf(strippedQuote);
	let prefix: string | undefined;
	let suffix: string | undefined;
	if (at > 0) {
		const startOrig = map[at];
		const endOrig = map[Math.min(at + strippedQuote.length - 1, map.length - 1)];
		prefix = normalizeWs(fullText.slice(Math.max(0, startOrig - 60), startOrig))
			.split(" ")
			.slice(-8)
			.join(" ");
		suffix = normalizeWs(fullText.slice(endOrig + 1, endOrig + 61))
			.split(" ")
			.slice(0, 8)
			.join(" ");
		if (!prefix) prefix = undefined;
		if (!suffix) suffix = undefined;
	}

	return {
		page: Number(pageEl.getAttribute("data-page-number")),
		quote,
		prefix,
		suffix,
		rects,
	};
}

export function clearSelection() {
	window.getSelection()?.removeAllRanges();
}

// ---------------------------------------------------------------------------
// Locating a quote that arrived without coordinates
// ---------------------------------------------------------------------------

interface CharSource {
	/** Index of the text item this character came from. */
	item: number;
	/** Character offset within that item's string. */
	offset: number;
}

interface PageTextIndex {
	/** Normalized character stream for the whole page. */
	text: string;
	/** Where each normalized character came from. */
	sources: CharSource[];
	items: PageTextItem[];
	pageWidth: number;
	pageHeight: number;
}

interface PageTextItem {
	str: string;
	/** Viewport-space box at scale 1, top-left origin. */
	left: number;
	top: number;
	width: number;
	height: number;
}

const textIndexCache = new WeakMap<PDFDocumentProxy, Map<number, Promise<PageTextIndex | null>>>();

/**
 * Build a searchable index of a page from `getTextContent()`.
 *
 * Deliberately *not* read from the rendered DOM text layer. pdf.js only builds
 * that layer for pages it has painted, which is what made assistant highlights
 * on unvisited pages invisible (caveats §5) and made them vanish and rebuild on
 * every zoom. The worker-side text content is available for any page at any
 * time, so a highlight on page 40 resolves before the user has ever seen it.
 */
async function buildPageTextIndex(
	pdfDoc: PDFDocumentProxy,
	pageNumber: number
): Promise<PageTextIndex | null> {
	let perDoc = textIndexCache.get(pdfDoc);
	if (!perDoc) {
		perDoc = new Map();
		textIndexCache.set(pdfDoc, perDoc);
	}
	const cached = perDoc.get(pageNumber);
	if (cached) return cached;
	// Store the in-flight extraction too: a priority jump can request a page
	// the background resolver is already reading.
	const pending = extractPageTextIndex(pdfDoc, pageNumber);
	perDoc.set(pageNumber, pending);
	return pending;
}

async function extractPageTextIndex(
	pdfDoc: PDFDocumentProxy,
	pageNumber: number
): Promise<PageTextIndex | null> {
	let index: PageTextIndex | null = null;
	try {
		const page = await pdfDoc.getPage(pageNumber);
		const viewport = page.getViewport({ scale: 1 });
		const content = await page.getTextContent();

		const items: PageTextItem[] = [];
		let text = "";
		const sources: CharSource[] = [];

		for (const raw of content.items) {
			const item = raw as {
				str?: string;
				transform?: number[];
				width?: number;
				height?: number;
				hasEOL?: boolean;
			};
			if (typeof item.str !== "string" || !item.transform) continue;

			// Mirrors pdf.js's own TextLayer#processItems (build/pdf.mjs), which is
			// the thing that decides where the glyphs the user sees actually land:
			// compose the item transform with the viewport transform, take the font
			// height from hypot(tx[2], tx[3]), and offset the box up from the
			// BASELINE by the font's ascent — not by the full font height, which
			// would float every box roughly a fifth of a line above its text.
			const t = pdfjsLib.Util.transform(viewport.transform, item.transform);
			const fontHeight = Math.hypot(t[2], t[3]) || item.height || 0;
			const ascent = fontHeight * ASCENT_RATIO;
			const itemIndex = items.length;
			items.push({
				str: item.str,
				left: t[4],
				top: t[5] - ascent,
				// `item.width` is already in viewport units at scale 1 — the same
				// space as the transformed coordinates (pdf.js uses it the same way
				// for its own text divs).
				width: item.width ?? 0,
				height: fontHeight,
			});

			for (let i = 0; i < item.str.length; ) {
				// Fold a base character together with any combining marks that
				// follow it. Folding strictly one UTF-16 unit at a time cannot
				// compose `e` + U+0301 into `é`, so a PDF storing decomposed text
				// would never match a quote that arrived composed — and the whole
				// string can't be normalized in one go either, because NFC changes
				// length and would desync `sources`. Composing per cluster keeps
				// every emitted character mapped to the base character's offset.
				let end = i + 1;
				while (end < item.str.length && COMBINING_MARK_RE.test(item.str[end])) {
					end += 1;
				}
				// Per-character on purpose: whole-string normalisation ends with
				// `.trim()`, which would delete every space in the page if applied
				// here.
				const folded = foldPdfMatchChar(item.str.slice(i, end)).normalize("NFKC");
				for (const ch of folded) {
					text += ch;
					sources.push({ item: itemIndex, offset: i });
				}
				i = end;
			}
			// pdf.js drops the space between items; reinstate one so words from
			// adjacent items don't fuse ("theresults").
			if (item.str.length && !/\s$/.test(item.str)) {
				text += " ";
				sources.push({ item: itemIndex, offset: item.str.length - 1 });
			}
		}

		index = {
			text,
			sources,
			items,
			pageWidth: viewport.width,
			pageHeight: viewport.height,
		};
	} catch {
		index = null;
	}

	return index;
}

/**
 * Collapse runs of whitespace, keeping a map back into the source string.
 *
 * Also resolves soft hyphens (kept in the page index by `foldPdfMatchChar`):
 * one before whitespace is a line-end hyphenation mark and becomes '-', so the
 * matcher's hyphen-rejoin rungs can glue "effi- cient" back into "efficient";
 * anywhere else it's an invisible break opportunity and is dropped.
 */
function compact(text: string): { value: string; map: number[] } {
	let value = "";
	const map: number[] = [];
	let lastWasSpace = true;
	for (let i = 0; i < text.length; i++) {
		const ch = text[i];
		if (ch === SOFT_HYPHEN) {
			const next = text[i + 1];
			if (next !== undefined && !/\s/.test(next)) continue;
			// A soft hyphen emitted as its own text item gets a space in front
			// of it; the hyphen must sit directly after the word to rejoin.
			if (value.endsWith(" ")) {
				value = value.slice(0, -1);
				map.pop();
			}
			value += "-";
			map.push(i);
			lastWasSpace = false;
			continue;
		}
		if (/\s/.test(ch)) {
			if (lastWasSpace) continue;
			lastWasSpace = true;
			value += " ";
			map.push(i);
			continue;
		}
		lastWasSpace = false;
		value += ch;
		map.push(i);
	}
	return { value, map };
}

function findMatchRange(
	haystack: string,
	needle: string
): { start: number; end: number } | null {
	// Ligatures are already expanded in the page index (pdf.js normalises them
	// in getTextContent, then NFKC + `foldPdfMatchChar`) and in the needle
	// (`normalizeForPdfMatch`). `compact` turns line-end soft hyphens into '-';
	// the server-aligned ladder then tries exact, line-break hyphen rejoin
	// (keeping / dropping the hyphen), whitespace, then punctuation for long
	// quotes. The source map keeps every match tied to actual PDF text items.
	const hay = compact(haystack);
	const hit = findServerAlignedMatchInNormalizedPdfText(needle, hay.value);
	if (!hit) return null;
	return { start: hay.map[hit.start], end: hay.map[hit.end - 1] + 1 };
}

/**
 * Merge boxes that sit on the same visual line into one rect per line.
 *
 * Vertical proximity alone is not enough: in a two-column paper the extraction
 * order can put text from both columns at the same height, and merging those
 * would draw one highlight straight across the gutter. Boxes must also be
 * horizontally adjacent to be joined.
 */
const MAX_LINE_GAP_PERCENT = 5;

/**
 * Selection / stored rects only need duplicates and word-adjacent fragments
 * folded together; anything wider than a word space may be a column gutter
 * (2–3% of the page in two-column papers).
 */
const MAX_SELECTION_GAP_PERCENT = 1;

export function mergeIntoLines(
	boxes: PercentRect[],
	maxGap: number = MAX_LINE_GAP_PERCENT
): PercentRect[] {
	const sorted = [...boxes].sort((a, b) => a.top - b.top || a.left - b.left);
	const lines: PercentRect[] = [];
	for (const box of sorted) {
		// Any line so far, not just the last: in a two-column selection the
		// other column's lines sort in between boxes of the same line, and a box
		// left unmerged double-tints wherever it overlaps its line.
		let merged = false;
		for (let li = lines.length - 1; li >= 0 && !merged; li--) {
			const line = lines[li];
			const lineMid = line.top + line.height / 2;
			const mid = box.top + box.height / 2;
			// Distance between the two horizontal intervals, in whichever order
			// they appear. A signed `box.left - line.right` goes strongly negative
			// when the next box sits to the *left* of the previous one — which
			// happens as soon as a box on the next line sorts in — and would then
			// always pass the threshold, merging straight across a column gutter.
			const gap = Math.max(
				0,
				box.left - (line.left + line.width),
				line.left - (box.left + box.width)
			);
			if (
				gap <= maxGap &&
				Math.abs(lineMid - mid) <= Math.max(line.height, box.height) * 0.6
			) {
				const left = Math.min(line.left, box.left);
				const right = Math.max(line.left + line.width, box.left + box.width);
				const top = Math.min(line.top, box.top);
				const bottom = Math.max(line.top + line.height, box.top + box.height);
				lines[li] = {
					left,
					top,
					width: right - left,
					height: bottom - top,
				};
				merged = true;
			}
		}
		if (!merged) lines.push({ ...box });
	}
	return lines;
}

/**
 * Find `quote` on `pageNumber` and return the boxes covering it, as
 * percentages of the page. Returns an empty array when the quote isn't there.
 */
export async function locateQuoteOnPage(
	pdfDoc: PDFDocumentProxy,
	pageNumber: number,
	quote: string
): Promise<PercentRect[]> {
	const index = await buildPageTextIndex(pdfDoc, pageNumber);
	if (!index || !index.text) return [];

	// TeX's escaped math delimiters aren't printed parentheses/brackets.
	const needle = normalizeForPdfMatch(
		quote.normalize("NFKC").replace(/\\(?:\(|\)|\[|\])/g, "")
	);
	const range = findMatchRange(index.text, needle);
	if (!range) return [];

	// Walk the matched span, accumulating one box per contiguous run of
	// characters within a text item. Partial items are interpolated
	// horizontally by character count, which is exact for monospaced runs and
	// close enough elsewhere that line merging hides the error.
	const boxes: PercentRect[] = [];
	let runItem = -1;
	let runStart = 0;
	let runEnd = 0;

	const flush = () => {
		if (runItem < 0) return;
		const item = index.items[runItem];
		if (!item || !item.str.length || item.width <= 0) {
			runItem = -1;
			return;
		}
		const perChar = item.width / item.str.length;
		const left = item.left + runStart * perChar;
		const width = Math.max((runEnd - runStart) * perChar, perChar);
		boxes.push({
			left: (left / index.pageWidth) * 100,
			top: (item.top / index.pageHeight) * 100,
			width: (width / index.pageWidth) * 100,
			height: (item.height / index.pageHeight) * 100,
		});
		runItem = -1;
	};

	for (let i = range.start; i < range.end && i < index.sources.length; i++) {
		const src = index.sources[i];
		if (src.item === runItem && src.offset === runEnd) {
			runEnd = src.offset + 1;
			continue;
		}
		if (src.item === runItem && src.offset < runEnd) continue; // ligature
		flush();
		runItem = src.item;
		runStart = src.offset;
		runEnd = src.offset + 1;
	}
	flush();

	return mergeIntoLines(
		boxes.filter((b) => b.width > MIN_RECT_PERCENT && b.height > MIN_RECT_PERCENT)
	);
}

/**
 * Locate a quote anywhere in the document.
 *
 * `hintPage` (the `page` the chat agent reported on a citation) is tried first
 * and, when it hits, saves scanning the rest of the document.
 */
export async function locateQuote(
	pdfDoc: PDFDocumentProxy,
	quote: string,
	hintPage?: number | null,
	options: { signal?: AbortSignal; beforePage?: () => Promise<void> } = {}
): Promise<{ page: number; rects: PercentRect[] } | null> {
	const total = pdfDoc.numPages;
	const order: number[] = [];
	if (Number.isInteger(hintPage) && hintPage && hintPage >= 1 && hintPage <= total) order.push(hintPage);
	for (let p = 1; p <= total; p++) if (p !== hintPage) order.push(p);

	for (const page of order) {
		if (options.signal?.aborted) return null;
		await options.beforePage?.();
		if (options.signal?.aborted) return null;
		const rects = await locateQuoteOnPage(pdfDoc, page, quote);
		if (options.signal?.aborted) return null;
		if (rects.length) return { page, rects };
	}
	return null;
}
