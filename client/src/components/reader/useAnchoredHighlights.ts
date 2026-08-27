"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useAtomValue } from "jotai";
import type { PaperHighlight } from "@/lib/schema";
import { pdfDocAtom } from "./atoms";
import { anchorFromScaledPosition, locateQuote } from "./anchoring";
import type { TextAnchor } from "./types";

export interface AnchoredHighlight {
	/** Stable per-render identity — see `identityOf`. */
	key: string;
	highlight: PaperHighlight;
	anchor: TextAnchor;
}

export interface AnchoredHighlightsResult {
	/** Highlights we can draw, in input order. */
	anchored: AnchoredHighlight[];
	/** Highlights we could not place — surfaced rather than silently dropped. */
	unanchored: PaperHighlight[];
	/** True while text-only highlights are still being searched for. */
	resolving: boolean;
}

/**
 * Identity of a highlight *as a search query*.
 *
 * Not just the id: if a quote is corrected or a page hint arrives, the cached
 * result for that id is stale and has to be recomputed. Falling back to the
 * quote alone isn't enough either — two id-less highlights quoting the same
 * sentence on different pages would collapse into one — so the input position
 * is folded in to keep them distinct.
 *
 * Parts are joined with a control character rather than a space: they include
 * arbitrary quote text, so a printable separator could collide with content and
 * make two different highlights collapse to the same identity.
 */
const KEY_SEP = "\u001f";

function identityOf(
	highlight: PaperHighlight,
	index: number,
	pageHint: number | undefined
): string {
	const id = highlight.id ?? `idx:${index}`;
	return [
		id,
		highlight.page_number ?? "",
		pageHint ?? "",
		highlight.raw_text ?? "",
	].join(KEY_SEP);
}

/**
 * Resolve every highlight to page-relative geometry.
 *
 * Two sources feed in:
 *
 *  - highlights that already carry a `position` (user-drawn ones, and assistant
 *    ones the ingestion job managed to anchor with PyMuPDF) convert straight
 *    across, synchronously;
 *  - highlights that arrived as text only are searched for in the document's
 *    text content, biased to the page the chat agent reported.
 *
 * Anything that still can't be placed is returned in `unanchored` instead of
 * being dropped. The old viewer discarded these silently, so an assistant
 * annotation could exist in the database, be perfectly readable server-side and
 * be invisible in the UI with no indication anything was missing
 * (PDF_HIGHLIGHTER_CAVEATS.md §4).
 */
export function useAnchoredHighlights(
	highlights: PaperHighlight[],
	/** Page hints from chat citations, keyed by highlight id. */
	pageHints?: Map<string, number>
): AnchoredHighlightsResult {
	const pdfDoc = useAtomValue(pdfDocAtom);

	/**
	 * Search results, tagged with the document they were computed against.
	 *
	 * The tag is what makes switching PDFs safe. An earlier version cleared this
	 * from a separate effect, which deadlocked: the clearing effect and the
	 * resolver effect ran in the same commit, the resolver read the *old* map
	 * through a ref, concluded there was nothing to do, and never re-ran because
	 * neither of its dependencies had changed afterwards — so every text-searched
	 * highlight disappeared for the rest of the session.
	 */
	const [resolved, setResolved] = useState<{
		doc: unknown;
		entries: Map<string, TextAnchor | null>;
	}>({ doc: null, entries: new Map() });
	const [resolving, setResolving] = useState(false);

	const pageHintFor = (highlight: PaperHighlight): number | undefined =>
		(highlight.id ? pageHints?.get(highlight.id) : undefined) ??
		highlight.page_number;

	// Split into the two paths up front so the synchronous half never waits on
	// the asynchronous one.
	const { direct, needsSearch } = useMemo(() => {
		const direct: AnchoredHighlight[] = [];
		const needsSearch: { key: string; highlight: PaperHighlight }[] = [];
		highlights.forEach((highlight, index) => {
			const key = identityOf(highlight, index, pageHintFor(highlight));
			if (highlight.position) {
				const anchor = anchorFromScaledPosition(
					highlight.position,
					highlight.raw_text
				);
				if (anchor) {
					direct.push({ key, highlight, anchor });
					return;
				}
			}
			if (highlight.raw_text?.trim()) needsSearch.push({ key, highlight });
		});
		return { direct, needsSearch };
		// `pageHints` is a stable Map owned by the caller; its contents feed the
		// keys above, and a change to them arrives with a new `highlights` array.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [highlights]);

	const searchKey = useMemo(
		() => needsSearch.map((n) => n.key).join(KEY_SEP),
		[needsSearch]
	);

	const resolvedRef = useRef(resolved);
	resolvedRef.current = resolved;
	const needsSearchRef = useRef(needsSearch);
	needsSearchRef.current = needsSearch;

	useEffect(() => {
		if (!pdfDoc) {
			setResolving(false);
			return;
		}
		let cancelled = false;

		(async () => {
			try {
				const sameDoc = resolvedRef.current.doc === pdfDoc;
				// Only search what this document hasn't already placed. Merging each
				// result as it lands (rather than replacing the map in one batch)
				// keeps already-drawn highlights on screen while the rest resolve.
				const pending = needsSearchRef.current.filter(
					(n) => !sameDoc || !resolvedRef.current.entries.has(n.key)
				);
				if (pending.length === 0) return;

				setResolving(true);
				for (const { key, highlight } of pending) {
					if (cancelled) return;
					const hit = await locateQuote(
						pdfDoc,
						highlight.raw_text,
						pageHintFor(highlight)
					);
					if (cancelled) return;
					const anchor = hit
						? { page: hit.page, quote: highlight.raw_text, rects: hit.rects }
						: null;
					setResolved((prev) => {
						const carryOver = prev.doc === pdfDoc ? prev.entries : new Map();
						const entries = new Map(carryOver);
						entries.set(key, anchor);
						return { doc: pdfDoc, entries };
					});
				}
			} finally {
				// Every exit path clears the flag, including the "nothing to do" one.
				if (!cancelled) setResolving(false);
			}
		})();

		return () => {
			cancelled = true;
		};
		// `searchKey` stands in for the identity of `needsSearch`; the resolved map
		// is read through a ref so merging a result doesn't restart the loop.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [pdfDoc, searchKey]);

	return useMemo(() => {
		const directByKey = new Map(direct.map((d) => [d.key, d]));
		const entries = resolved.doc === pdfDoc ? resolved.entries : null;
		const anchored: AnchoredHighlight[] = [];
		const unanchored: PaperHighlight[] = [];

		highlights.forEach((highlight, index) => {
			const key = identityOf(highlight, index, pageHintFor(highlight));
			const fromPosition = directByKey.get(key);
			if (fromPosition) {
				anchored.push(fromPosition);
				return;
			}
			if (!entries || !entries.has(key)) return; // not searched yet
			const searched = entries.get(key);
			if (searched) anchored.push({ key, highlight, anchor: searched });
			else unanchored.push(highlight);
		});

		return { anchored, unanchored, resolving };
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [highlights, direct, resolved, resolving, pdfDoc]);
}
