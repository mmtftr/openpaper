"use client";

import { useCallback } from "react";
import { atom, useAtom, useAtomValue } from "jotai";
import { useReaderContext } from "./ReaderContext";
import { viewerApiAtom } from "./useViewer";

interface ScrollPoint {
	top: number;
	left: number;
}

/**
 * Where the reader was before it followed a link.
 *
 * Clicking a citation takes you to the bibliography, usually pages away from
 * what you were reading — without somewhere to come back to, that's a one-way
 * trip and you have to find your place by hand.
 */
const scrollHistoryAtom = atom<ScrollPoint[]>([]);

/** Matches alphaXiv's cap; deep enough to chase a chain of references. */
const MAX_HISTORY = 50;

export function useReaderNavigation() {
	const { containerRef } = useReaderContext();
	const api = useAtomValue(viewerApiAtom);
	const [history, setHistory] = useAtom(scrollHistoryAtom);

	/** Record the current position so a later `goBack` can return to it. */
	const pushHistory = useCallback(() => {
		const el = containerRef.current;
		if (!el) return;
		setHistory((prev) => {
			const point = { top: el.scrollTop, left: el.scrollLeft };
			const last = prev[prev.length - 1];
			// Following two links from the same spot shouldn't stack two entries.
			if (last && Math.abs(last.top - point.top) < 4) return prev;
			return [...prev, point].slice(-MAX_HISTORY);
		});
	}, [containerRef, setHistory]);

	/** Jump to a page, remembering where we came from. */
	const jumpToPage = useCallback(
		(page: number) => {
			pushHistory();
			api?.goToPage(page);
		},
		[api, pushHistory]
	);

	const goBack = useCallback(() => {
		const el = containerRef.current;
		if (!el || history.length === 0) return;
		// Read the target outside the updater: React may invoke an updater twice
		// under StrictMode, and a side effect belongs in neither invocation.
		const target = history[history.length - 1];
		el.scrollTo({ top: target.top, left: target.left, behavior: "smooth" });
		setHistory((prev) => prev.slice(0, -1));
	}, [containerRef, history, setHistory]);

	return {
		jumpToPage,
		pushHistory,
		goBack,
		canGoBack: history.length > 0,
	};
}
