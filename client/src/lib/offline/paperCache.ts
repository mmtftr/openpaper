import type {
    PaperData,
    PaperHighlight,
    PaperHighlightAnnotation,
    PaperItem,
} from "@/lib/schema";

import { fetchFromApi } from "@/lib/api";

import { getOfflineDb, getDefaultSyncState } from "./db";
import { notifyOfflineSyncStateChanged } from "./events";
import { getSyncPaperIds } from "./syncPolicy";
import type { SyncPaperCandidate } from "./types";

// localStorage-backed cache for the paper list (the response of
// `/api/paper/all` and friends). Stored here rather than IDB so we can read
// it synchronously from the SWR fallback and avoid a schema bump. Small
// payload — a couple hundred KB at worst.
const LIST_CACHE_PREFIX = "openpaper:paperList:";

function listCacheKey(url: string) {
    return `${LIST_CACHE_PREFIX}${url}`;
}

export function readCachedPaperList(url: string): PaperItem[] | null {
    if (typeof window === "undefined") return null;
    try {
        const raw = window.localStorage.getItem(listCacheKey(url));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed?.papers) ? (parsed.papers as PaperItem[]) : null;
    } catch {
        return null;
    }
}

export function writeCachedPaperList(url: string, papers: PaperItem[]) {
    if (typeof window === "undefined") return;
    try {
        window.localStorage.setItem(
            listCacheKey(url),
            JSON.stringify({ papers, cachedAt: new Date().toISOString() })
        );
    } catch {
        // Quota or disabled localStorage — non-fatal.
    }
}

const objectUrls = new Map<string, string>();

function nowIso() {
    return new Date().toISOString();
}

// Statuses owned by an active sync/replay loop — caching individual items
// while one is running shouldn't clobber that status.
const PRESERVED_STATUSES = new Set([
    "syncing",
    "paused",
    "needs-attention",
    "error",
]);

async function updateSyncStateAfterCache(paperId: string, pinned?: boolean) {
    const db = await getOfflineDb();
    const state = await getDefaultSyncState();
    const syncedPaperIds = Array.from(new Set([...state.syncedPaperIds, paperId]));
    const pinnedPaperIds = pinned
        ? Array.from(new Set([...state.pinnedPaperIds, paperId]))
        : state.pinnedPaperIds;
    const status = PRESERVED_STATUSES.has(state.status)
        ? state.status
        : state.pendingCount > 0
            ? "pending"
            : "idle";

    await db.put("syncState", {
        ...state,
        syncedPaperIds,
        pinnedPaperIds,
        lastSuccessfulSyncAt: nowIso(),
        status,
        updatedAt: nowIso(),
    });
    notifyOfflineSyncStateChanged();
}

export async function cachePaperMetadata(
    paperId: string,
    data: PaperData,
    options: { pinned?: boolean } = {}
) {
    const db = await getOfflineDb();
    const existing = await db.get("papers", paperId);
    const pinned = options.pinned || existing?.pinned || false;
    await db.put("papers", {
        paperId,
        data,
        pinned,
        cachedAt: nowIso(),
        lastOpenedAt: nowIso(),
    });
    await updateSyncStateAfterCache(paperId, pinned);
}

export async function getCachedPaperMetadata(paperId: string) {
    const db = await getOfflineDb();
    return db.get("papers", paperId);
}

export async function cacheHighlights(paperId: string, highlights: PaperHighlight[]) {
    const db = await getOfflineDb();
    await db.put("highlights", { paperId, highlights, cachedAt: nowIso() });
}

export async function getCachedHighlights(paperId: string) {
    const db = await getOfflineDb();
    return db.get("highlights", paperId);
}

export async function cacheAnnotations(
    paperId: string,
    annotations: PaperHighlightAnnotation[]
) {
    const db = await getOfflineDb();
    await db.put("annotations", { paperId, annotations, cachedAt: nowIso() });
}

export async function getCachedAnnotations(paperId: string) {
    const db = await getOfflineDb();
    return db.get("annotations", paperId);
}

export async function cacheDocumentList(paperId: string, documents: unknown[]) {
    const db = await getOfflineDb();
    await db.put("documentLists", { paperId, documents, cachedAt: nowIso() });
}

export async function getCachedDocumentList(paperId: string) {
    const db = await getOfflineDb();
    return db.get("documentLists", paperId);
}

