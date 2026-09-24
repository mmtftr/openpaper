"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAtomValue } from "jotai";
import type { PaperHighlight } from "@/lib/schema";
import { pdfDocAtom } from "./atoms";
import { anchorFromScaledPosition, locateQuote } from "./anchoring";
import type { TextAnchor } from "./types";

export interface AnchoredHighlight {
	key: string;
	highlight: PaperHighlight;
	anchor: TextAnchor;
}

export interface AnchoredHighlightsResult {
	anchored: AnchoredHighlight[];
	unanchored: PaperHighlight[];
	resolving: boolean;
	/** Immediately resolve this highlight, ahead of the background queue. */
	resolveHighlight(id: string, signal: AbortSignal): Promise<TextAnchor | null>;
}

const KEY_SEP = "\u001f";
function identityOf(highlight: PaperHighlight, index: number, hint?: number | null): string {
	return [highlight.id ?? `idx:${index}`, highlight.page_number ?? "", hint ?? "", highlight.raw_text ?? ""].join(KEY_SEP);
}

export function useAnchoredHighlights(
	highlights: PaperHighlight[],
	pageHints?: Map<string, number>
): AnchoredHighlightsResult {
	const pdfDoc = useAtomValue(pdfDocAtom);
	const currentDoc = useRef(pdfDoc);
	currentDoc.current = pdfDoc;
	const [revision, setRevision] = useState(0);
	const [resolving, setResolving] = useState(false);
	// A document owns its cache. A late result from a previous document can
	// neither replace the current cache nor satisfy a new jump request.
	const cache = useMemo(() => new Map<string, TextAnchor | null>(), [pdfDoc]);
	const priority = useMemo(() => ({ current: null as Promise<unknown> | null }), [pdfDoc]);
	const entries = useMemo(() => highlights.map((highlight, index) => {
		const hint = (highlight.id ? pageHints?.get(highlight.id) : undefined) ?? highlight.page_number;
		return {
			key: identityOf(highlight, index, hint), highlight, hint,
			anchor: highlight.position ? anchorFromScaledPosition(highlight.position, highlight.raw_text) : null,
		};
	}), [highlights, pageHints]);

	const resolveEntry = useCallback(async (
		entry: typeof entries[number], signal: AbortSignal, background = false
	): Promise<TextAnchor | null> => {
		if (entry.anchor) return entry.anchor;
		if (cache.has(entry.key)) return cache.get(entry.key) ?? null;
		if (!pdfDoc || signal.aborted) return null;
		const hit = entry.highlight.raw_text?.trim() ? await locateQuote(
			pdfDoc, entry.highlight.raw_text, entry.hint, {
				signal,
				// Pause between pages, not just between highlights. A single bad
				// quote can otherwise occupy the entire background scan.
				beforePage: background ? async () => { await priority.current; } : undefined,
			}
		) : null;
		if (signal.aborted || currentDoc.current !== pdfDoc) return null;
		const anchor = hit ? { ...hit, quote: entry.highlight.raw_text } : null;
		cache.set(entry.key, anchor);
		setRevision(n => n + 1);
		return anchor;
	}, [pdfDoc, cache, priority]);

	const resolveHighlight = useCallback((id: string, signal: AbortSignal) => {
		const entry = entries.find(e => e.highlight.id === id);
		if (!entry) return Promise.resolve(null);
		// Don't await a background search: it may currently be paused for an
		// older click. Page text promises are shared, so this doesn't re-extract.
		const task = resolveEntry(entry, signal).catch(() => null);
		priority.current = task;
		void task.finally(() => { if (priority.current === task) priority.current = null; });
		return task;
	}, [entries, resolveEntry, priority]);

	useEffect(() => {
		if (!pdfDoc) { setResolving(false); return; }
		const controller = new AbortController();
		setResolving(true);
		void (async () => {
			try {
				// Let a click arriving with the document start its hinted lookup
				// before speculative background work.
				await new Promise(resolve => setTimeout(resolve, 0));
				for (const entry of entries) {
					await priority.current;
					if (controller.signal.aborted) return;
					await resolveEntry(entry, controller.signal, true);
				}
			} finally {
				if (!controller.signal.aborted) setResolving(false);
			}
		})().catch(() => { /* Unreadable PDFs must not reject out of an effect. */ });
		return () => controller.abort();
	}, [pdfDoc, entries, resolveEntry, priority]);

	return useMemo(() => {
		const anchored: AnchoredHighlight[] = [];
		const unanchored: PaperHighlight[] = [];
		for (const entry of entries) {
			const anchor = entry.anchor ?? cache.get(entry.key);
			if (anchor) anchored.push({ key: entry.key, highlight: entry.highlight, anchor });
			else if (cache.has(entry.key)) unanchored.push(entry.highlight);
		}
		return { anchored, unanchored, resolving, resolveHighlight };
		// Revision signals changes to the document-owned cache.
	}, [entries, cache, revision, resolving, resolveHighlight]);
}
