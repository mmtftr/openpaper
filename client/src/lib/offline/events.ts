export const OFFLINE_SYNC_STATE_EVENT = "openpaper:offline-sync-state";
export const OFFLINE_HIGHLIGHTS_CHANGED_EVENT = "openpaper:offline-highlights-changed";
export const OFFLINE_ANNOTATIONS_CHANGED_EVENT = "openpaper:offline-annotations-changed";
export const OFFLINE_DOC_CONFLICT_EVENT = "openpaper:offline-doc-conflict";

export function notifyOfflineSyncStateChanged() {
    if (typeof window === "undefined") return;
    window.dispatchEvent(new CustomEvent(OFFLINE_SYNC_STATE_EVENT));
}

export function notifyOfflineHighlightsChanged(paperId?: string) {
    if (typeof window === "undefined") return;
    window.dispatchEvent(
        new CustomEvent(OFFLINE_HIGHLIGHTS_CHANGED_EVENT, { detail: { paperId } })
    );
}

export function notifyOfflineAnnotationsChanged(paperId?: string) {
    if (typeof window === "undefined") return;
    window.dispatchEvent(
        new CustomEvent(OFFLINE_ANNOTATIONS_CHANGED_EVENT, { detail: { paperId } })
    );
}

export function notifyOfflineDocConflict(documentId: string) {
    if (typeof window === "undefined") return;
    window.dispatchEvent(
        new CustomEvent(OFFLINE_DOC_CONFLICT_EVENT, { detail: { documentId } })
    );
}
