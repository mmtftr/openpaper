"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Provider, useAtomValue, useSetAtom } from "jotai";
import type { BasicUser } from "@/lib/auth";
import type {
	HighlightColor,
	PaperHighlight,
	PaperHighlightAnnotation,
	ScaledPosition,
	SupplementaryMaterialSummary,
} from "@/lib/schema";
import { findMatchCountAtom, findQueryAtom, outlineOpenAtom, outlineTabAtom, pdfDocAtom } from "./atoms";
import { viewerApiAtom } from "./useViewer";
import PdfPane from "./PdfPane";
import { ReaderToolbar } from "./ReaderToolbar";
import { HighlightLayer } from "./HighlightLayer";
import SelectionLayer from "./SelectionLayer";
import { AnnotationCardsLayer } from "./AnnotationCardsLayer";
import { AnnotationHoverCard } from "@/components/AnnotationHoverCard";
import Thumbnails from "./Thumbnails";
import Outline from "./Outline";
import { useAnchoredHighlights } from "./useAnchoredHighlights";
import { scaledPositionFromAnchor } from "./anchoring";
import type { RenderedHighlightPosition, TextAnchor } from "./types";

export interface PdfReaderProps {
	pdfUrl: string;
	/** Quote pushed from a chat citation; searched and scrolled to. */
	explicitSearchTerm?: string;
	/** Page the chat agent reported for `explicitSearchTerm`, when known. */
	explicitSearchPage?: number;
	onSearchComplete?: (term: string, matchCount: number) => void;

	highlights?: PaperHighlight[];
	annotations?: PaperHighlightAnnotation[];
	activeHighlight?: PaperHighlight | null;
	setActiveHighlight?: (highlight: PaperHighlight | null) => void;
	/** Omit to make the reader read-only: the selection toolbar drops to copy-only. */
	addHighlight?: (
		selectedText: string,
		position?: ScaledPosition,
		pageNumber?: number,
		doAnnotate?: boolean,
		color?: HighlightColor
	) => void;
	removeHighlight?: (highlight: PaperHighlight) => void;

	addAnnotation?: (
		highlightId: string,
		content: string
	) => Promise<PaperHighlightAnnotation>;
	updateAnnotation?: (
		annotationId: string,
		content: string
	) => Promise<unknown> | void;
	removeAnnotation?: (annotationId: string) => void;

	/** Selected text becomes a reference on the next chat message. */
	setUserMessageReferences?: React.Dispatch<React.SetStateAction<string[]>>;

	onOverlaysCreated?: (
		positions: Map<string, RenderedHighlightPosition>
	) => void;
	/** Highlights that exist but could not be located in this PDF. */
	onUnanchoredHighlights?: (highlights: PaperHighlight[]) => void;
	onRefreshUrl?: () => Promise<string | null>;

	currentUser?: BasicUser | null;
	showAnnotationCards?: boolean;
	onToggleAnnotationCards?: () => void;
	/** Route note composition to the side panel instead of an inline card. */
	annotationsPanelActive?: boolean;
	onAnnotateViaSidePanel?: (payload: { highlightId: string }) => void;
	composeHighlightId?: string | null;

	isReadMode?: boolean;
	onToggleReadMode?: () => void;
	/** Side panel is taking horizontal space — left-align pages to free the right gutter for cards. */
	sidePanelOpen?: boolean;

	parentPaperId?: string;
	displayedPaperId?: string;
	parentPaperTitle?: string;
	supplementaryMaterials?: SupplementaryMaterialSummary[];
	onChangeDisplayed?: (paperId: string) => void;
	onSupplementaryUploaded?: () => void;
}

// Module-level so the default value is referentially stable across renders;
// a fresh `[]` in the destructure would invalidate every downstream memo.
const EMPTY_HIGHLIGHTS: PaperHighlight[] = [];
const EMPTY_ANNOTATIONS: PaperHighlightAnnotation[] = [];

