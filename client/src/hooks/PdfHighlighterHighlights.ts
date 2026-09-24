import { useState, useEffect, useCallback } from "react";
import useSWR, { useSWRConfig } from "swr";
import { PaperHighlight, ScaledPosition, HighlightColor } from "@/lib/schema";
import { api, unwrap } from "@/lib/api/client";

const EMPTY: PaperHighlight[] = [];

// `refreshKey` changes when ingest's AI highlights stage finishes, so the
// list is re-read (see `useStageRefreshKey`).
const highlightsKey = (paperId: string, refreshKey: string): [string, string, string] => [
	"/api/highlight/{paper_id}",
	paperId,
	refreshKey,
];

/** `GET /api/highlight/{paper_id}`, minus unusable rows and duplicates. */
async function loadHighlights([, paperId]: [string, string, string]): Promise<PaperHighlight[]> {
	try {
		const data = await unwrap(
			api.GET("/api/highlight/{paper_id}", { params: { path: { paper_id: paperId } } })
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
		return validHighlights.filter(
			(highlight, index, self) =>
				index ===
				self.findIndex(
					(h) =>
						h.id === highlight.id ||
						(h.raw_text === highlight.raw_text &&
							h.page_number === highlight.page_number)
				)
		);
	} catch (error) {
		console.error("Error loading highlights from server:", error);
		throw error;
	}
}

export function useHighlighterHighlights(paperId: string, refreshKey = "") {
	// Cached per paper. The hook's bound `mutate` always targets the
	// CURRENT key, so after an await (the reader may have switched parent <->
	// supplementary meanwhile) writes go through the global `mutate` with the
	// key captured before the request — the result lands in the entry of the
	// paper it was made for, not in the newly displayed paper's list.
	const { data, mutate } = useSWR(
		paperId ? highlightsKey(paperId, refreshKey) : null,
		loadHighlights
	);
	const { mutate: globalMutate } = useSWRConfig();
	const highlights = data ?? EMPTY;
	const [activeHighlight, setActiveHighlight] =
		useState<PaperHighlight | null>(null);

	const fetchHighlights = useCallback(async () => {
		await mutate();
	}, [mutate]);

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

		try {
			return await unwrap(
				api.POST("/api/highlight", {
					body: {
						paper_id: paperId,
						raw_text: highlight.raw_text,
						page_number: highlight.page_number,
						position: highlight.position,
						color: highlight.color,
					},
				})
			);
		} catch (error) {
			console.error("Error sending highlight to server:", error);
		}
	};

	// Remove highlight from server
	const removeHighlightFromServer = async (highlight: PaperHighlight) => {
		if (!highlight.id) return;
		const highlightId = highlight.id;
		const key = highlightsKey(paperId, refreshKey);
		try {
			await unwrap(
				api.DELETE("/api/highlight/{highlight_id}", {
					params: { path: { highlight_id: highlightId } },
				})
			);

			await globalMutate<PaperHighlight[]>(
				key,
				(prev) => prev?.filter((h) => h.id !== highlightId),
				{ revalidate: false }
			);
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

			const key = highlightsKey(paperId, refreshKey);
			let savedHighlight: PaperHighlight | undefined;
			try {
				const saved = await sendHighlightToServer(newHighlight);
				savedHighlight = saved;

				if (saved)
					await globalMutate<PaperHighlight[]>(
						key,
						(prev) => [...(prev ?? []), saved],
						{ revalidate: false }
					);
			} catch (error) {
				console.error("Error adding highlight:", error);
			}

			return savedHighlight;
		},
		[highlights, paperId, refreshKey, globalMutate]
	);

	// Remove a highlight
	const removeHighlight = useCallback(
		(highlight: PaperHighlight) => {
			removeHighlightFromServer(highlight);
		},
		// removeHighlightFromServer only closes over paperId, refreshKey + globalMutate.
		[paperId, refreshKey, globalMutate]
	);

	// Change a user highlight's colour. The PATCH replaces every field, so the
	// rest of the highlight is sent back unchanged.
	const recolorHighlight = useCallback(
		async (highlight: PaperHighlight, color: HighlightColor) => {
			if (!highlight.id || highlight.color === color) return;
			const highlightId = highlight.id;
			const previous = highlight.color;
			const key = highlightsKey(paperId, refreshKey);
			await globalMutate<PaperHighlight[]>(
				key,
				(prev) => prev?.map((h) => (h.id === highlightId ? { ...h, color } : h)),
				{ revalidate: false }
			);
			try {
				await unwrap(
					api.PATCH("/api/highlight/{highlight_id}", {
						params: { path: { highlight_id: highlightId } },
						body: {
							raw_text: highlight.raw_text,
							position: highlight.position ?? null,
							start_offset: highlight.start_offset ?? null,
							end_offset: highlight.end_offset ?? null,
							color,
						},
					})
				);
			} catch (error) {
				console.error("Error updating highlight colour:", error);
				// Only undo our own change — a newer pick may have landed since.
				await globalMutate<PaperHighlight[]>(
					key,
					(prev) =>
						prev?.map((h) =>
							h.id === highlightId && h.color === color ? { ...h, color: previous } : h
						),
					{ revalidate: false }
				);
			}
		},
		[paperId, refreshKey, globalMutate]
	);

	// A paper switch drops the previous paper's active selection right away
	// (its highlights are already out: the SWR key changed).
	useEffect(() => {
		setActiveHighlight(null);
	}, [paperId]);

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