export async function cacheDocument<T extends { paper_id?: string | null }>(
    documentId: string,
    data: T
) {
    const db = await getOfflineDb();
    await db.put("documents", {
        documentId,
        paperId: data.paper_id,
        data,
        cachedAt: nowIso(),
    });
}

export async function getCachedDocument(documentId: string) {
    const db = await getOfflineDb();
    return db.get("documents", documentId);
}

export async function cachePdfBlob(
    paperId: string,
    sourceUrl: string,
    options: { pinned?: boolean } = {}
) {
    const sourceOrigin = new URL(sourceUrl, window.location.href).origin;
    const response = await fetch(sourceUrl, {
        credentials: sourceOrigin === window.location.origin ? "include" : "omit",
    });
    if (!response.ok) {
        throw new Error(`PDF cache failed: HTTP ${response.status}`);
    }

    const blob = await response.blob();
    const db = await getOfflineDb();
    await db.put("pdfMeta", {
        paperId,
        blob,
        sourceUrl,
        bytes: blob.size,
        cachedAt: nowIso(),
    });
    await updateSyncStateAfterCache(paperId, options.pinned);
}

export async function getCachedPdfObjectUrl(paperId: string) {
    const cached = await (await getOfflineDb()).get("pdfMeta", paperId);
    if (!cached) return null;

    const existing = objectUrls.get(paperId);
    if (existing) return existing;

    const url = URL.createObjectURL(cached.blob);
    objectUrls.set(paperId, url);
    return url;
}

export async function pinPaperForOffline(paperId: string) {
    const db = await getOfflineDb();
    const paper = await db.get("papers", paperId);
    if (paper) {
        await db.put("papers", { ...paper, pinned: true, cachedAt: nowIso() });
    }
    await updateSyncStateAfterCache(paperId, true);
}

export async function refreshSyncScope(papers: SyncPaperCandidate[]) {
    const db = await getOfflineDb();
    const state = await getDefaultSyncState();
    const { paperIds, scope } = getSyncPaperIds(papers, state.pinnedPaperIds);
    await db.put("syncState", {
        ...state,
        scope,
        syncedPaperIds: paperIds,
        updatedAt: nowIso(),
    });
    notifyOfflineSyncStateChanged();
}

export async function getPdfCacheBytes() {
    const db = await getOfflineDb();
    const entries = await db.getAll("pdfMeta");
    return entries.reduce((total, entry) => total + entry.bytes, 0);
}

// ---- Pre-warming ----------------------------------------------------------
//
// "Sync now" should leave the user able to read every paper in the sync set
// offline — metadata, PDF, doc list, doc bodies, highlights, annotations.
// Without this, opening one paper online only caches that paper, and the
// indicator's "Latest 50 synced" claim is a lie.

interface PaperListResponse {
    papers?: SyncPaperCandidate[];
}

interface DocumentSummary {
    id: string;
    [k: string]: unknown;
}

async function prewarmPaper(paperId: string): Promise<void> {
    // Metadata first — without it the rest is meaningless.
    const paper = await fetchFromApi(`/api/paper?id=${paperId}`);
    await cachePaperMetadata(paperId, paper as PaperData);

    // PDF blob: skip if we already have one. Re-downloading a 50MB PDF on
    // every "Sync now" click would be both wasteful and likely to blow IDB
    // quotas on Safari.
    const pdfData = paper as PaperData;
    if (pdfData.file_url) {
        const db = await getOfflineDb();
        const existingMeta = await db.get("pdfMeta", paperId);
        if (!existingMeta) {
            try {
                await cachePdfBlob(paperId, pdfData.file_url);
            } catch (err) {
                // Don't abort the rest of the paper just because the PDF
                // failed (could be quota, CORS, transient). Notes still
                // sync, the user can pin manually if it matters.
                console.warn(`Prewarm: PDF cache failed for ${paperId}:`, err);
            }
        }
    }

    // Document list
    let docList: DocumentSummary[] = [];
    try {
        const list = await fetchFromApi(
            `/api/document?paper_id=${encodeURIComponent(paperId)}`
        );
        docList = (Array.isArray(list) ? list : []) as DocumentSummary[];
        await cacheDocumentList(paperId, docList);
    } catch (err) {
        console.warn(`Prewarm: doc list failed for ${paperId}:`, err);
    }

    // Each doc's full content. Run in parallel within a paper — typically a
    // small number (1 main + a few notes), and they're tiny markdown payloads.
    await Promise.all(
        docList.map(async (summary) => {
            try {
                const fullDoc = await fetchFromApi(
                    `/api/document/${encodeURIComponent(summary.id)}`
                );
                await cacheDocument(fullDoc.id, fullDoc);
            } catch (err) {
                console.warn(`Prewarm: doc ${summary.id} failed:`, err);
            }
        })
    );

    // Highlights + annotations: independent, can run in parallel.
    await Promise.all([
        (async () => {
            try {
                const highlights = (await fetchFromApi(
                    `/api/highlight/${encodeURIComponent(paperId)}`
                )) as PaperHighlight[];
                if (Array.isArray(highlights)) {
                    await cacheHighlights(paperId, highlights);
                }
            } catch (err) {
                console.warn(`Prewarm: highlights failed for ${paperId}:`, err);
            }
        })(),
        (async () => {
            try {
                const annotations = (await fetchFromApi(
                    `/api/annotation/${encodeURIComponent(paperId)}`
                )) as PaperHighlightAnnotation[];
                if (Array.isArray(annotations)) {
                    await cacheAnnotations(paperId, annotations);
                }
            } catch (err) {
                console.warn(`Prewarm: annotations failed for ${paperId}:`, err);
            }
        })(),
    ]);
}