/** Left rail: thumbnails / outline. */
function ReaderSidebar() {
	const open = useAtomValue(outlineOpenAtom);
	const setTab = useSetAtom(outlineTabAtom);
	const tab = useAtomValue(outlineTabAtom);
	if (!open) return null;
	return (
		<aside className="flex w-56 shrink-0 flex-col border-r border-border bg-background">
			<div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2">
				{(["thumbnails", "outline"] as const).map((id) => (
					<button
						key={id}
						onClick={() => setTab(id)}
						className={`rounded-md px-2 py-1 text-[11px] capitalize ${
							tab === id
								? "bg-blue-500/10 text-blue-500"
								: "text-muted-foreground hover:bg-muted hover:text-foreground"
						}`}
					>
						{id}
					</button>
				))}
			</div>
			<div className="min-h-0 flex-1 overflow-y-auto">
				{tab === "thumbnails" ? <Thumbnails /> : <Outline />}
			</div>
		</aside>
	);
}

function PdfReaderInner(props: PdfReaderProps) {
	const {
		pdfUrl,
		explicitSearchTerm,
		explicitSearchPage,
		onSearchComplete,
		highlights = EMPTY_HIGHLIGHTS,
		annotations = EMPTY_ANNOTATIONS,
		activeHighlight = null,
		setActiveHighlight,
		addHighlight,
		addAnnotation,
		updateAnnotation,
		removeAnnotation,
		setUserMessageReferences,
		onOverlaysCreated,
		onUnanchoredHighlights,
		onRefreshUrl,
		currentUser,
		showAnnotationCards = true,
		onToggleAnnotationCards,
		annotationsPanelActive,
		onAnnotateViaSidePanel,
		composeHighlightId,
		isReadMode,
		onToggleReadMode,
		sidePanelOpen = false,
		displayedPaperId = "",
		...toolbarProps
	} = props;

	const api = useAtomValue(viewerApiAtom);
	const pdfDoc = useAtomValue(pdfDocAtom);
	const matchCount = useAtomValue(findMatchCountAtom);
	const setFindQuery = useSetAtom(findQueryAtom);

	const pageHints = useMemo(() => new Map<string, number>(), []);
	const { anchored, unanchored } = useAnchoredHighlights(highlights, pageHints);

	// Surface highlights we could not place, so the side panel can say so.
	const unanchoredKey = unanchored.map((h) => h.id ?? h.raw_text).join("|");
	useEffect(() => {
		onUnanchoredHighlights?.(unanchored);
		// `unanchoredKey` stands in for the identity of `unanchored`.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [unanchoredKey]);

	// --- Chat citation → find + scroll -------------------------------------
	const pendingSearchRef = useRef<string | null>(null);
	useEffect(() => {
		const term = explicitSearchTerm?.trim();
		if (!term || !api) return;
		pendingSearchRef.current = term;
		if (explicitSearchPage) api.goToPage(explicitSearchPage);
		setFindQuery(term);
		api.find(term);
	}, [explicitSearchTerm, explicitSearchPage, api, setFindQuery]);

	// pdf.js reports counts progressively; settle briefly before reporting so a
	// mid-scan zero isn't mistaken for "no match".
	useEffect(() => {
		const term = pendingSearchRef.current;
		if (!term || !onSearchComplete) return;
		const timer = setTimeout(() => {
			if (pendingSearchRef.current !== term) return;
			pendingSearchRef.current = null;
			onSearchComplete(term, matchCount.total);
		}, 400);
		return () => clearTimeout(timer);
	}, [matchCount, onSearchComplete]);

	// --- Selection actions --------------------------------------------------
	const persistHighlight = useCallback(
		async (anchor: TextAnchor, color: HighlightColor, doAnnotate: boolean) => {
			if (!pdfDoc || !addHighlight) return;
			try {
				// Store against the page's unrotated dimensions so the saved
				// position stays comparable with what the ingestion job writes.
				const page = await pdfDoc.getPage(anchor.page);
				const viewport = page.getViewport({ scale: 1 });
				const position = scaledPositionFromAnchor(
					anchor,
					viewport.width,
					viewport.height
				);
				addHighlight(anchor.quote, position, anchor.page, doAnnotate, color);
			} catch (error) {
				console.error("Failed to persist highlight:", error);
			}
		},
		[pdfDoc, addHighlight]
	);

	const handleAskAi = useCallback(
		(quote: string) => {
			setUserMessageReferences?.((prev) =>
				prev.includes(quote) ? prev : [...prev, quote]
			);
		},
		[setUserMessageReferences]
	);

	const handleHighlightClick = useCallback(
		(highlight: PaperHighlight) => {
			setActiveHighlight?.(highlight);
			if (annotationsPanelActive && highlight.id) {
				onAnnotateViaSidePanel?.({ highlightId: highlight.id });
			}
		},
		[setActiveHighlight, annotationsPanelActive, onAnnotateViaSidePanel]
	);

	// Hovering a highlight previews its notes. Only useful when the inline cards
	// aren't already on screen showing the same thing.
	const [hovered, setHovered] = useState<{
		highlight: PaperHighlight;
		point: { x: number; y: number };
	} | null>(null);
	const handleHighlightHover = useCallback(
		(highlight: PaperHighlight | null, point: { x: number; y: number } | null) => {
			setHovered(highlight && point ? { highlight, point } : null);
		},
		[]
	);
	const hoveredAnnotations = useMemo(
		() =>
			hovered?.highlight.id
				? annotations.filter((a) => a.highlight_id === hovered.highlight.id)
				: [],
		[annotations, hovered]
	);

	const alignLeft = sidePanelOpen && showAnnotationCards && !annotationsPanelActive;

	return (
		<div
			className={`flex h-full w-full flex-col${alignLeft ? " pdf-align-left" : ""}`}
		>
			<ReaderToolbar
				{...toolbarProps}
				displayedPaperId={displayedPaperId}
				isReadMode={isReadMode}
				onToggleReadMode={onToggleReadMode}
				showAnnotationCards={showAnnotationCards}
				onToggleAnnotationCards={onToggleAnnotationCards}
			/>
			<div className="flex min-h-0 flex-1">
				<ReaderSidebar />
				<div className="relative min-w-0 flex-1">
					<PdfPane
						pdfUrl={pdfUrl}
						displayedPaperId={displayedPaperId}
						onRefreshUrl={onRefreshUrl}
					>
						<HighlightLayer
							highlights={anchored}
							activeHighlightId={activeHighlight?.id ?? null}
							onHighlightClick={handleHighlightClick}
							onHighlightHover={
								showAnnotationCards ? undefined : handleHighlightHover
							}
							onRenderedPositions={onOverlaysCreated}
						/>
						{showAnnotationCards && !annotationsPanelActive && (
							<AnnotationCardsLayer
								highlights={anchored}
								annotations={annotations}
								activeHighlight={activeHighlight}
								composeHighlightId={composeHighlightId}
								currentUser={currentUser}
								addAnnotation={addAnnotation}
								updateAnnotation={updateAnnotation}
								removeAnnotation={removeAnnotation}
								onClose={() => setActiveHighlight?.(null)}
								onCardFocus={(highlightId) => {
									const match = highlights.find((h) => h.id === highlightId);
									if (match) setActiveHighlight?.(match);
								}}
							/>
						)}
						{hovered && hoveredAnnotations.length > 0 && !showAnnotationCards && (
							<AnnotationHoverCard
								annotations={hoveredAnnotations}
								position={hovered.point}
								user={currentUser ?? null}
							/>
						)}
						<SelectionLayer
							onHighlight={
								addHighlight
									? (anchor, color) => void persistHighlight(anchor, color, false)
									: undefined
							}
							onAnnotate={
								addHighlight
									? (anchor, color) => void persistHighlight(anchor, color, true)
									: undefined
							}
							onAskAi={setUserMessageReferences ? handleAskAi : undefined}
						/>
					</PdfPane>
				</div>
			</div>
		</div>
	);
}

/**
 * openpaper's PDF reader.
 *
 * Replaces the react-pdf-highlighter-extended viewer with pdf.js's own
 * `PDFViewer`, keeping every seam the chat and side panel depend on:
 * `explicitSearchTerm` still drives scroll-to-quote (now through pdf.js's find
 * controller), selections still feed `setUserMessageReferences`, and
 * `onOverlaysCreated` still reports which highlights are anchored.
 *
 * The jotai `Provider` is mounted here rather than app-wide so viewer state is
 * per-instance: switching between a paper and its supplementary tears down one
 * document's scale, page and find state instead of leaking it into the next.
 */
export function PdfReader(props: PdfReaderProps) {
	return (
		<Provider>
			<PdfReaderInner {...props} />
		</Provider>
	);
}

export type { RenderedHighlightPosition };
