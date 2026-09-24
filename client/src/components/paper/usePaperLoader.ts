"use client";

import { useCallback, useEffect } from "react";
import { api, unwrap } from "@/lib/api/client";
import { useStageRefreshKey } from "@/hooks/useIngest";
import {
    displayedPaperIdAtom,
    paperAtom,
    paperLoadingAtom,
    parentPaperIdAtom,
    routePaperIdAtom,
    supplementaryPaperAtom,
} from "./paperStore";
import { usePaperAtomValue, usePaperStore } from "./PaperStoreProvider";

export const getPaper = (paperId: string) =>
    unwrap(api.GET("/api/paper", { params: { query: { id: paperId } } }));

/**
 * `history.replaceState`, not `router.replace`: the App Router's replace
 * issues an RSC fetch for the new URL and falls back to a full navigation
 * when that fails (extension blocking, a network blip), which reloads the
 * page, which rewrites the URL again — an endless reload loop. These URL
 * updates are cosmetic, so the History API is enough.
 */
export function replaceUrl(nextUrl: string) {
    if (nextUrl !== `${window.location.pathname}${window.location.search}`) {
        window.history.replaceState(null, "", nextUrl);
    }
}

/**
 * Loads the paper for the route and keeps the paper atoms current:
 *
 * - a supplementary's URL becomes `/paper/<parent>?display=<supp>`, with the
 *   parent loaded for the chat / notes and the supplementary shown;
 * - the parent is re-read when ingest's metadata stages finish;
 * - a displayed supplementary's details are fetched when it changes;
 * - the `display` query param follows the displayed paper.
 */
export function usePaperLoader() {
    const store = usePaperStore();
    const routeId = usePaperAtomValue(routePaperIdAtom);
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    const displayedPaperId = usePaperAtomValue(displayedPaperIdAtom);

    useEffect(() => {
        if (!routeId) return;
        async function fetchPaper() {
            try {
                const response = await getPaper(routeId);
                const parentId = response.supplementary_of_paper_id;
                if (parentId) {
                    try {
                        const params = new URLSearchParams(window.location.search);
                        params.set("display", routeId);
                        replaceUrl(`/paper/${parentId}?${params.toString()}`);
                    } catch (err) {
                        console.error("Error rewriting supplementary URL:", err);
                    }
                    store.set(displayedPaperIdAtom, routeId);
                    store.set(parentPaperIdAtom, parentId);
                    // Already have the supplementary's details.
                    store.set(supplementaryPaperAtom, response);
                    try {
                        store.set(paperAtom, await getPaper(parentId));
                    } catch (parentErr) {
                        console.error("Error fetching parent paper:", parentErr);
                    }
                } else {
                    // The route id is the parent; an existing `display` param stands.
                    store.set(parentPaperIdAtom, routeId);
                    store.set(paperAtom, response);
                }
            } catch (error) {
                console.error("Error fetching paper:", error);
            } finally {
                store.set(paperLoadingAtom, false);
            }
        }
        fetchPaper();
    }, [routeId, store]);

    // Title / authors / DOI come from ingest's metadata stages: re-read the
    // paper when one of them finishes while the page is open.
    const metadataRefreshKey = useStageRefreshKey(parentPaperId, ["metadata", "metadata_fallback"]);
    useEffect(() => {
        if (!metadataRefreshKey || !parentPaperId) return;
        let cancelled = false;
        getPaper(parentPaperId)
            .then((response) => {
                if (!cancelled) store.set(paperAtom, response);
            })
            .catch((err) => console.error("Error refreshing paper metadata:", err));
        return () => {
            cancelled = true;
        };
    }, [metadataRefreshKey, parentPaperId, store]);

    // The displayed supplementary's details. Back on the parent, `paperAtom`
    // is the source of truth.
    useEffect(() => {
        if (!displayedPaperId) return;
        if (displayedPaperId === parentPaperId) {
            store.set(supplementaryPaperAtom, null);
            return;
        }
        if (store.get(supplementaryPaperAtom)?.id === displayedPaperId) return;
        let cancelled = false;
        getPaper(displayedPaperId)
            .then((response) => {
                if (!cancelled) store.set(supplementaryPaperAtom, response);
            })
            .catch((err) => console.error("Error fetching displayed paper:", err));
        return () => {
            cancelled = true;
        };
    }, [displayedPaperId, parentPaperId, store]);

    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        if (!displayedPaperId || displayedPaperId === parentPaperId) {
            params.delete("display");
        } else {
            params.set("display", displayedPaperId);
        }
        const qs = params.toString();
        replaceUrl(qs ? `${window.location.pathname}?${qs}` : window.location.pathname);
    }, [displayedPaperId, parentPaperId]);
}

/**
 * Re-fetch the displayed PDF's details for a fresh signed URL (the reader
 * calls this on a 403). A supplementary's URL is its own, not the parent's.
 */
export function useRefreshPdfUrl() {
    const store = usePaperStore();
    const displayedPaperId = usePaperAtomValue(displayedPaperIdAtom);
    const parentPaperId = usePaperAtomValue(parentPaperIdAtom);
    return useCallback(async (): Promise<string | null> => {
        if (!displayedPaperId) return null;
        try {
            const response = await getPaper(displayedPaperId);
            if (!response.file_url) return null;
            store.set(displayedPaperId === parentPaperId ? paperAtom : supplementaryPaperAtom, response);
            return response.file_url;
        } catch (error) {
            console.error("Error refreshing PDF URL:", error);
            return null;
        }
    }, [displayedPaperId, parentPaperId, store]);
}

/** Keeps the tab title on the paper's title, restoring the previous one on unmount. */
export function usePaperDocumentTitle() {
    const title = usePaperAtomValue(paperAtom)?.title?.trim();
    useEffect(() => {
        if (!title) return;
        const previous = document.title;
        document.title = `${title} - Open Paper`;
        return () => {
            document.title = previous;
        };
    }, [title]);
}
