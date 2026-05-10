import type {
    AnnotationCreatePayload,
    AnnotationDeletePayload,
    AnnotationSnapshot,
    AnnotationUpdatePayload,
    DocPutPayload,
    DocumentSnapshot,
    HighlightCreatePayload,
    HighlightDeletePayload,
    HighlightUpdatePayload,
    OutboxItem,
} from "./types";
import type { PaperHighlight, PaperHighlightAnnotation } from "@/lib/schema";
import { getDefaultSyncState, getOfflineDb } from "./db";
import {
    notifyOfflineAnnotationsChanged,
    notifyOfflineDocConflict,
    notifyOfflineHighlightsChanged,
    notifyOfflineSyncStateChanged,
} from "./events";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "";

function nowIso() {
    return new Date().toISOString();
}

async function setSyncStatus(
    status: "idle" | "syncing" | "pending" | "paused" | "needs-attention" | "error",
    lastError?: string,
    options: { bumpLastSuccessful?: boolean } = {}
) {
    const db = await getOfflineDb();
    const state = await getDefaultSyncState();
    const pendingCount = await db.count("outbox");
    await db.put("syncState", {
        ...state,
        status,
        pendingCount,
        lastError,
        updatedAt: nowIso(),
        lastSuccessfulSyncAt: options.bumpLastSuccessful
            ? nowIso()
            : state.lastSuccessfulSyncAt,
    });
    notifyOfflineSyncStateChanged();
}

async function apiFetch(endpoint: string, init: RequestInit = {}) {
    return fetch(`${API_BASE_URL}${endpoint}`, {
        ...init,
        credentials: "include",
        headers: {
            "Content-Type": "application/json",
            ...init.headers,
        },
    });
}

function itemError(response: Response) {
    if (response.status === 401 || response.status === 403) return "Authentication required";
    if (response.status === 409) return "Server changed this note";
    return `HTTP ${response.status}`;
}

function replayStopStatus(response: Response) {
    return response.status === 401 || response.status === 403 ? "paused" : "error";
}

async function markItemFailed(item: OutboxItem, error: string) {
    const db = await getOfflineDb();
    await db.put("outbox", {
        ...item,
        attempts: item.attempts + 1,
        lastError: error,
        updatedAt: nowIso(),
    });
}

async function replaceLocalHighlightId(
    paperId: string,
    localId: string,
    serverHighlight: PaperHighlight
) {
    const db = await getOfflineDb();
    const newId = serverHighlight.id || localId;
    const snapshot = itemSnapshot(await db.get("highlights", paperId));
    if (snapshot) {
        await db.put("highlights", {
            ...snapshot,
            highlights: snapshot.highlights.map((highlight) =>
                highlight.id === localId ? serverHighlight : highlight
            ),
            cachedAt: nowIso(),
        });
    }

    // Local annotations created against the still-pending highlight need their
    // foreign key updated in the cache too — otherwise the React state for
    // annotations would still point at `local:abc` after the page refreshes.
    const annotationSnapshot = await db.get("annotations", paperId);
    if (annotationSnapshot) {
        let touched = false;
        const updated = annotationSnapshot.annotations.map((annotation) => {
            if (annotation.highlight_id !== localId) return annotation;
            touched = true;
            return { ...annotation, highlight_id: newId };
        });
        if (touched) {
            await db.put("annotations", {
                ...annotationSnapshot,
                annotations: updated,
                cachedAt: nowIso(),
            } satisfies AnnotationSnapshot);
        }
    }

    const items = await db.getAll("outbox");
    await Promise.all(items.map(async (item) => {
        if (item.type !== "annotation-create") return;
        const payload = item.payload as AnnotationCreatePayload;
        if (payload.highlightId !== localId) return;
        await db.put("outbox", {
            ...item,
            payload: { ...payload, highlightId: newId },
            updatedAt: nowIso(),
        });
    }));

    notifyOfflineHighlightsChanged(paperId);
    notifyOfflineAnnotationsChanged(paperId);
}

