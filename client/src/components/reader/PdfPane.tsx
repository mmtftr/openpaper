"use client";

import { useCallback, useEffect } from "react";
import { ArrowLeft } from "lucide-react";
import { useAtomValue, useSetAtom } from "jotai";
import {
	loadingErrorAtom,
	loadingProgressAtom,
	pdfDocAtom,
	pdfUrlAtom,
	pdfViewerAtom,
	scaleValueAtom,
} from "./atoms";
import { usePdfViewer, viewerApiAtom } from "./useViewer";
import { useCitationLinks } from "./citations/useCitationLinks";
import { useReaderContext } from "./ReaderContext";
import type { PDFDocumentProxy, PDFViewer } from "./pdfjs";
import CitationPreviewCard from "./citations/CitationPreviewCard";
import FindBar from "./FindBar";
import { ReaderContextProvider } from "./ReaderContext";
import { useReaderNavigation } from "./useReaderNavigation";
import "pdfjs-dist/web/pdf_viewer.css";

export interface PdfPaneProps {
	pdfUrl: string;
	displayedPaperId: string;
	/** Presigned URLs expire; return a fresh one and the viewer retries. */
	onRefreshUrl?: () => Promise<string | null>;
	/** Overlays that need the reader context (highlights, selection, cards). */
	children?: React.ReactNode;
}

/**
 * The scrolling PDF surface: pdf.js's own `PDFViewer` plus the overlays that
 * sit on top of it.
 *
 * Everything visual that isn't a page lives in `children`, rendered inside the
 * reader context so overlays measure against *this* pane's container rather
 * than hunting the document for one.
 */

/**
 * Overlays that need the reader context, so they must live *inside* the
 * provider rather than in the component that supplies it.
 */
function PaneOverlays({
	viewer,
	doc,
}: {
	viewer: PDFViewer | null;
	doc: PDFDocumentProxy | null;
}) {
	const { containerRef } = useReaderContext();
	const { jumpToPage, pushHistory, goBack, canGoBack } = useReaderNavigation();

	useCitationLinks(containerRef, viewer, doc, jumpToPage, pushHistory);

	// Alt/⌥ + ← mirrors the browser's own "back", which is the reflex here.
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if (e.altKey && e.key === "ArrowLeft") {
				e.preventDefault();
				goBack();
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [goBack]);

	return (
		<>
			<CitationPreviewCard scrollerRef={containerRef} onJumpToPage={jumpToPage} />
			<FindBar />
			{canGoBack && (
				<button
					onClick={goBack}
					title="Back to where you were (⌥←)"
					aria-label="Back to where you were"
					className="absolute bottom-4 left-4 z-20 flex items-center gap-1.5 rounded-full border border-border bg-background/95 px-3 py-1.5 text-xs font-medium shadow-lg backdrop-blur transition-colors hover:bg-muted"
				>
					<ArrowLeft className="size-3.5" /> Back
				</button>
			)}
		</>
	);
}

export default function PdfPane({
	pdfUrl,
	displayedPaperId,
	onRefreshUrl,
	children,
}: PdfPaneProps) {
	const { containerRef } = usePdfViewer({ onRefreshUrl });
	const doc = useAtomValue(pdfDocAtom);
	const viewer = useAtomValue(pdfViewerAtom);
	const progress = useAtomValue(loadingProgressAtom);
	const error = useAtomValue(loadingErrorAtom);
	const api = useAtomValue(viewerApiAtom);
	const setUrl = useSetAtom(pdfUrlAtom);
	const setScaleValue = useSetAtom(scaleValueAtom);

	// The document to show is a prop here, not a `?file=` query param.
	useEffect(() => {
		setUrl(pdfUrl);
	}, [pdfUrl, setUrl]);

	// Zoom keyboard shortcuts, ignored while typing.
	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			const target = e.target as HTMLElement | null;
			if (
				target?.tagName === "INPUT" ||
				target?.tagName === "TEXTAREA" ||
				target?.isContentEditable
			)
				return;
			if (e.metaKey || e.ctrlKey || e.altKey) return;
			if (e.key === "+" || e.key === "=") api?.zoomIn();
			else if (e.key === "-") api?.zoomOut();
			else if (e.key === "0") setScaleValue("page-width");
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [api, setScaleValue]);

	return (
		<ReaderContextProvider value={{ containerRef, displayedPaperId }}>
			<div className="relative h-full">
				<div
					ref={containerRef}
					className="absolute inset-0 overflow-auto bg-muted"
				>
					<div className="pdfViewer" />
				</div>

				{children}

				<PaneOverlays viewer={viewer} doc={doc} />

				{progress > 0 && progress < 1 && !error && (
					<div className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3 bg-background/60">
						<div className="size-10 animate-spin rounded-full border-2 border-border border-t-blue-500" />
						<span className="text-sm tabular-nums text-muted-foreground">
							Loading… {Math.round(progress * 100)}%
						</span>
					</div>
				)}

				{error && (
					<div className="absolute inset-0 z-10 flex items-center justify-center">
						<div className="max-w-sm rounded-xl border border-border bg-background p-6 text-center">
							<p className="mb-2 text-sm font-medium">Could not load PDF</p>
							<p className="text-xs break-words text-muted-foreground">{error}</p>
						</div>
					</div>
				)}
			</div>
		</ReaderContextProvider>
	);
}
