"use client";

import { createContext, useContext } from "react";
import type { RefObject } from "react";

/**
 * Hands the scroll container down to the overlay layers.
 *
 * paper-reader reached for `document.getElementById("scrollablePane")` from
 * anchoring, notes and citation code. openpaper can mount more than one reader
 * in a tree (the paper route renders separate desktop and mobile branches, and
 * a supplementary PDF can be swapped in beside its parent), so a document-wide
 * id would let one reader's overlays measure against another's pages. The ref
 * keeps every lookup scoped to the instance that owns it.
 */
export interface ReaderContextValue {
	/** The `overflow-auto` element that scrolls the pages. */
	containerRef: RefObject<HTMLDivElement | null>;
	/** Paper whose PDF is currently rendered — used for highlight persistence. */
	displayedPaperId: string;
}

const ReaderContext = createContext<ReaderContextValue | null>(null);

export const ReaderContextProvider = ReaderContext.Provider;

export function useReaderContext(): ReaderContextValue {
	const ctx = useContext(ReaderContext);
	if (!ctx) {
		throw new Error("useReaderContext must be used inside <PdfReader>");
	}
	return ctx;
}

/** Non-throwing variant for optional overlays. */
export function useReaderContextOptional(): ReaderContextValue | null {
	return useContext(ReaderContext);
}
