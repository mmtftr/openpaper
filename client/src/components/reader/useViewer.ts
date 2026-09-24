"use client";

import { useCallback, useEffect, useRef } from "react";
import { atom, useAtomValue, useSetAtom } from "jotai";
import {
	pdfjsLib,
	EventBus,
	PDFLinkService,
	PDFFindController,
	PDFViewer,
	FeatureTest,
	TouchManager,
	AnnotationMode,
	AnnotationEditorType,
	PDF_DOCUMENT_OPTIONS,
	type PDFDocumentProxy,
} from "./pdfjs";
import {
	actualScaleAtom,
	currentPageAtom,
	findMatchCountAtom,
	loadingErrorAtom,
	loadingProgressAtom,
	numPagesAtom,
	pdfDocAtom,
	pdfUrlAtom,
	pdfViewerAtom,
	scaleValueAtom,
	spreadModeAtom,
} from "./atoms";
import { jumpToAnchor } from "./jumpToAnchor";
import type { TextAnchor, ScaleValue } from "./types";

export interface ViewerApi {
	/** One token spans annotation lookup AND its eventual scroll/corrections. */
	beginNavigation(): AbortController;
	cancelNavigation(): void;
	/** Instant geometry jump with a bounded correction pass for lazy layout. */
	jumpToAnchor(anchor: Pick<TextAnchor, "page" | "rects">, signal: AbortSignal): Promise<void>;
	goToPage(page: number): void;
	/** Scroll so that `topPercent` down page `page` sits a third into the viewport. */
	goToPagePercent(page: number, topPercent: number): void;
	find(query: string, findPrevious?: boolean, again?: boolean): void;
	findClose(): void;
	zoomIn(): void;
	zoomOut(): void;
	getViewer(): PDFViewer | null;
}

export const viewerApiAtom = atom<ViewerApi | null>(null);

const SPREAD_MAP = { none: 0, odd: 1, even: 2 } as const;

// Ported from Mozilla pdf.js v5.5.207 (Apache License 2.0),
// web/pdf_viewer.mjs `normalizeWheelEventDirection`.
function normalizeWheelEventDirection(evt: WheelEvent) {
	let delta = Math.hypot(evt.deltaX, evt.deltaY);
	const angle = Math.atan2(evt.deltaY, evt.deltaX);
	if (-0.25 * Math.PI < angle && angle < 0.75 * Math.PI) {
		delta = -delta;
	}
	return delta;
}

const DEFAULT_ZOOM_DELAY = 400;
/** Expiring S3 links come back as 403; refresh and retry a bounded number of times. */
const MAX_URL_REFRESH_ATTEMPTS = 2;

interface ViewerRefs {
	viewer: PDFViewer | null;
	eventBus: EventBus | null;
	linkService: PDFLinkService | null;
	findController: PDFFindController | null;
	scaleValue: ScaleValue;
	isCtrlKeyDown: boolean;
}

export interface UsePdfViewerOptions {
	/** Called when the PDF fails to load with an auth error; should return a fresh URL. */
	onRefreshUrl?: () => Promise<string | null>;
}

