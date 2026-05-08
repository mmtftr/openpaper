import { getDefaultSyncState, getOfflineDb } from "./db";
import { notifyOfflineSyncStateChanged } from "./events";
import type {
    AnnotationCreatePayload,
    AnnotationDeletePayload,
    AnnotationUpdatePayload,
    DocPutPayload,
    HighlightCreatePayload,
    HighlightDeletePayload,
    HighlightUpdatePayload,
    OutboxItem,
} from "./types";

function nowIso() {
    return new Date().toISOString();
}

async function refreshPendingCount() {
    const db = await getOfflineDb();
    const state = await getDefaultSyncState();
    const pendingCount = await db.count("outbox");
    await db.put("syncState", {
        ...state,
        pendingCount,
        status: pendingCount > 0 ? "pending" : "idle",
        updatedAt: nowIso(),
    });
    notifyOfflineSyncStateChanged();
}

async function putOutboxItem(
    id: string,
    type: OutboxItem["type"],
    paperId: string | undefined,
    payload: OutboxItem["payload"]
) {
    const db = await getOfflineDb();
    const existing = await db.get("outbox", id);
    const timestamp = nowIso();
    await db.put("outbox", {
        id,
        type,
        paperId,
        createdAt: existing?.createdAt || timestamp,
        updatedAt: timestamp,
        attempts: existing?.attempts || 0,
        payload,
    });
    await refreshPendingCount();
}

export async function queueDocPut(input: {
    documentId: string;
    paperId?: string | null;
    content: string;
    expectedRevision: number;
}) {
    const id = `doc-put:${input.documentId}`;
    await putOutboxItem(id, "doc-put", input.paperId || undefined, {
            documentId: input.documentId,
            content: input.content,
            expectedRevision: input.expectedRevision,
    });
}

export async function clearQueuedDocPut(documentId: string) {
    const db = await getOfflineDb();
    await db.delete("outbox", `doc-put:${documentId}`);
    await refreshPendingCount();
}

export async function getQueuedDocPut(documentId: string) {
    const db = await getOfflineDb();
    const item = await db.get("outbox", `doc-put:${documentId}`);
    return item?.type === "doc-put"
        ? { ...item, payload: item.payload as DocPutPayload }
        : null;
}

export async function queueHighlightCreate(payload: HighlightCreatePayload) {
    await putOutboxItem(
        `highlight-create:${payload.localId}`,
        "highlight-create",
        payload.paperId,
        payload
    );
}

export async function queueHighlightUpdate(payload: HighlightUpdatePayload, paperId?: string) {
    if (payload.highlightId.startsWith("local:")) {
        const db = await getOfflineDb();
        const createItem = await db.get("outbox", `highlight-create:${payload.highlightId}`);
        if (createItem?.type === "highlight-create") {
            await db.put("outbox", {
                ...createItem,
                payload: {
                    ...(createItem.payload as HighlightCreatePayload),
                    highlight: payload.highlight,
                },
                updatedAt: nowIso(),
            });
            await refreshPendingCount();
            return;
        }
    }
    await putOutboxItem(
        `highlight-update:${payload.highlightId}`,
        "highlight-update",
        paperId,
        payload
    );
}

export async function queueHighlightDelete(payload: HighlightDeletePayload, paperId?: string) {
    const db = await getOfflineDb();
    if (payload.highlightId.startsWith("local:")) {
        await db.delete("outbox", `highlight-create:${payload.highlightId}`);
        await db.delete("outbox", `highlight-update:${payload.highlightId}`);
        await refreshPendingCount();
        return;
    }
    await putOutboxItem(
        `highlight-delete:${payload.highlightId}`,
        "highlight-delete",
        paperId,
        payload
    );
}

export async function queueAnnotationCreate(payload: AnnotationCreatePayload) {
    await putOutboxItem(
        `annotation-create:${payload.localId}`,
        "annotation-create",
        payload.paperId,
        payload
    );
}

export async function queueAnnotationUpdate(payload: AnnotationUpdatePayload, paperId?: string) {
    if (payload.annotationId.startsWith("local:")) {
        const db = await getOfflineDb();
        const createItem = await db.get("outbox", `annotation-create:${payload.annotationId}`);
        if (createItem?.type === "annotation-create") {
            await db.put("outbox", {
                ...createItem,
                payload: {
                    ...(createItem.payload as AnnotationCreatePayload),
                    content: payload.content,
                },
                updatedAt: nowIso(),
            });
            await refreshPendingCount();
            return;
        }
    }
    await putOutboxItem(
        `annotation-update:${payload.annotationId}`,
        "annotation-update",
        paperId,
        payload
    );
}

export async function queueAnnotationDelete(payload: AnnotationDeletePayload, paperId?: string) {
    const db = await getOfflineDb();
    if (payload.annotationId.startsWith("local:")) {
        await db.delete("outbox", `annotation-create:${payload.annotationId}`);
        await db.delete("outbox", `annotation-update:${payload.annotationId}`);
        await refreshPendingCount();
        return;
    }
    await putOutboxItem(
        `annotation-delete:${payload.annotationId}`,
        "annotation-delete",
        paperId,
        payload
    );
}
