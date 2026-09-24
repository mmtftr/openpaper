"use client";

import { atom } from "jotai";
import type { PDFDocumentProxy, PDFViewer } from "./pdfjs";
import type { ScaleValue, SpreadMode } from "./types";

/**
 * Viewer-internal state, ported from paper-reader's `state/atoms.ts`.
 *
 * Scope matters: `<ReaderProvider>` mounts a jotai `Provider` per reader
 * instance, so these reset when the reader unmounts and two readers (e.g. a
 * paper and one of its supplementaries) never share a document or scale.
 *
 * Deliberately *not* here: highlights, annotations and anything the chat panel
 * touches. Those stay in the existing React state owned by the paper route so
 * the side-panel wiring is untouched by the viewer swap.
 */

/** Set from the `pdfUrl` prop rather than paper-reader's `?file=` query param. */
export const pdfUrlAtom = atom<string | null>(null);
export const pdfDocAtom = atom<PDFDocumentProxy | null>(null);
export const pdfViewerAtom = atom<PDFViewer | null>(null);
export const numPagesAtom = atom(0);
export const loadingProgressAtom = atom(0);
export const loadingErrorAtom = atom<string | null>(null);

export const scaleValueAtom = atom<ScaleValue>("auto");
export const actualScaleAtom = atom(1);
export const currentPageAtom = atom(1);
export const spreadModeAtom = atom<SpreadMode>("none");

export const findOpenAtom = atom(false);
export const findQueryAtom = atom("");
export const findMatchCountAtom = atom({ current: 0, total: 0 });

export const outlineOpenAtom = atom(false);
export const outlineTabAtom = atom<"thumbnails" | "outline">("thumbnails");

// --- Citation hover previews (see components/reader/citations) ---

export interface CitationPreviewBase {
	anchorRect: DOMRect;
}
export interface CitationPreviewSkeleton extends CitationPreviewBase {
	state: "skeleton";
}
/** The bibliography entry was extracted; the card resolves it (SWR). */
export interface CitationPreviewEntry extends CitationPreviewBase {
	state: "entry";
	referenceText: string;
	destinationPage: number | null;
}
export interface CitationPreviewUnavailable extends CitationPreviewBase {
	state: "unavailable";
	destinationPage: number | null;
}

export type CitationPreviewState =
	| CitationPreviewSkeleton
	| CitationPreviewEntry
	| CitationPreviewUnavailable;

export const citationPreviewAtom = atom<CitationPreviewState | null>(null);
