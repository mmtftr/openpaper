"use client";

import { useEffect, useRef } from "react";
import { useAtomValue } from "jotai";
import type { PaperHighlight } from "@/lib/schema";
import { pdfDocAtom } from "./atoms";
import { viewerApiAtom } from "./useViewer";
import type { AnchoredHighlightsResult } from "./useAnchoredHighlights";

export interface HighlightJumpRequest {
	readonly highlightId: string;
	/** A new value on EVERY click, including a repeated click on one thread. */
	readonly nonce: number;
}

// Requests belong to the caller, which survives desktop/mobile reader remounts.
// Keep consumption with that immutable request, not a hook ref or a PDF proxy:
// a refreshed URL creates a new proxy for the same document fingerprint.
// Weak keys release this history when the caller drops the request; separate
// readers/callers using the same highlight and nonce do not suppress each other.
const handledRequests = new WeakMap<HighlightJumpRequest, Set<string>>();

export function useHighlightJump(
	request: HighlightJumpRequest | null | undefined,
	highlights: PaperHighlight[],
	resolveHighlight: AnchoredHighlightsResult["resolveHighlight"]
) {
	const api = useAtomValue(viewerApiAtom);
	const doc = useAtomValue(pdfDocAtom);
	const inputs = useRef({ highlights, resolveHighlight });
	inputs.current = { highlights, resolveHighlight };
	useEffect(() => {
		if (!request || !api || !doc) return;
		const highlight = inputs.current.highlights.find(h => h.id === request.highlightId);
		if (!highlight) return;
		const key = JSON.stringify([doc.fingerprints[0], request.highlightId, request.nonce]);
		if (handledRequests.get(request)?.has(key)) return;
		const controller = api.beginNavigation();
		const searchController = new AbortController();
		let disposed = false;
		let timer: ReturnType<typeof setTimeout> | undefined;
		void (async () => {
			try {
				// Let React's StrictMode setup/cleanup probe finish before claiming
				// the request. A real start is consumed even if navigation cancels it.
				await Promise.resolve();
				if (disposed) return;
				const handled = handledRequests.get(request) ?? new Set<string>();
				if (handled.has(key)) return;
				handled.add(key);
				handledRequests.set(request, handled);
				if (controller.signal.aborted) return;
				// Stop a preceding citation match from taking back the scroll.
				api.findClose();
				const anchor = await Promise.race([
					inputs.current.resolveHighlight(request.highlightId, AbortSignal.any([controller.signal, searchController.signal])),
					new Promise<null>(resolve => {
						timer = setTimeout(() => { searchController.abort(); resolve(null); }, 1500);
					}),
				]);
				clearTimeout(timer);
				if (controller.signal.aborted) return;
				if (anchor) await api.jumpToAnchor(anchor, controller.signal);
				else if (highlight.page_number) {
					await api.jumpToAnchor({ page: highlight.page_number, rects: [] }, controller.signal);
				}
			} catch {
				// Navigation is best-effort for damaged/closed documents.
			} finally {
				clearTimeout(timer);
			}
		})();
		return () => { disposed = true; controller.abort(); clearTimeout(timer); };
	}, [request, api, doc]);
}