function itemSnapshot<T>(value: T | undefined): T | null {
    return value || null;
}

async function replaceLocalAnnotationId(
    localId: string,
    paperId: string,
    serverAnnotation: PaperHighlightAnnotation
) {
    const db = await getOfflineDb();
    const snapshot = await db.get("annotations", paperId);
    if (!snapshot) return;
    await db.put("annotations", {
        ...snapshot,
        annotations: snapshot.annotations.map((annotation) =>
            annotation.id === localId ? serverAnnotation : annotation
        ),
        cachedAt: nowIso(),
    } satisfies AnnotationSnapshot);
    notifyOfflineAnnotationsChanged(paperId);
}

async function replayDocPut(item: OutboxItem) {
    const payload = item.payload as DocPutPayload;
    const response = await apiFetch(`/api/document/${encodeURIComponent(payload.documentId)}`, {
        method: "PUT",
        body: JSON.stringify({
            content: payload.content,
            expected_revision: payload.expectedRevision,
        }),
    });

    if (response.status === 409) {
        const db = await getOfflineDb();
        const body = await response.json().catch(() => null);
        const existing = await db.get("documents", payload.documentId);
        await db.put("documents", {
            documentId: payload.documentId,
            paperId: existing?.paperId,
            data: {
                ...(existing?.data && typeof existing.data === "object" ? existing.data : {}),
                content: payload.content,
                offlineConflict: {
                    serverRevision: body?.current_revision,
                    serverContent: body?.current_content,
                    localContent: payload.content,
                },
            },
            cachedAt: nowIso(),
        } satisfies DocumentSnapshot);
        notifyOfflineDocConflict(payload.documentId);
        throw new ReplayStopError("Server changed this note", "needs-attention");
    }

    if (response.status === 413) {
        // Don't keep retrying an oversized payload — it'll never succeed until
        // the user trims it. Surface as needs-attention so the indicator says so.
        throw new ReplayStopError("Document too large to sync", "needs-attention");
    }

    if (!response.ok) throw new ReplayStopError(itemError(response), replayStopStatus(response));

    const updated = await response.json();
    const db = await getOfflineDb();
    await db.put("documents", {
        documentId: updated.id,
        paperId: updated.paper_id,
        data: updated,
        cachedAt: nowIso(),
    });
}

async function replayHighlightCreate(item: OutboxItem) {
    const payload = item.payload as HighlightCreatePayload;
    const highlight = payload.highlight;
    const response = await apiFetch("/api/highlight", {
        method: "POST",
        body: JSON.stringify({
            paper_id: payload.paperId,
            raw_text: highlight.raw_text,
            page_number: highlight.page_number,
            position: highlight.position,
            color: highlight.color,
        }),
    });
    if (!response.ok) throw new ReplayStopError(itemError(response), replayStopStatus(response));
    const saved = await response.json();
    await replaceLocalHighlightId(payload.paperId, payload.localId, saved);
}

async function replayHighlightUpdate(item: OutboxItem) {
    const payload = item.payload as HighlightUpdatePayload;
    const response = await apiFetch(`/api/highlight/${encodeURIComponent(payload.highlightId)}`, {
        method: "PATCH",
        body: JSON.stringify({
            raw_text: payload.highlight.raw_text,
            position: payload.highlight.position,
            color: payload.highlight.color,
            start_offset: payload.highlight.start_offset,
            end_offset: payload.highlight.end_offset,
        }),
    });
    if (!response.ok) throw new ReplayStopError(itemError(response), replayStopStatus(response));
}

async function replayHighlightDelete(item: OutboxItem) {
    const payload = item.payload as HighlightDeletePayload;
    const response = await apiFetch(`/api/highlight/${encodeURIComponent(payload.highlightId)}`, {
        method: "DELETE",
    });
    if (response.status === 404) return;
    if (!response.ok) throw new ReplayStopError(itemError(response), replayStopStatus(response));
}

