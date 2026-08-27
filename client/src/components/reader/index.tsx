"use client";

import dynamic from "next/dynamic";
import type { PdfReaderProps } from "./PdfReader";

/**
 * pdf.js touches `window`, `DOMMatrix` and `Worker` at import time, so the
 * reader is loaded client-side only. Keeping the boundary here means callers
 * just render `<PdfReader />` and never think about it.
 */
const PdfReaderImpl = dynamic(
	() => import("./PdfReader").then((mod) => mod.PdfReader),
	{
		ssr: false,
		loading: () => (
			<div className="flex h-full w-full items-center justify-center">
				<div className="size-8 animate-spin rounded-full border-2 border-border border-t-blue-500" />
			</div>
		),
	}
);

export function PdfReader(props: PdfReaderProps) {
	return <PdfReaderImpl {...props} />;
}

export type { PdfReaderProps };
export type { RenderedHighlightPosition, TextAnchor, PercentRect } from "./types";
