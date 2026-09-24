"use client";

/**
 * Single entry point for pdf.js. Everything in `components/reader` imports the
 * library through here so the worker is configured exactly once and nothing
 * else has to know where the runtime assets are served from.
 *
 * The worker and the cmap/font/wasm bundles are copied out of node_modules into
 * `public/pdfjs/<version>/` by `scripts/sync-pdfjs-assets.mjs` (wired to
 * `predev`/`prebuild`), so the served assets always match the installed
 * pdfjs-dist. paper-reader used Vite's `?url` import for this; Next has no
 * equivalent, hence the public dir.
 *
 * The version segment in the URL is what makes upgrades safe: the worker must
 * be the exact same release as the bundled API, and an unversioned
 * `/pdf.worker.mjs` kept getting served from browser caches (mobile Safari in
 * particular) long after a bump, failing with "The API version X does not match
 * the Worker version Y". With the version in the path every bump is a new URL,
 * and `next.config.ts` can mark the whole directory immutable.
 */

import * as pdfjsLib from "pdfjs-dist";
import {
	EventBus,
	PDFLinkService,
	PDFFindController,
	PDFViewer,
} from "pdfjs-dist/web/pdf_viewer.mjs";

/** Served from `public/pdfjs/<version>/` — see the sync script. */
export const PDFJS_ASSET_BASE = `/pdfjs/${pdfjsLib.version}`;

if (typeof window !== "undefined") {
	pdfjsLib.GlobalWorkerOptions.workerSrc = `${PDFJS_ASSET_BASE}/pdf.worker.mjs`;
}

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