async function replayAnnotationCreate(item: OutboxItem) {
    const payload = item.payload as AnnotationCreatePayload;
    const response = await apiFetch("/api/annotation", {
        method: "POST",
        body: JSON.stringify({
            paper_id: payload.paperId,
            highlight_id: payload.highlightId,
            content: payload.content,
        }),
    });
    if (!response.ok) throw new ReplayStopError(itemError(response), replayStopStatus(response));
    const saved = await response.json();
    await replaceLocalAnnotationId(payload.localId, payload.paperId, saved);
}

async function replayAnnotationUpdate(item: OutboxItem) {
    const payload = item.payload as AnnotationUpdatePayload;
    const response = await apiFetch(`/api/annotation/${encodeURIComponent(payload.annotationId)}`, {
        method: "PATCH",
        body: JSON.stringify({ content: payload.content }),
    });
    if (!response.ok) throw new ReplayStopError(itemError(response), replayStopStatus(response));
}

async function replayAnnotationDelete(item: OutboxItem) {
    const payload = item.payload as AnnotationDeletePayload;
    const response = await apiFetch(`/api/annotation/${encodeURIComponent(payload.annotationId)}`, {
        method: "DELETE",
    });
    if (response.status === 404) return;
    if (!response.ok) throw new ReplayStopError(itemError(response), replayStopStatus(response));
}

class ReplayStopError extends Error {
    constructor(message: string, public status: "paused" | "needs-attention" | "error") {
        super(message);
    }
}

async function replayItem(item: OutboxItem) {
    switch (item.type) {
        case "doc-put":
            return replayDocPut(item);
        case "highlight-create":
            return replayHighlightCreate(item);
        case "highlight-update":
            return replayHighlightUpdate(item);
        case "highlight-delete":
            return replayHighlightDelete(item);
        case "annotation-create":
            return replayAnnotationCreate(item);
        case "annotation-update":
            return replayAnnotationUpdate(item);
        case "annotation-delete":
            return replayAnnotationDelete(item);
    }
}

// Single-flight guard: if a replay is already running, callers set the latch
// and the active loop runs once more after it finishes. This is what keeps
// rapid-fire highlight/annotation creates from racing the loop and producing
// duplicate POSTs against the server.
let replayInFlight = false;
let replayQueued = false;

async function runReplayOnce() {
    if (typeof navigator !== "undefined" && !navigator.onLine) {
        await setSyncStatus("paused", "Offline");
        return;
    }

    const db = await getOfflineDb();
    if ((await db.count("outbox")) === 0) {
        // Nothing to do — clear status, but don't bump lastSuccessfulSyncAt.
        // A timestamp here would mean "we just synced" when in fact we found
        // nothing to sync.
        await setSyncStatus("idle");
        return;
    }

    await setSyncStatus("syncing");
    while (true) {
        const pending = await db.getAllFromIndex("outbox", "by-created");
        const item = pending[0];
        if (!item) break;
        try {
            await replayItem(item);
            // Don't delete blindly — if the user typed (or otherwise mutated
            // the queue) during the network round-trip, the outbox record
            // now has a newer payload than what we just sent. Deleting it
            // would silently drop those keystrokes. Compare updatedAt and
            // leave any newer write for the next drain iteration.
            const current = await db.get("outbox", item.id);
            if (current && current.updatedAt === item.updatedAt) {
                await db.delete("outbox", item.id);
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : "Replay failed";
            await markItemFailed(item, message);
            if (error instanceof ReplayStopError) {
                await setSyncStatus(error.status, message);
            } else {
                await setSyncStatus("error", message);
            }
            return;
        }
    }

    const remaining = await db.count("outbox");
    await setSyncStatus(remaining > 0 ? "pending" : "idle", undefined, {
        bumpLastSuccessful: true,
    });
}

export async function replayOutbox() {
    if (replayInFlight) {
        replayQueued = true;
        return;
    }
    replayInFlight = true;
    try {
        do {
            replayQueued = false;
            await runReplayOnce();
        } while (replayQueued);
    } finally {
        replayInFlight = false;
    }
}
