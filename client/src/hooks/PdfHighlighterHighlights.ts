import { useState, useEffect, useCallback } from "react";
import { PaperHighlight, ScaledPosition, HighlightColor } from "@/lib/schema";
import { fetchFromApi } from "@/lib/api";

export function useHighlighterHighlights(paperId: string) {
	const [highlights, setHighlights] = useState<Array<PaperHighlight>>([]);
	const [activeHighlight, setActiveHighlight] =
		useState<PaperHighlight | null>(null);

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

			// Filter valid highlights - require either position or offsets
			const validHighlights = data.filter(
				(h) =>
					h.raw_text &&
					(h.position ||
						(typeof h.start_offset === "number" &&
							typeof h.end_offset === "number"))
			);

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

			setHighlights(deduplicatedHighlights);
		} catch (error) {
			console.error("Error loading highlights from server:", error);
		}
	}, [paperId]);

	// Send highlight to server
	const sendHighlightToServer = async (
		highlight: Omit<PaperHighlight, "id">
	): Promise<PaperHighlight | undefined> => {
		// Check for duplicates
		const isDuplicate = highlights.some(
			(h) =>
				h.raw_text === highlight.raw_text &&
				h.page_number === highlight.page_number
		);

		if (isDuplicate) {
			return;
		}

		const payload = {
			paper_id: paperId,
			raw_text: highlight.raw_text,
			page_number: highlight.page_number,
			position: highlight.position,
			role: highlight.role || "user",
			color: highlight.color,
		};

		try {
			const data = await fetchFromApi(`/api/highlight`, {
				method: "POST",
				headers: {
					"Content-Type": "application/json",
					Accept: "application/json",
				},
				body: JSON.stringify(payload),
			});
			return data;
		} catch (error) {
			console.error("Error sending highlight to server:", error);
		}
	};

	// Remove highlight from server
	const removeHighlightFromServer = async (highlight: PaperHighlight) => {
		try {
			await fetchFromApi(`/api/highlight/${highlight.id}`, {
				method: "DELETE",
				headers: {
					"Content-Type": "application/json",
					Accept: "application/json",
				},
			});

			setHighlights((prev) => prev.filter((h) => h.id !== highlight.id));
		} catch (error) {
			console.error("Error removing highlight from server:", error);
		}
	};

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

			// Re-selecting already-highlighted text: hand back the existing one
			// so the caller can annotate it (sendHighlightToServer would drop it
			// as a duplicate). It isn't made active — for an AI highlight that
			// would also switch the side panel to Annotations.
			const existing = highlights.find(
				(h) =>
					h.raw_text === selectedText &&
					h.page_number === (pageNumber || position.boundingRect.pageNumber)
			);
			if (existing && doAnnotate) return existing;

			const newHighlight: Omit<PaperHighlight, "id"> = {
				raw_text: selectedText,
				role: "user",
				page_number: pageNumber || position.boundingRect.pageNumber,
				position: position,
				color: color,
			};

			let savedHighlight: PaperHighlight | undefined;
			try {
				const saved = await sendHighlightToServer(newHighlight);
				savedHighlight = saved;

				if (saved) setHighlights((prev) => [...prev, saved]);
			} catch (error) {
				console.error("Error adding highlight:", error);
			}

			return savedHighlight;
		},
		[highlights]
	);

	// Remove a highlight
	const removeHighlight = useCallback((highlight: PaperHighlight) => {
		removeHighlightFromServer(highlight);
	}, []);

	// Change a user highlight's colour. The PATCH replaces every field, so the
	// rest of the highlight is sent back unchanged.
	const recolorHighlight = useCallback(
		async (highlight: PaperHighlight, color: HighlightColor) => {
			if (!highlight.id || highlight.color === color) return;
			const previous = highlight.color;
			setHighlights((prev) =>
				prev.map((h) => (h.id === highlight.id ? { ...h, color } : h))
			);
			try {
				await fetchFromApi(`/api/highlight/${highlight.id}`, {
					method: "PATCH",
					headers: {
						"Content-Type": "application/json",
						Accept: "application/json",
					},
					body: JSON.stringify({
						raw_text: highlight.raw_text,
						position: highlight.position ?? null,
						start_offset: highlight.start_offset ?? null,
						end_offset: highlight.end_offset ?? null,
						color,
					}),
				});
			} catch (error) {
				console.error("Error updating highlight colour:", error);
				// Only undo our own change — a newer pick may have landed since.
				setHighlights((prev) =>
					prev.map((h) =>
						h.id === highlight.id && h.color === color ? { ...h, color: previous } : h
					)
				);
			}
		},
		[]
	);

	// Load highlights on mount or when paperId changes
	useEffect(() => {
		fetchHighlights();
	}, [paperId, fetchHighlights]);

	return {
		highlights,
		recolorHighlight,
		activeHighlight,
		setActiveHighlight,
		addHighlight,
		removeHighlight,
		fetchHighlights,
	};
}
