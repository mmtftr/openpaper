"use client";

import { useCallback, useEffect, useMemo, useRef } from "react";
import { toast } from "sonner";
import { Provider, useAtomValue, useSetAtom } from "jotai";
import type { BasicUser } from "@/lib/auth";
import type {
	HighlightColor,
	PaperHighlight,
	PaperHighlightAnnotation,
	ScaledPosition,
	SupplementaryMaterialSummary,
} from "@/lib/schema";
import { findMatchCountAtom, findQueryAtom, outlineOpenAtom, outlineTabAtom, pdfDocAtom, readerActiveAtom } from "./atoms";
import { viewerApiAtom } from "./useViewer";
import PdfPane from "./PdfPane";
import { ReaderToolbar } from "./ReaderToolbar";
import { HighlightLayer } from "./HighlightLayer";
import SelectionLayer from "./SelectionLayer";
import { HighlightPopover } from "./HighlightPopover";
import { useHighlightPopover } from "./useHighlightPopover";
import type { HighlightPopoverTarget } from "./useHighlightPopover";
import type { HighlightHit } from "./HighlightLayer";
import Thumbnails from "./Thumbnails";
import Outline from "./Outline";
import { useHighlightJump, type HighlightJumpRequest } from "./useHighlightJump";
import { useAnchoredHighlights } from "./useAnchoredHighlights";
import { scaledPositionFromAnchor } from "./anchoring";
import type { RenderedHighlightPosition, TextAnchor } from "./types";

/** A request to find `term` in the PDF, starting at `page` when known. */
export interface TextSearchRequest {
	readonly term: string;
	/** 1-indexed page the chat agent quoted from, when known. */
	readonly page?: number;
	/** A new value on EVERY request, including a repeated click on one citation. */
	readonly nonce: number;
}

export interface PdfReaderProps {
	pdfUrl: string;
	/** Explicit panel navigation; nonce changes even when the id stays the same. */
	highlightJumpRequest?: HighlightJumpRequest | null;
	/**
	 * Quote pushed from a chat citation; searched and scrolled to. Each new
	 * request object searches again, even for the same term.
	 */
	explicitSearch?: TextSearchRequest | null;
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
	) => Promise<PaperHighlight | undefined> | void;
	removeHighlight?: (highlight: PaperHighlight) => void;
	recolorHighlight?: (highlight: PaperHighlight, color: HighlightColor) => void;

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
	/** The Annotations side panel is on screen: clicked highlights scroll it to their thread. */
	annotationsPanelActive?: boolean;
	/** "Open in Annotations" from a highlight's note popover. */
	onOpenThread?: (highlight: PaperHighlight) => void;
	/** A quote was sent to chat from the selection toolbar. */
	onAskStarted?: () => void;

	isReadMode?: boolean;
	onToggleReadMode?: () => void;
	/** False while mounted but hidden (see `readerActiveAtom`). */
	active?: boolean;

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
function ReaderSidebar({ displayedPaperId }: { displayedPaperId: string }) {
	const open = useAtomValue(outlineOpenAtom);
	const setTab = useSetAtom(outlineTabAtom);
	const tab = useAtomValue(outlineTabAtom);
	const setOpen = useSetAtom(outlineOpenAtom);
	if (!open) return null;
	// Beside the pages when there's room; over them in a narrow reader
	// (phones), where pushing the pages aside would leave them unreadable.
	return (
		<>
		<button
			type="button"
			aria-label="Close thumbnails and outline"
			onClick={() => setOpen(false)}
			className="absolute inset-0 z-20 bg-black/20 animate-in fade-in duration-200 md:hidden"
		/>
		<aside className="flex w-56 shrink-0 flex-col border-r border-border bg-background max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:w-64 max-md:max-w-[80%] max-md:shadow-xl animate-in fade-in slide-in-from-left-2 duration-200 ease-out-soft">
			<div className="flex h-9 shrink-0 items-center gap-1 border-b border-border px-2">
				{(["thumbnails", "outline"] as const).map((id) => (
					<button
						key={id}
						onClick={() => setTab(id)}
						className={`rounded-md px-2 py-1 text-[11px] capitalize transition-colors ${
							tab === id
								? "bg-brand/10 text-brand"
								: "text-muted-foreground hover:bg-muted hover:text-foreground"
						}`}
					>
						{id}
					</button>
				))}
			</div>
			<div className="min-h-0 flex-1 overflow-y-auto">
				{tab === "thumbnails" ? (
					<Thumbnails />
				) : (
					<Outline key={displayedPaperId} displayedPaperId={displayedPaperId} />
				)}
			</div>
		</aside>
		</>
	);
}