export function usePdfViewer({ onRefreshUrl }: UsePdfViewerOptions = {}) {
	const containerRef = useRef<HTMLDivElement>(null);
	const url = useAtomValue(pdfUrlAtom);
	const scaleValue = useAtomValue(scaleValueAtom);
	const spreadMode = useAtomValue(spreadModeAtom);

	const setNumPages = useSetAtom(numPagesAtom);
	const setProgress = useSetAtom(loadingProgressAtom);
	const setError = useSetAtom(loadingErrorAtom);
	const setPage = useSetAtom(currentPageAtom);
	const setActualScale = useSetAtom(actualScaleAtom);
	const setFindMatches = useSetAtom(findMatchCountAtom);
	const setDoc = useSetAtom(pdfDocAtom);
	const setViewer = useSetAtom(pdfViewerAtom);
	const setApi = useSetAtom(viewerApiAtom);
	const setScaleValue = useSetAtom(scaleValueAtom);
	const setUrl = useSetAtom(pdfUrlAtom);

	const refreshAttemptsRef = useRef(0);
	const onRefreshUrlRef = useRef(onRefreshUrl);
	onRefreshUrlRef.current = onRefreshUrl;
	const navigationRef = useRef<AbortController | null>(null);
	const cancelNavigation = useCallback(() => { navigationRef.current?.abort(); }, []);
	const beginNavigation = useCallback(() => {
		cancelNavigation();
		const controller = new AbortController();
		navigationRef.current = controller;
		return controller;
	}, [cancelNavigation]);

	const refs = useRef<ViewerRefs>({
		viewer: null,
		eventBus: null,
		linkService: null,
		findController: null,
		scaleValue: "auto",
		isCtrlKeyDown: false,
	});
	refs.current.scaleValue = scaleValue;

	useEffect(() => {
		const container = containerRef.current;
		if (!container) return;
		const r = refs.current;

		const eventBus = new EventBus();
		const linkService = new PDFLinkService({ eventBus });
		const findController = new PDFFindController({
			linkService,
			eventBus,
			updateMatchesCountOnProgress: true,
		});
		const viewerEl = container.querySelector(".pdfViewer") as HTMLDivElement;
		const viewer = new PDFViewer({
			container,
			viewer: viewerEl,
			eventBus,
			linkService,
			findController,
			textLayerMode: 2,
			// Render annotations (we need the link annotations for citation
			// resolution) but never interactive form widgets, and never the
			// annotation *editor* — openpaper treats uploaded PDFs as untrusted.
			annotationMode: AnnotationMode.ENABLE,
			annotationEditorMode: AnnotationEditorType.DISABLE,
		});
		linkService.setViewer(viewer);
		r.viewer = viewer;
		setViewer(viewer);
		r.eventBus = eventBus;
		r.linkService = linkService;
		r.findController = findController;

		const onPagesInit = () => {
			viewer.currentScaleValue = String(r.scaleValue);
		};
		const onScaleChanging = (e: { scale: number; presetValue?: unknown }) => {
			setActualScale(e.scale);
			// The first user wheel/pinch zoom while a named fit ("auto" /
			// "page-width") is active promotes the atom to that concrete scale.
			// Named-fit changes carry presetValue and must not promote.
			if (e.presetValue === undefined && typeof r.scaleValue !== "number") {
				setScaleValue(e.scale);
			}
		};
		const onPageChanging = (e: { pageNumber: number }) => setPage(e.pageNumber);
		const onFindMatches = (e: {
			matchesCount: { current: number; total: number };
		}) => setFindMatches(e.matchesCount);

		eventBus.on("pagesinit", onPagesInit);
		eventBus.on("scalechanging", onScaleChanging);
		eventBus.on("pagechanging", onPageChanging);
		eventBus.on("updatefindmatchescount", onFindMatches);

		// --- Continuous factor zoom on top of pdf.js updateScale ---
		const updateZoom = (
			steps: number | null,
			scaleFactor: number | null,
			origin?: [number, number]
		) => {
			viewer.updateScale({
				drawingDelay: DEFAULT_ZOOM_DELAY,
				steps: steps ?? undefined,
				scaleFactor: scaleFactor ?? undefined,
				origin,
			});
		};

		// Accumulators keep sub-step remainders so tiny trackpad deltas
		// compose without rounding drift.
		const accumulateFactor = (
			previousScale: number,
			factor: number,
			acc: { value: number }
		) => {
			if (factor === 1) return 1;
			let next: number;
			if ((acc.value > 1 && factor < 1) || (acc.value < 1 && factor > 1)) {
				next = Math.floor(previousScale * factor * 100) / (100 * previousScale);
			} else {
				next =
					Math.floor(previousScale * factor * acc.value * 100) /
					(100 * previousScale);
			}
			acc.value = factor / next;
			return next;
		};

		const accumulateTicks = (delta: number, acc: { value: number }) => {
			let total: number;
			if ((acc.value > 0 && delta < 0) || (acc.value < 0 && delta > 0)) {
				total = delta;
			} else {
				total = acc.value + delta;
			}
			const whole = Math.trunc(total);
			acc.value = total - whole;
			return whole;
		};

		const wheelFactor = { value: 1 };
		const wheelTicks = { value: 0 };
		const touchFactor = { value: 1 };

		const onWheel = (evt: WheelEvent) => {
			if (viewer.isInPresentationMode) return;

			// Trackpad pinch maps to a wheel event with ctrlKey set; if the ctrl
			// key isn't physically held we infer a pinch gesture.
			const { deltaMode } = evt;
			const pinchCandidate = Math.exp(-evt.deltaY / 100);
			const isPinch =
				evt.ctrlKey &&
				!r.isCtrlKeyDown &&
				deltaMode === WheelEvent.DOM_DELTA_PIXEL &&
				evt.deltaX === 0 &&
				(Math.abs(pinchCandidate - 1) < 0.05 || FeatureTest.platform.isMac) &&
				evt.deltaZ === 0;
			const factor = isPinch ? pinchCandidate : Math.exp(-evt.deltaY / 200);

			if (!(isPinch || evt.ctrlKey || evt.metaKey)) return;
			// Only zoom the pages, not the entire viewer.
			evt.preventDefault();

			const rect = viewer.container.getBoundingClientRect();
			const origin: [number, number] = [
				evt.clientX - rect.left + viewer.container.offsetLeft,
				evt.clientY - rect.top + viewer.container.offsetTop,
			];

			if (isPinch || deltaMode === WheelEvent.DOM_DELTA_PIXEL) {
				updateZoom(
					null,
					accumulateFactor(viewer.currentScale, factor, wheelFactor),
					origin
				);
			} else {
				const delta = normalizeWheelEventDirection(evt);
				const ticks =
					Math.abs(delta) >= 1
						? Math.sign(delta)
						: accumulateTicks(delta, wheelTicks);
				updateZoom(ticks, null, origin);
			}
		};
		container.addEventListener("wheel", onWheel, { passive: false });

		const zoomAbort = new AbortController();
		// pdf.js's shipped .d.ts types the pinch callbacks as null even though
		// the runtime invokes them (upstream app.js relies on this too).
		const touchOptions = {
			container,
			onPinching: (origin: number[], prevDistance: number, distance: number) => {
				updateZoom(
					null,
					accumulateFactor(
						viewer.currentScale,
						distance / prevDistance,
						touchFactor
					),
					origin as [number, number]
				);
			},
			onPinchEnd: () => {
				touchFactor.value = 1;
			},
			signal: zoomAbort.signal,
		} as unknown as ConstructorParameters<typeof TouchManager>[0];
		new TouchManager(touchOptions);

		const onKeyDown = (e: KeyboardEvent) => {
			r.isCtrlKeyDown = e.key === "Control";
		};
		const onKeyUp = (e: KeyboardEvent) => {
			if (e.key === "Control") r.isCtrlKeyDown = false;
		};
		window.addEventListener("keydown", onKeyDown);
		window.addEventListener("keyup", onKeyUp);

		// Include toolbar and sidebar intent, not just events in the scroller.
		// Capture runs before controls (including async outline destinations).
		const reader = container.closest("[data-pdf-reader]") ?? container;
		const navigationEvents = ["wheel", "touchstart", "pointerdown", "keydown", "click"];
		for (const event of navigationEvents) reader.addEventListener(event, cancelNavigation, { capture: true, passive: true });
		const api: ViewerApi = {
			beginNavigation,
			cancelNavigation,
			jumpToAnchor: (anchor, signal) => jumpToAnchor(viewer, anchor, signal),
			goToPage(page: number) {
				cancelNavigation();
				viewer.currentPageNumber = page;
			},
			goToPagePercent(page: number, topPercent: number) {
				cancelNavigation();
				const scroller = viewer.container as HTMLElement;
				const pageEl = scroller.querySelector(
					`[data-page-number="${page}"]`
				) as HTMLElement | null;
				if (!pageEl) {
					viewer.currentPageNumber = page;
					return;
				}
				const pageRect = pageEl.getBoundingClientRect();
				const target =
					pageRect.top +
					(topPercent / 100) * pageRect.height +
					scroller.scrollTop -
					scroller.getBoundingClientRect().top -
					scroller.clientHeight / 3;
				scroller.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
			},
			find(query: string, findPrevious = false, again = false) {
				cancelNavigation();
				eventBus.dispatch("find", {
					source: null,
					type: again ? "again" : "",
					query,
					phraseSearch: true,
					caseSensitive: false,
					entireWord: false,
					highlightAll: true,
					findPrevious,
					matchDiacritics: true,
				});
			},
			findClose() {
				eventBus.dispatch("findbarclose", { source: null });
			},
			zoomIn() {
				cancelNavigation();
				updateZoom(1, null);
			},
			zoomOut() {
				cancelNavigation();
				updateZoom(-1, null);
			},
			getViewer() {
				return refs.current.viewer;
			},
		};
		setApi(api);

		return () => {
			cancelNavigation();
			for (const event of navigationEvents) reader.removeEventListener(event, cancelNavigation, true);
			zoomAbort.abort();
			container.removeEventListener("wheel", onWheel);
			window.removeEventListener("keydown", onKeyDown);
			window.removeEventListener("keyup", onKeyUp);
			eventBus.off("pagesinit", onPagesInit);
			eventBus.off("scalechanging", onScaleChanging);
			eventBus.off("pagechanging", onPageChanging);
			eventBus.off("updatefindmatchescount", onFindMatches);
			setApi(null);
			setViewer(null);
			viewer.cleanup();
			r.viewer = null;
		};
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, []);

	useEffect(() => {
		if (!url) {
			setDoc(null);
			setNumPages(0);
			return;
		}
		let cancelled = false;
		const task = pdfjsLib.getDocument({ url, ...PDF_DOCUMENT_OPTIONS });
		task.onProgress = ({ loaded, total }: { loaded: number; total: number }) => {
			if (total > 0) setProgress(loaded / total);
		};
		task.promise
			.then((doc) => {
				if (cancelled) {
					doc.destroy();
					return;
				}
				refreshAttemptsRef.current = 0;
				const r = refs.current;
				r.viewer?.setDocument(doc);
				r.linkService?.setDocument(doc);
				r.findController?.setDocument(doc);
				setDoc(doc);
				setNumPages(doc.numPages);
				setError(null);
			})
			.catch(async (err: unknown) => {
				const e = err as { aborted?: boolean; status?: number; message?: string };
				if (e?.aborted || cancelled) return;

				// Presigned object-store URLs expire; ask the caller for a fresh
				// one and retry rather than showing the user a dead viewer.
				const isAuthError =
					e?.status === 403 ||
					e?.status === 401 ||
					/\b(403|401)\b/.test(e?.message ?? "");
				if (
					isAuthError &&
					onRefreshUrlRef.current &&
					refreshAttemptsRef.current < MAX_URL_REFRESH_ATTEMPTS
				) {
					refreshAttemptsRef.current += 1;
					try {
						const fresh = await onRefreshUrlRef.current();
						if (!cancelled && fresh && fresh !== url) {
							setUrl(fresh);
							return;
						}
					} catch {
						// fall through to surfacing the original error
					}
				}
				setError(e?.message ?? String(err));
			});
		return () => {
			cancelled = true;
			cancelNavigation();
			setDoc(null);
			refs.current.findController?.setDocument(
				null as unknown as PDFDocumentProxy
			);
			refs.current.viewer?.setDocument(null as unknown as PDFDocumentProxy);
			task.destroy();
		};
	}, [url, setDoc, setNumPages, setProgress, setError, setUrl, cancelNavigation]);

	useEffect(() => {
		const viewer = refs.current.viewer;
		if (!viewer) return;
		if (typeof scaleValue === "number") {
			viewer.currentScaleValue = String(scaleValue);
		} else {
			viewer.currentScaleValue = scaleValue;
		}
	}, [scaleValue]);

	// Re-apply the fit whenever the pane resizes while a named scale
	// ("auto"/"page-width") is active — the side panel is resizable, so this
	// fires a lot in openpaper.
	useEffect(() => {
		const container = containerRef.current;
		if (!container || typeof ResizeObserver === "undefined") return;
		const ro = new ResizeObserver(() => {
			const r = refs.current;
			if (!r.viewer || typeof r.scaleValue === "number") return;
			r.viewer.currentScaleValue = r.scaleValue;
		});
		ro.observe(container);
		return () => ro.disconnect();
	}, [containerRef]);

	useEffect(() => {
		const viewer = refs.current.viewer;
		if (viewer) viewer.spreadMode = SPREAD_MAP[spreadMode];
	}, [spreadMode]);

	const getContainer = useCallback(() => containerRef.current, []);

	return { containerRef, getContainer };
}