async function setPrewarmStatus(
    status: "syncing" | "idle" | "pending" | "error",
    progress?: { completed: number; total: number },
    lastError?: string
) {
    const db = await getOfflineDb();
    const state = await getDefaultSyncState();
    await db.put("syncState", {
        ...state,
        status,
        prewarmProgress: progress,
        lastError,
        updatedAt: nowIso(),
        lastSuccessfulSyncAt:
            status === "idle" || status === "pending"
                ? nowIso()
                : state.lastSuccessfulSyncAt,
    });
    notifyOfflineSyncStateChanged();
}

let prewarmInFlight = false;

export async function prewarmAllSyncedPapers(): Promise<void> {
    if (prewarmInFlight) return;
    if (typeof navigator !== "undefined" && !navigator.onLine) return;

    prewarmInFlight = true;
    try {
        await setPrewarmStatus("syncing", { completed: 0, total: 0 });

        let papers: SyncPaperCandidate[];
        try {
            const response = (await fetchFromApi("/api/paper/all")) as
                | SyncPaperCandidate[]
                | PaperListResponse;
            papers = Array.isArray(response) ? response : response.papers || [];
            // Refresh the offline-readable list at the same key SWR uses, so
            // the library page renders the up-to-date set after Sync now.
            writeCachedPaperList("/api/paper/all", papers as unknown as PaperItem[]);
        } catch (err) {
            await setPrewarmStatus(
                "error",
                undefined,
                err instanceof Error ? err.message : "Could not fetch paper list"
            );
            return;
        }

        const state = await getDefaultSyncState();
        const { paperIds, scope } = getSyncPaperIds(papers, state.pinnedPaperIds);

        // Update scope and target list up front so the indicator reflects what
        // we're trying to sync, not the previous run's set.
        const db = await getOfflineDb();
        await db.put("syncState", {
            ...state,
            scope,
            syncedPaperIds: paperIds,
            status: "syncing",
            prewarmProgress: { completed: 0, total: paperIds.length },
            updatedAt: nowIso(),
        });
        notifyOfflineSyncStateChanged();

        // Bounded concurrency: 3 papers in flight. Higher saturates the
        // browser's concurrent-fetch limit and starves PDF downloads; lower
        // wastes time waiting on metadata round-trips.
        const concurrency = 3;
        const queue = [...paperIds];
        let completed = 0;

        const workers = Array.from(
            { length: Math.min(concurrency, queue.length) },
            async () => {
                while (queue.length) {
                    const paperId = queue.shift();
                    if (!paperId) break;
                    try {
                        await prewarmPaper(paperId);
                    } catch (err) {
                        console.warn(`Prewarm failed for ${paperId}:`, err);
                    }
                    completed++;
                    await setPrewarmStatus("syncing", {
                        completed,
                        total: paperIds.length,
                    });
                }
            }
        );
        await Promise.all(workers);

        // Clear progress and let the regular outbox/replay drive next status.
        const finalState = await getDefaultSyncState();
        await db.put("syncState", {
            ...finalState,
            status: finalState.pendingCount > 0 ? "pending" : "idle",
            prewarmProgress: undefined,
            lastSuccessfulSyncAt: nowIso(),
            updatedAt: nowIso(),
        });
        notifyOfflineSyncStateChanged();
    } finally {
        prewarmInFlight = false;
    }
}
