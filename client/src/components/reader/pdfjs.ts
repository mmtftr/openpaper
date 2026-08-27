"use client";

/**
 * Single entry point for pdf.js. Everything in `components/reader` imports the
 * library through here so the worker is configured exactly once and nothing
 * else has to know where the runtime assets are served from.
 *
 * The worker and the cmap/font/wasm bundles are copied out of node_modules into
 * `public/` by `scripts/sync-pdfjs-assets.mjs` (wired to `predev`/`prebuild`),
 * so the served assets always match the installed pdfjs-dist. paper-reader used
 * Vite's `?url` import for this; Next has no equivalent, hence the public dir.
 */

import * as pdfjsLib from "pdfjs-dist";
import {
	EventBus,
	PDFLinkService,
	PDFFindController,
	PDFViewer,
} from "pdfjs-dist/web/pdf_viewer.mjs";

if (typeof window !== "undefined") {
	pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.mjs";
}

/** Served from `public/pdfjs/` — see the sync script. */
export const PDFJS_ASSET_BASE = "/pdfjs";

/**
 * Options every `getDocument` call in the reader shares.
 *
 * `useSystemFonts: false` matters for correctness, not just looks: with system
 * font substitution the text layer's glyph metrics drift from the canvas, and
 * every selection rect / highlight box drifts with them.
 */
export const PDF_DOCUMENT_OPTIONS = {
	cMapUrl: `${PDFJS_ASSET_BASE}/cmaps/`,
	cMapPacked: true,
	standardFontDataUrl: `${PDFJS_ASSET_BASE}/standard_fonts/`,
	wasmUrl: `${PDFJS_ASSET_BASE}/wasm/`,
	useSystemFonts: false,
} as const;

export {
	pdfjsLib,
	EventBus,
	PDFLinkService,
	PDFFindController,
	PDFViewer,
};
export {
	FeatureTest,
	TouchManager,
	AnnotationMode,
	AnnotationEditorType,
} from "pdfjs-dist";
export type { PDFDocumentProxy, PageViewport } from "pdfjs-dist";
