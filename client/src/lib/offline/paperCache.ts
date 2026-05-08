import type {
    PaperData,
    PaperHighlight,
    PaperHighlightAnnotation,
} from "@/lib/schema";

import { getOfflineDb, getDefaultSyncState } from "./db";
import { notifyOfflineSyncStateChanged } from "./events";
import { getSyncPaperIds } from "./syncPolicy";
import type { SyncPaperCandidate } from "./types";

const objectUrls = new Map<string, string>();

function nowIso() {
    return new Date().toISOString();
}

async function updateSyncStateAfterCache(paperId: string, pinned?: boolean) {
    const db = await getOfflineDb();
    const state = await getDefaultSyncState();
    const syncedPaperIds = Array.from(new Set([...state.syncedPaperIds, paperId]));
    const pinnedPaperIds = pinned
        ? Array.from(new Set([...state.pinnedPaperIds, paperId]))
        : state.pinnedPaperIds;

    await db.put("syncState", {
        ...state,
        syncedPaperIds,
        pinnedPaperIds,
        lastSuccessfulSyncAt: nowIso(),
        status: state.pendingCount > 0 ? "pending" : "idle",
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
