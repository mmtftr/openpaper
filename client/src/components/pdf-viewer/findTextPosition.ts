"use client";

import { ScaledPosition, ScaledRect } from "@/lib/schema";
import {
	findServerAlignedMatchInNormalizedPdfText,
	findServerAlignedPdfTextMatch,
	foldPdfMatchChar,
} from "./textNormalization";

interface CharMapping {
	span: HTMLSpanElement;
	originalCharIndex: number;
	textNode: Text | null;
	isVirtual?: boolean;
}

function buildNormalizedTextLayerStream(textLayer: Element): {
	normalizedText: string;
	charMappings: CharMapping[];
} {
	const spans = Array.from(textLayer.querySelectorAll("span"));
	let normalizedText = "";
	const charMappings: CharMapping[] = [];

	spans.forEach((span) => {
		const originalText = span.textContent || "";
		const textNode = span.firstChild as Text | null;

		if (originalText.length === 0) return;

		if (normalizedText.length > 0 && !normalizedText.endsWith(" ")) {
			normalizedText += " ";
			charMappings.push({ span, originalCharIndex: -1, textNode, isVirtual: true });
		}

		let prevWasSpace = normalizedText.endsWith(" ");

		for (let i = 0; i < originalText.length; i++) {
			const folded = foldPdfMatchChar(originalText[i]);
			if (!folded || folded === "*") continue;

			for (const char of folded) {
				if (/\s/.test(char)) {
					if (!prevWasSpace) {
						normalizedText += " ";
						charMappings.push({ span, originalCharIndex: i, textNode });
						prevWasSpace = true;
					}
				} else {
					normalizedText += char;
					charMappings.push({ span, originalCharIndex: i, textNode });
					prevWasSpace = false;
				}
			}
		}
	});

	while (normalizedText.startsWith(" ")) {
		normalizedText = normalizedText.slice(1);
		charMappings.shift();
	}
	while (normalizedText.endsWith(" ")) {
		normalizedText = normalizedText.slice(0, -1);
		charMappings.pop();
	}

	return { normalizedText, charMappings };
}

/**
 * Find which page(s) contain the given text.
 * Returns an array of page numbers where the text was found.
 */
export async function findTextPages(
	searchText: string,
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	pdfDocument: any,
	targetPageNumber?: number
): Promise<number[]> {
	if (!searchText || !pdfDocument) return [];

	const matchingPages: number[] = [];

	// If target page is specified, check it first
	const pagesToSearch: number[] = [];
	if (targetPageNumber && targetPageNumber >= 1 && targetPageNumber <= pdfDocument.numPages) {
		pagesToSearch.push(targetPageNumber);
	}
	for (let i = 1; i <= pdfDocument.numPages; i++) {
		if (i !== targetPageNumber) {
			pagesToSearch.push(i);
		}
	}

	for (const pageNum of pagesToSearch) {
		try {
			const page = await pdfDocument.getPage(pageNum);
			const textContent = await page.getTextContent();
			const pageText = textContent.items
				.map((item: { str?: string }) => item.str || "")
				.join(" ");

			if (findServerAlignedPdfTextMatch(searchText, pageText)) {
				matchingPages.push(pageNum);
			}
		} catch (err) {
			console.warn(`Error searching page ${pageNum}:`, err);
		}
	}

	return matchingPages;
}

/**
 * Create highlight overlays for text in a rendered text layer.
 * This uses the same DOM-based approach as the search highlighting.
 * Returns the created overlay elements.
 */
export function createTextHighlightOverlays(
	textLayer: Element,
	searchText: string,
	highlightClass: string = "assistant-highlight-overlay",
	backgroundColor: string = "rgba(168, 85, 247, 0.3)"
): HTMLElement[] {
	const matchElements: HTMLElement[] = [];
	const { normalizedText, charMappings } = buildNormalizedTextLayerStream(textLayer);
	const match = findServerAlignedMatchInNormalizedPdfText(searchText, normalizedText);

	if (!match || charMappings.length === 0) return matchElements;

	const normalizedStartIndex = match.start;
	const normalizedEndIndex = match.end;

	const textLayerRect = textLayer.getBoundingClientRect();

	interface HighlightRange {
		span: HTMLSpanElement;
		textNode: Text | null;
		startIdx: number;
		endIdx: number;
	}

	const ranges: HighlightRange[] = [];
	let currentRange: HighlightRange | null = null;

	for (let i = normalizedStartIndex; i < normalizedEndIndex && i < charMappings.length; i++) {
		const mapping = charMappings[i];

		if (mapping.isVirtual) {
			continue;
		}

		if (
			currentRange &&
			currentRange.span === mapping.span &&
			currentRange.endIdx === mapping.originalCharIndex
		) {
			currentRange.endIdx = mapping.originalCharIndex + 1;
		} else if (
			currentRange &&
			currentRange.span === mapping.span &&
			currentRange.endIdx === mapping.originalCharIndex + 1
		) {
			// Same position (ligature case)
		} else {
			if (currentRange) {
				ranges.push(currentRange);
			}
			currentRange = {
				span: mapping.span,
				textNode: mapping.textNode,
				startIdx: mapping.originalCharIndex,
				endIdx: mapping.originalCharIndex + 1,
			};
		}
	}
	if (currentRange) {
		ranges.push(currentRange);
	}

	for (const range of ranges) {
		if (range.textNode && range.textNode.nodeType === Node.TEXT_NODE) {
			try {
				const domRange = document.createRange();
				const safeStart = Math.min(range.startIdx, range.textNode.length);
				const safeEnd = Math.min(range.endIdx, range.textNode.length);

				if (safeStart >= safeEnd) continue;

				domRange.setStart(range.textNode, safeStart);
				domRange.setEnd(range.textNode, safeEnd);

				const rects = domRange.getClientRects();
				for (let i = 0; i < rects.length; i++) {
					const rect = rects[i];
					if (rect.width === 0 || rect.height === 0) continue;

					const highlight = document.createElement("div");
					highlight.className = highlightClass;
					highlight.style.position = "absolute";
					highlight.style.left = `${rect.left - textLayerRect.left}px`;
					highlight.style.top = `${rect.top - textLayerRect.top}px`;
					highlight.style.width = `${rect.width}px`;
					highlight.style.height = `${rect.height}px`;
					highlight.style.backgroundColor = backgroundColor;
					highlight.style.borderRadius = "2px";
					highlight.style.pointerEvents = "none";
					highlight.setAttribute("data-match-strategy", match.strategy);
					// No mix-blend-multiply: it darkens vs react-pdf-highlighter TextHighlight (solid rgba),
					// so position-backed and overlay-backed highlights look inconsistent.

					textLayer.appendChild(highlight);
					matchElements.push(highlight);
				}
			} catch (e) {
				console.warn("Range error:", e);
			}
		}
	}

	return matchElements;
}