function PdfReaderInner(props: PdfReaderProps) {
	const {
		pdfUrl,
		highlightJumpRequest,
		explicitSearch,
		onSearchComplete,
		highlights = EMPTY_HIGHLIGHTS,
		annotations = EMPTY_ANNOTATIONS,
		activeHighlight = null,
		setActiveHighlight,
		addHighlight,
		removeHighlight,
		recolorHighlight,
		addAnnotation,
		updateAnnotation,
		removeAnnotation,
		setUserMessageReferences,
		onOverlaysCreated,
		onUnanchoredHighlights,
		onRefreshUrl,
		currentUser,
		annotationsPanelActive,
		onOpenThread,
		onAskStarted,
		isReadMode,
		onToggleReadMode,
		active = true,
		displayedPaperId = "",
		...toolbarProps
	} = props;

	const setReaderActive = useSetAtom(readerActiveAtom);
	useEffect(() => {
		setReaderActive(active);
	}, [active, setReaderActive]);

	const api = useAtomValue(viewerApiAtom);
	const pdfDoc = useAtomValue(pdfDocAtom);
	const matchCount = useAtomValue(findMatchCountAtom);
	const setFindQuery = useSetAtom(findQueryAtom);

	const pageHints = useMemo(() => new Map<string, number>(), []);
	const { anchored, unanchored, resolveHighlight } = useAnchoredHighlights(highlights, pageHints);
	useHighlightJump(explicitSearch?.term ? null : highlightJumpRequest, highlights, resolveHighlight);

	// Surface highlights we could not place, so the side panel can say so.
	const unanchoredKey = unanchored.map((h) => h.id ?? h.raw_text).join("|");
	useEffect(() => {
		onUnanchoredHighlights?.(unanchored);
		// `unanchoredKey` stands in for the identity of `unanchored`.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [unanchoredKey]);

	// --- Chat citation → find + scroll -------------------------------------
	// A citation can switch the displayed paper in the same render that asks
	// for the search. The doc on screen when `pdfUrl` last changed is the
	// outgoing one: a find dispatched before the new PDF replaces it is
	// dropped by pdf.js, so each search waits for a doc newer than that.
	const staleDocRef = useRef<typeof pdfDoc>(null);
	useEffect(() => {
		staleDocRef.current = pdfDoc;
		// Only a URL change marks the current doc stale (pdfDoc is read, not
		// a dependency).
	}, [pdfUrl]);
	const appliedSearchRef = useRef<TextSearchRequest | null>(null);
	const pendingSearchRef = useRef<string | null>(null);
	useEffect(() => {
		const term = explicitSearch?.term.trim();
		if (!term || !explicitSearch || !api) return;
		if (appliedSearchRef.current === explicitSearch) return;
		if (!pdfDoc || pdfDoc === staleDocRef.current) return;
		appliedSearchRef.current = explicitSearch;
		pendingSearchRef.current = term;
		if (explicitSearch?.page) api.goToPage(explicitSearch.page);
		setFindQuery(term);
		api.find(term);
	}, [explicitSearch, api, pdfDoc, setFindQuery]);

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

	// --- Highlight popover (note thread, or actions for a bare highlight) ---
	const popover = useHighlightPopover();
	const {
		target: popoverTarget,
		engage,
		isDirty,
		clickAway,
		close: closePopover,
	} = popover;
	const popoverHighlight = useMemo(
		() =>
			popoverTarget
				? highlights.find((h) => h.id === popoverTarget.highlightId) ?? null
				: null,
		[popoverTarget, highlights]
	);
	const popoverNotes = useMemo(
		() =>
			popoverTarget
				? annotations.filter((a) => a.highlight_id === popoverTarget.highlightId)
				: EMPTY_ANNOTATIONS,
		[popoverTarget, annotations]
	);

	// A panel jump dismisses a hover card that could obscure the destination.
	useEffect(() => {
		if (highlightJumpRequest && !isDirty()) closePopover();
	}, [highlightJumpRequest, closePopover, isDirty]);

	// Bumped per user action so a slow note save can't pop a composer over
	// whatever the user did next (another highlight, Ask, another document).
	const selectionActionSeq = useRef(0);

	// Another document means another set of highlights.
	useEffect(() => {
		selectionActionSeq.current++;
		closePopover();
	}, [pdfUrl, closePopover]);

	// --- Selection actions --------------------------------------------------
	const persistHighlight = useCallback(
		async (anchor: TextAnchor, color: HighlightColor, doAnnotate: boolean) => {
			if (!pdfDoc || !addHighlight) return;
			// Opening a composer here would replace a note still being written.
			if (doAnnotate && isDirty()) {
				toast("Finish or discard the note you're writing first.");
				return;
			}
			const seq = ++selectionActionSeq.current;
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
				const saved = await addHighlight(
					anchor.quote,
					position,
					anchor.page,
					doAnnotate,
					color
				);
				if (
					doAnnotate &&
					saved?.id &&
					seq === selectionActionSeq.current &&
					!isDirty()
				) {
					// Re-selecting already-highlighted text returns the existing
					// highlight; that one must survive an abandoned note.
					const existed = highlights.some((h) => h.id === saved.id);
					engage(saved, -1, { compose: true, createdForNote: !existed });
				}
			} catch (error) {
				console.error("Failed to persist highlight:", error);
			}
		},
		[pdfDoc, addHighlight, highlights, engage, isDirty]
	);

	const handleAskAi = useCallback(
		(quote: string) => {
			selectionActionSeq.current++;
			setUserMessageReferences?.((prev) =>
				prev.includes(quote) ? prev : [...prev, quote]
			);
			onAskStarted?.();
		},
		[setUserMessageReferences, onAskStarted]
	);

	const handleHighlightClick = useCallback(
		({ highlight, rectIndex }: HighlightHit) => {
			const cur = popoverTarget;
			if (cur?.engaged && cur.highlightId === highlight.id) return;
			// Don't throw away an unsaved note by clicking another highlight.
			if (cur && isDirty()) return;
			selectionActionSeq.current++;
			engage(highlight, rectIndex);
			// The panel follows only when it's already showing annotations.
			if (annotationsPanelActive) setActiveHighlight?.(highlight);
		},
		[popoverTarget, isDirty, engage, annotationsPanelActive, setActiveHighlight]
	);

	// A highlight made by "Comment" exists only to hold the note. However its
	// popover goes away (Cancel, Escape, click-away, another highlight), drop it
	// if no note was saved.
	const prevTargetRef = useRef<HighlightPopoverTarget | null>(null);
	useEffect(() => {
		const prev = prevTargetRef.current;
		prevTargetRef.current = popoverTarget;
		if (!prev?.createdForNote || prev.highlightId === popoverTarget?.highlightId) return;
		if (annotations.some((a) => a.highlight_id === prev.highlightId)) return;
		const abandoned = highlights.find((h) => h.id === prev.highlightId);
		if (abandoned) removeHighlight?.(abandoned);
		// Only target transitions matter; the lists are read at that moment.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [popoverTarget]);

	return (
		<div className="flex h-full w-full flex-col" data-pdf-reader>
			<ReaderToolbar
				{...toolbarProps}
				displayedPaperId={displayedPaperId}
				isReadMode={isReadMode}
				onToggleReadMode={onToggleReadMode}
			/>
			<div className="relative flex min-h-0 flex-1">
				<ReaderSidebar displayedPaperId={displayedPaperId} />
				<div className="relative min-w-0 flex-1">
					<PdfPane
						pdfUrl={pdfUrl}
						displayedPaperId={displayedPaperId}
						onRefreshUrl={onRefreshUrl}
					>
						<HighlightLayer
							highlights={anchored}
							activeHighlightId={
								highlightJumpRequest && activeHighlight?.id === highlightJumpRequest.highlightId && isDirty()
									? activeHighlight?.id ?? null
									: popoverTarget?.highlightId ?? activeHighlight?.id ?? null
							}
							onHighlightClick={handleHighlightClick}
							onEmptyClick={clickAway}
							onHighlightHover={popover.hover}
							onPagePointerDown={popover.pagePointerDown}
							onRenderedPositions={onOverlaysCreated}
						/>
						<HighlightPopover
							target={popoverTarget}
							highlight={popoverHighlight}
							notes={popoverNotes}
							currentUser={currentUser}
							addAnnotation={addAnnotation}
							updateAnnotation={updateAnnotation}
							removeAnnotation={removeAnnotation}
							onRecolor={recolorHighlight}
							onDeleteHighlight={removeHighlight}
							onAskAi={setUserMessageReferences ? handleAskAi : undefined}
							onOpenThread={onOpenThread}
							close={closePopover}
							clickAway={clickAway}
							popoverEnter={popover.popoverEnter}
							popoverLeave={popover.popoverLeave}
							engageCurrent={popover.engageCurrent}
							setCompose={popover.setCompose}
							setDirty={popover.setDirty}
							isDirty={isDirty}
							cancelPending={popover.cancelPending}
						/>
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
 * `explicitSearch` still drives scroll-to-quote (now through pdf.js's find
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
