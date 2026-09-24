"use client";

import useSWR, { mutate } from "swr";
import { api, unwrap, type Schemas } from "@/lib/api/client";

/**
 * Turn a bibliography entry into something the citation card can show.
 *
 * The server does the work (`POST /api/references/resolve`, cached in the DB):
 * the owner's library first, then a DOI / arXiv id in the entry, then the
 * entry's URL (line-break splits repaired; `citation_*` / OpenGraph / JSON-LD
 * metadata, so blog posts resolve too), then a verified title search. Here we
 * only ask, and keep answers in the SWR cache keyed by the entry text.
 */

export type ResolvedReference = Schemas["ResolvedReference"];

/** SWR key for one entry. The server hashes a normalized form of the text. */
export const referenceKey = (text: string) => ["/api/references/resolve", text] as const;

/** Server couldn't answer yet (time budget / temporary failure). Not cached. */
export class ReferencePending extends Error {}

async function resolveOne(text: string, refresh = false): Promise<ResolvedReference> {
	const response = await unwrap(
		api.POST("/api/references/resolve", {
			params: { query: { refresh } },
			body: { entries: [{ key: "0", text }] },
		})
	);
	const found = response.results["0"];
	if (!found) throw new ReferencePending("Reference lookup unavailable");
	return found;
}

/**
 * The resolution for one entry (`null` = nothing to resolve). An error means
 * "couldn't look it up right now": SWR keeps no data, so the next hover asks
 * again.
 */
export function useResolvedReference(text: string | null) {
	return useSWR(text ? referenceKey(text) : null, ([, entry]) => resolveOne(entry), {
		revalidateIfStale: false,
		revalidateOnFocus: false,
		revalidateOnReconnect: false,
		shouldRetryOnError: false,
		dedupingInterval: 60_000,
	});
}

/** Look one entry up again, bypassing the server cache. */
export async function refreshReference(text: string): Promise<void> {
	await mutate(referenceKey(text), resolveOne(text, true), { revalidate: false });
}

const PREFETCH_CHUNK = 40;

/**
 * Warm a whole bibliography: one request per chunk, answers seeded into the
 * SWR cache so hovering is instant. Entries the server couldn't finish in
 * time are simply left for the hover to ask about.
 */
export async function prefetchReferences(texts: string[], signal?: AbortSignal): Promise<void> {
	const unique = [...new Set(texts)];
	for (let start = 0; start < unique.length; start += PREFETCH_CHUNK) {
		if (signal?.aborted) return;
		const chunk = unique.slice(start, start + PREFETCH_CHUNK);
		let response;
		try {
			response = await unwrap(
				api.POST("/api/references/resolve", {
					body: { entries: chunk.map((text, i) => ({ key: String(i), text })) },
					signal,
				})
			);
		} catch {
			return; // offline / aborted: hovers still work on their own
		}
		chunk.forEach((text, i) => {
			const found = response.results[String(i)];
			if (found) void mutate(referenceKey(text), found, { revalidate: false });
		});
	}
}