/**
 * Remove all highlight overlays with the given class.
 */
export function removeHighlightOverlays(className: string = "assistant-highlight-overlay"): void {
	document.querySelectorAll(`.${className}`).forEach((el) => el.remove());
}

/**
 * Find text in a rendered text layer and compute ScaledPosition.
 * This uses DOM measurements and converts them to the format expected by react-pdf-highlighter-extended.
 */
export function computeScaledPositionFromTextLayer(
	textLayer: Element,
	searchText: string,
	pageNumber: number,
	scale: number
): ScaledPosition | null {
	const { normalizedText, charMappings } = buildNormalizedTextLayerStream(textLayer);
	const match = findServerAlignedMatchInNormalizedPdfText(searchText, normalizedText);

	if (!match || charMappings.length === 0) return null;

	const normalizedStartIndex = match.start;
	const normalizedEndIndex = match.end;

	// Get the page element to compute relative positions
	const pageEl = textLayer.closest(".page");
	if (!pageEl) return null;

	const pageRect = pageEl.getBoundingClientRect();

	interface HighlightRange {
		span: HTMLSpanElement;
		textNode: Text | null;
		startIdx: number;
		endIdx: number;
	}

	const ranges: HighlightRange[] = [];
	let currentRange: HighlightRange | null = null;

	for (let i = normalizedStartIndex; i < normalizedEndIndex && i < charMappings.length; i++) {
		const mapping = charMappings[i];

		if (mapping.isVirtual) continue;

		if (
			currentRange &&
			currentRange.span === mapping.span &&
			currentRange.endIdx === mapping.originalCharIndex
		) {
			currentRange.endIdx = mapping.originalCharIndex + 1;
		} else if (
			currentRange &&
			currentRange.span === mapping.span &&
			currentRange.endIdx === mapping.originalCharIndex + 1
		) {
			// Same position (ligature case)
		} else {
			if (currentRange) ranges.push(currentRange);
			currentRange = {
				span: mapping.span,
				textNode: mapping.textNode,
				startIdx: mapping.originalCharIndex,
				endIdx: mapping.originalCharIndex + 1,
			};
		}
	}
	if (currentRange) ranges.push(currentRange);

	const scaledRects: ScaledRect[] = [];

	for (const range of ranges) {
		if (range.textNode && range.textNode.nodeType === Node.TEXT_NODE) {
			try {
				const domRange = document.createRange();
				const safeStart = Math.min(range.startIdx, range.textNode.length);
				const safeEnd = Math.min(range.endIdx, range.textNode.length);

				if (safeStart >= safeEnd) continue;

				domRange.setStart(range.textNode, safeStart);
				domRange.setEnd(range.textNode, safeEnd);

				const rects = domRange.getClientRects();
				for (let i = 0; i < rects.length; i++) {
					const rect = rects[i];
					if (rect.width === 0 || rect.height === 0) continue;

					// Convert to page-relative coordinates at scale 1.0
					const x1 = (rect.left - pageRect.left) / scale;
					const y1 = (rect.top - pageRect.top) / scale;
					const x2 = (rect.right - pageRect.left) / scale;
					const y2 = (rect.bottom - pageRect.top) / scale;

					scaledRects.push({
						x1,
						y1,
						x2,
						y2,
						width: x2 - x1,
						height: y2 - y1,
						pageNumber,
					});
				}
			} catch (e) {
				console.warn("Range error:", e);
			}
		}
	}

	if (scaledRects.length === 0) return null;

	// Compute bounding rect from all rects
	const boundingRect: ScaledRect = {
		x1: Math.min(...scaledRects.map((r) => r.x1)),
		y1: Math.min(...scaledRects.map((r) => r.y1)),
		x2: Math.max(...scaledRects.map((r) => r.x2)),
		y2: Math.max(...scaledRects.map((r) => r.y2)),
		width: 0,
		height: 0,
		pageNumber,
	};
	boundingRect.width = boundingRect.x2 - boundingRect.x1;
	boundingRect.height = boundingRect.y2 - boundingRect.y1;

	return {
		boundingRect,
		rects: scaledRects,
	};
}
