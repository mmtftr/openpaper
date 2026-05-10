import { useState, useEffect, useCallback, useRef } from "react";
import { PaperHighlight, ScaledPosition, HighlightColor } from "@/lib/schema";
import { fetchFromApi } from "@/lib/api";
import {
	cacheHighlights,
	getCachedHighlights,
	OFFLINE_HIGHLIGHTS_CHANGED_EVENT,
	queueHighlightCreate,
	queueHighlightDelete,
	replayOutbox,
} from "@/lib/offline";
import { nanoid } from "nanoid";

export function useHighlighterHighlights(
	paperId: string,
	readOnlyHighlights: Array<PaperHighlight> = []
) {
	const [highlights, setHighlights] = useState<Array<PaperHighlight>>([]);
	const [selectedText, setSelectedText] = useState<string>("");
	const [tooltipPosition, setTooltipPosition] = useState<{
		x: number;
		y: number;
	} | null>(null);
	const [isAnnotating, setIsAnnotating] = useState(false);
	const [isHighlightInteraction, setIsHighlightInteraction] = useState(false);
	const [activeHighlight, setActiveHighlight] =
		useState<PaperHighlight | null>(null);
	const blockScrollOnNextHighlight = useRef(false);

	// Fetch highlights from server
	const fetchHighlights = useCallback(async () => {
		try {
			const data: PaperHighlight[] = await fetchFromApi(
				`/api/highlight/${paperId}`,
				{
					method: "GET",
					headers: {
						"Content-Type": "application/json",
						Accept: "application/json",
					},
				}
			);

			// Filter valid highlights. Assistant rows can be text-only because
			// the viewer attempts to anchor them at render time from raw_text.
			const validHighlights = data.filter((h) => {
				if (!h.raw_text?.trim()) return false;
				if (h.role === "assistant") return true;
				return Boolean(
					h.position ||
						(typeof h.start_offset === "number" &&
							typeof h.end_offset === "number")
				);
			});

			// Deduplicate
			const deduplicatedHighlights = validHighlights.filter(
				(highlight, index, self) =>
					index ===
					self.findIndex(
						(h) =>
							h.id === highlight.id ||
							(h.raw_text === highlight.raw_text &&
								h.page_number === highlight.page_number)
					)
			);

			// Preserve `local:` highlights that the user just created — they're
			// still in the outbox waiting to replay. Without this merge the
			// server fetch would clobber them and the user would watch their
			// brand-new highlight vanish from the page mid-replay.
			const cached = await getCachedHighlights(paperId).catch(() => null);
			const serverKeys = new Set(
				deduplicatedHighlights.map(
					(h) => `${h.raw_text}::${h.page_number}`
				)
			);
			const stillPending = (cached?.highlights || []).filter(
				(h) =>
					h.id?.startsWith("local:") &&
					!serverKeys.has(`${h.raw_text}::${h.page_number}`)
			);
			const merged = [...deduplicatedHighlights, ...stillPending];

			setHighlights(merged);
			await cacheHighlights(paperId, merged);
		} catch (error) {
			console.error("Error loading highlights from server:", error);
			const cached = await getCachedHighlights(paperId).catch(() => null);
			if (cached) {
				setHighlights(cached.highlights);
			}
		}
	}, [paperId]);

	// Add a new highlight with position data
	const addHighlight = useCallback(
		async (
			selectedText: string,
			position?: ScaledPosition,
			pageNumber?: number,
			doAnnotate?: boolean,
			color?: HighlightColor
		) => {
			if (!position) {
				console.error("Position is required for highlights");
				return;
			}

			const isDuplicate = highlights.some(
				(h) =>
					h.raw_text === selectedText &&
					h.page_number === (pageNumber || position.boundingRect.pageNumber)
			);
			if (isDuplicate) return;

			const localId = `local:${nanoid()}`;
			const savedHighlight: PaperHighlight = {
				id: localId,
				raw_text: selectedText,
				role: "user",
				page_number: pageNumber || position.boundingRect.pageNumber,
				position: position,
				color: color,
			};

			try {
				const nextHighlights = [...highlights, savedHighlight];
				setHighlights(nextHighlights);
				await cacheHighlights(paperId, nextHighlights);
				await queueHighlightCreate({
					localId,
					paperId,
					highlight: savedHighlight,
				});

				if (doAnnotate) {
					blockScrollOnNextHighlight.current = true;
					setActiveHighlight(savedHighlight);
					setIsAnnotating(true);
				}

				if (typeof navigator === "undefined" || navigator.onLine) {
					void replayOutbox();
				}
			} catch (error) {
				console.error("Error adding highlight:", error);
			}

			// Reset states
			setSelectedText("");
			setTooltipPosition(null);
			if (!doAnnotate) {
				setIsAnnotating(false);
			}
		},
		[highlights, paperId]
	);

	// Remove a highlight
	const removeHighlight = useCallback((highlight: PaperHighlight) => {
		if (!highlight.id) return;
		const nextHighlights = highlights.filter((h) => h.id !== highlight.id);
		setHighlights(nextHighlights);
		void cacheHighlights(paperId, nextHighlights);
		void queueHighlightDelete({ highlightId: highlight.id }, paperId).then(() => {
			if (typeof navigator === "undefined" || navigator.onLine) {
				void replayOutbox();
			}
		});
	}, [highlights, paperId]);

	// Handle text selection (for compatibility, though not used with new viewer)
	const handleTextSelection = useCallback(
		(e: React.MouseEvent | MouseEvent) => {
			const selection = window.getSelection();
			if (selection && selection.toString()) {
				let text = selection.toString();
				setIsHighlightInteraction(false);

				// Normalize the text
				text = text.replace(/\s+/g, " ").trim();
				setSelectedText(text);

				setTooltipPosition({
					x: e.clientX,
					y: e.clientY,
				});
			} else {
				if (!isHighlightInteraction && selectedText) {
					setTimeout(() => {
						if (!isHighlightInteraction) {
							const currentSelection = window.getSelection();
							if (!currentSelection?.toString()) {
								setSelectedText("");
							}
							setTooltipPosition(null);
						}
					}, 200);
				}
			}
		},
		[isHighlightInteraction, selectedText]
	);

	// Clear highlights from state
	const clearHighlights = useCallback(() => {
		setHighlights([]);
	}, []);

	// Refresh highlights
	const refreshHighlights = useCallback(async () => {
		await fetchHighlights();
	}, [fetchHighlights]);

	// Load highlights on mount or when readOnlyHighlights changes
	useEffect(() => {
		if (readOnlyHighlights.length > 0) {
			setHighlights(readOnlyHighlights);
		} else {
			fetchHighlights();
		}
	}, [paperId, readOnlyHighlights.length, fetchHighlights]);

	// When the replay loop promotes a local highlight id to a server id, the
	// IDB cache is the authoritative state — sync React state from it so a
	// follow-up delete uses the real server id.
	useEffect(() => {
		const handler = (event: Event) => {
			const detail = (event as CustomEvent<{ paperId?: string }>).detail;
			if (detail?.paperId && detail.paperId !== paperId) return;
			getCachedHighlights(paperId)
				.then((cached) => {
					if (cached) setHighlights(cached.highlights);
				})
				.catch(() => undefined);
		};
		window.addEventListener(OFFLINE_HIGHLIGHTS_CHANGED_EVENT, handler);
		return () => window.removeEventListener(OFFLINE_HIGHLIGHTS_CHANGED_EVENT, handler);
	}, [paperId]);

	// Reset interaction state when selectedText is cleared
	useEffect(() => {
		if (!selectedText) {
			setIsHighlightInteraction(false);
		}
	}, [selectedText]);

	// Handle active highlight scrolling
	useEffect(() => {
		if (activeHighlight && !blockScrollOnNextHighlight.current) {
			// Scrolling is handled by the PdfHighlighter component via utilsRef
		}
		blockScrollOnNextHighlight.current = false;
	}, [activeHighlight]);

	return {
		highlights,
		setHighlights,
		selectedText,
		setSelectedText,
		tooltipPosition,
		setTooltipPosition,
		isAnnotating,
		setIsAnnotating,
		isHighlightInteraction,
		setIsHighlightInteraction,
		activeHighlight,
		setActiveHighlight,
		handleTextSelection,
		clearHighlights,
		addHighlight,
		removeHighlight,
		fetchHighlights,
		refreshHighlights,
	};
}
